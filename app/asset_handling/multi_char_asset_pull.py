import json
import logging
import os

from app.esi_service.auth_service import AuthService
from app.esi_service.base_interfaces import ESIRequest
from app.esi_service.esi_settings import ESI_BASE_URL
from app.esi_service.oauth_config import CLIENT_ID, REDIRECT_URI
from app.esi_service.real_esi_client import RealESIClient
from app.asset_handling.enrich_assets import enrich_assets_with_custom_names
from app.asset_handling.clone_pull import pull_clones_for_character, token_scopes
from app.asset_handling.corp_pull import load_corporations, pull_corporations
from app.asset_handling.esi_cache_times import cached_until, record_expires
from app.asset_handling.mutated_items import MutatedItems
from app.loaders.sde_rules import SdeRules
from app.paths import EVE_DB_PATH, RAW_DIR
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("MultiCharAssetPull")

async def pull_assets_for_character(esi_client: RealESIClient, char_id: str, output_dir: str, auth_service: Any) -> bool:
    """
    Pulls all assets for a single character with full pagination and enriches them.
    """
    asset_endpoint = f"/characters/{char_id}/assets/"
    all_assets = []
    total_pages = 1
    current_page = 1
    first_modified = None       # every page must come from the same ESI copy (RC2 §1, G4)
    expires = None
    restarted = False

    logger.info(f"[{char_id}] Starting asset pull from {asset_endpoint}")

    try:
        while current_page <= total_pages:
            params = {"page": current_page}

            request = ESIRequest(
                url=asset_endpoint,
                method="GET",
                params=params
            )

            response = await esi_client.request(request)

            if response.status_code != 200:
                logger.error(f"[{char_id}] Failed to fetch assets. Status: {response.status_code}")
                logger.error(f"[{char_id}] Response data: {response.data}")
                return False

            # If ESI refreshed its copy part way through, pages can repeat or miss items:
            # start again once from page 1.
            modified = response.headers.get("last-modified")
            if current_page == 1:
                first_modified, expires = modified, response.headers.get("expires")
            elif modified and first_modified and modified != first_modified:
                if restarted:
                    logger.error(f"[{char_id}] ESI's copy of the assets changed during the pull twice; try again later.")
                    return False
                logger.warning(f"[{char_id}] ESI's copy of the assets changed during the pull (page {current_page}); "
                               "starting again from page 1.")
                restarted = True
                all_assets, total_pages, current_page = [], 1, 1
                continue

            assets_page = response.data
            if not assets_page:
                logger.debug(f"[{char_id}] No more assets found on page {current_page}.")
                break

            all_assets.extend(assets_page)
            logger.debug(f"[{char_id}] Retrieved {len(assets_page)} assets (Total: {len(all_assets)})")

            # ESI Pagination: check for 'x-pages' to determine total pages
            try:
                x_pages_header = response.headers.get("x-pages")
                if x_pages_header:
                    total_pages = int(x_pages_header)
                    logger.debug(f"[{char_id}] Detected {total_pages} total pages from x-pages header.")
                else:
                    logger.debug(f"[{char_id}] No 'x-pages' header found. Treating as single page.")
            except ValueError:
                logger.error(f"[{char_id}] Failed to parse 'x-pages' header: {response.headers.get('x-pages')}")

            current_page += 1

        # Save to character-specific file
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, f"{char_id}.json")
        
        with open(output_path, "w") as f:
            json.dump(all_assets, f, indent=4)
        record_expires(char_id, expires)
        
        logger.info(f"[{char_id}] Asset pull completed. Saved {len(all_assets)} assets.")

        # --- ENRICHMENT STEP ---
        logger.info(f"[{char_id}] Starting enrichment...")
        import shutil
        temp_dir = os.path.join(output_dir, f"temp_{char_id}")
        os.makedirs(temp_dir, exist_ok=True)
        temp_file = os.path.join(temp_dir, f"{char_id}.json")
        
        shutil.move(output_path, temp_file)
        
        enrich_success = await enrich_assets_with_custom_names(
            temp_dir,
            output_path,
            esi_client,
            auth_service
        )
        
        if enrich_success:
            logger.info(f"[{char_id}] Enrichment successful. Saved to {output_path}")
            shutil.rmtree(temp_dir)
        else:
            logger.error(f"[{char_id}] Enrichment failed.")
            return False

        return True

    except Exception as e:
        logger.exception(f"[{char_id}] An error occurred during asset pull: {e}")
        return False

async def refresh_mutated_items(esi_client, raw_dir: str) -> int:
    """Looks up the base of every mutated item in the pulled assets that isn't known yet."""
    if not EVE_DB_PATH.exists():
        return 0
    assets = []
    for path in Path(raw_dir).glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, list):
            assets.extend(a for a in data if isinstance(a, dict))
    for corp in load_corporations():
        assets.extend(a for a in corp.get("assets", []) if isinstance(a, dict))
    rules = SdeRules(EVE_DB_PATH)
    try:
        return await MutatedItems().refresh(esi_client, assets, rules.is_mutated, log=logger.info)
    finally:
        rules.close()


async def run_multi_char_pull(log_callback: Callable[[str], None] = None, auth_service: Any = None) -> bool:
    """
    Entry point for the multi-character asset pull.
    If log_callback is provided, it will be used for logging. auth_service is the
    app's shared AuthService (F7); without one, the pull makes its own.
    Returns True if successful, False otherwise.
    """
    # Configure logging if callback is provided; removed again when the pull ends, or
    # each pull in a session would add another handler and repeat every line.
    handler = None
    if log_callback:
        class CallbackHandler(logging.Handler):
            def emit(self, record):
                log_callback(self.format(record))
        
        handler = CallbackHandler()
        handler.setFormatter(logging.Formatter('%(levelname)s: %(message)s'))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)

    try:
        return await _pull_all_characters(auth_service)
    finally:
        if handler is not None:
            logger.removeHandler(handler)


async def _pull_all_characters(auth_service: Any) -> bool:
    """Pulls every logged-in character's assets (run_multi_char_pull's work)."""
    # 1. Setup Auth
    if auth_service is None:
        logger.debug("Initializing AuthService...")
        auth_service = AuthService(CLIENT_ID, REDIRECT_URI)
    
    # Get all character IDs from profiles
    char_ids = list(auth_service.profiles.keys())
    
    if not char_ids:
        logger.error("No authenticated characters found in data/auth/.")
        return False

    logger.info(f"Found {len(char_ids)} characters to process: {char_ids}")

    # 2. Setup ESI Client
    esi_client = RealESIClient(base_url=ESI_BASE_URL, auth_service=auth_service)

    # 3. Iterate and Pull
    output_dir = str(RAW_DIR)
    results = []

    for char_id in char_ids:
        logger.info(f"--- Processing Character: {char_id} ---")
        until = cached_until(char_id)
        if until:
            name = auth_service.index.get(char_id, char_id)
            logger.warning(f"ESI's copy of {name}'s assets is cached until {until.astimezone():%H:%M}; "
                           "this pull will return the same assets as the last one.")

        original_active_id = auth_service.active_character_id
        auth_service.active_character_id = char_id
        
        success = await pull_assets_for_character(esi_client, char_id, output_dir, auth_service)
        results.append((char_id, success))
        # Clones and implants: saved apart from the assets; a failure here doesn't fail the pull.
        try:
            await pull_clones_for_character(esi_client, char_id, auth_service.index.get(char_id, ""),
                                            scopes=token_scopes(auth_service.get_access_token()))
        except Exception as e:
            logger.warning(f"[{char_id}] Clones and implants weren't pulled: {e}")
        
        # Restore original active ID
        auth_service.active_character_id = original_active_id

    # Corporation hangars, from each corporation's lowest-ID linked Director (Phase 13).
    try:
        await pull_corporations(esi_client, auth_service)
    except Exception as e:
        logger.warning(f"Corp hangars weren't pulled: {e}")

    # Mutated modules: each new one's base module, looked up once (TRACKED_ITEMS_PLAN.md 12.1).
    try:
        await refresh_mutated_items(esi_client, output_dir)
    except Exception as e:
        logger.warning(f"Mutated module lookups failed: {e}")

    await esi_client.close()

    # 4. Summary
    logger.info("========================================")
    logger.info("MULTI-CHARACTER ASSET PULL SUMMARY")
    logger.info("========================================")
    all_success = True
    for char_id, success in results:
        status = "SUCCESS" if success else "FAILED"
        if not success:
            all_success = False
        logger.info(f"Character {char_id}: {status}")
    logger.info("========================================")
    
    return all_success
