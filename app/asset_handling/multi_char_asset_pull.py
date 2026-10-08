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
from app.asset_handling.contract_pull import pull_contracts
from app.asset_handling.killmail_pull import pull_corporation_killmails, pull_insurance, pull_killmails_for_character
from app.asset_handling.skill_pull import pull_skills_for_character
from app.services import esi_features
from app.asset_handling.corp_pull import load_corporations, pull_corporations
from app.asset_handling.esi_cache_times import cached_until, record_expires
from app.asset_handling.mutated_items import MutatedItems
from app.loaders.sde_rules import SdeRules
from app.paths import EVE_DB_PATH, RAW_DIR
from app.services.pull_sequence import CharOutcome, Hooks, PullSequence, SequenceResult
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("MultiCharAssetPull")

async def pull_assets_for_character(esi_client: RealESIClient, char_id: str, output_dir: str,
                                    auth_service: Any) -> CharOutcome:
    """
    Pulls all assets for a single character with full pagination and enriches them.
    The outcome is falsy on failure and carries the HTTP status that ended it, so the
    pull sequence can tell a server problem from a login problem (plan 25.4).
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
                return CharOutcome(False, response.status_code, f"ESI error {response.status_code}")

            # If ESI refreshed its copy part way through, pages can repeat or miss items:
            # start again once from page 1.
            modified = response.headers.get("last-modified")
            if current_page == 1:
                first_modified, expires = modified, response.headers.get("expires")
            elif modified and first_modified and modified != first_modified:
                if restarted:
                    logger.error(f"[{char_id}] ESI's copy of the assets changed during the pull twice; try again later.")
                    return CharOutcome(False, None, "ESI's copy of the assets kept changing")
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
            return CharOutcome(False, None, "naming the assets failed")

        return CharOutcome(True)

    except Exception as e:
        logger.exception(f"[{char_id}] An error occurred during asset pull: {e}")
        return CharOutcome(False, None, str(e))

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


async def run_multi_char_pull(log_callback: Callable[[str], None] = None, auth_service: Any = None,
                              hooks: Hooks = None) -> SequenceResult:
    """
    Entry point for the multi-character asset pull.
    If log_callback is provided, it will be used for logging. auth_service is the
    app's shared AuthService (F7); without one, the pull makes its own. hooks drive
    the safe-mode sequence (plan 25.4); without them a failing character is skipped.
    Returns which characters were pulled, which were dropped and why, and whether the
    sequence stopped or was held.
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
        return await _pull_all_characters(auth_service, hooks)
    finally:
        if handler is not None:
            logger.removeHandler(handler)


async def _pull_all_characters(auth_service: Any, hooks: Hooks = None) -> SequenceResult:
    """Pulls every logged-in character's assets (run_multi_char_pull's work)."""
    # 1. Setup Auth
    if auth_service is None:
        logger.debug("Initializing AuthService...")
        auth_service = AuthService(CLIENT_ID, REDIRECT_URI)
    
    # Get all character IDs from profiles
    char_ids = list(auth_service.profiles.keys())
    
    if not char_ids:
        logger.error("No authenticated characters found in data/auth/.")
        return SequenceResult()

    logger.info(f"Found {len(char_ids)} characters to process: {char_ids}")

    # 2. Setup ESI Client
    esi_client = RealESIClient(base_url=ESI_BASE_URL, auth_service=auth_service)

    # 3. Pull each character in turn: the safe-mode sequence decides what a failure does (plan 25.4).
    output_dir = str(RAW_DIR)

    async def pull_one(char_id: str) -> CharOutcome:
        logger.info(f"--- Processing Character: {char_id} ---")
        until = cached_until(char_id)
        if until:
            name = auth_service.index.get(char_id, char_id)
            logger.warning(f"ESI's copy of {name}'s assets is cached until {until.astimezone():%H:%M}; "
                           "this pull will return the same assets as the last one.")

        original_active_id = auth_service.active_character_id
        auth_service.active_character_id = char_id
        try:
            outcome = await pull_assets_for_character(esi_client, char_id, output_dir, auth_service)
            if outcome:
                # Clones and implants: saved apart from the assets; a failure here doesn't fail the pull.
                try:
                    await pull_clones_for_character(esi_client, char_id, auth_service.index.get(char_id, ""),
                                                    scopes=token_scopes(auth_service.get_access_token()))
                except Exception as e:
                    logger.warning(f"[{char_id}] Clones and implants weren't pulled: {e}")
                # Skills, for the skill check (plan 26.3): the same, when the feature is on.
                if esi_features.enabled("skills"):
                    try:
                        await pull_skills_for_character(esi_client, char_id, auth_service.index.get(char_id, ""),
                                                        scopes=token_scopes(auth_service.get_access_token()))
                    except Exception as e:
                        logger.warning(f"[{char_id}] Skills weren't pulled: {e}")
                # Losses (plan 30.1): the character's recent killmails, each new one read once.
                if esi_features.enabled("losses"):
                    try:
                        await pull_killmails_for_character(esi_client, char_id, auth_service.index.get(char_id, ""),
                                                           scopes=token_scopes(auth_service.get_access_token()))
                    except Exception as e:
                        logger.warning(f"[{char_id}] Killmails weren't read: {e}")
            return outcome
        finally:
            auth_service.active_character_id = original_active_id

    if hooks is not None:
        hooks.name = lambda char: auth_service.index.get(char, char)      # names in the status line
    sequence = await PullSequence(pull_one, hooks).run(char_ids)

    if not (sequence.held or sequence.stopped):
        # Corporation hangars, from each corporation's lowest-ID linked Director (Phase 13).
        try:
            await pull_corporations(esi_client, auth_service)
        except Exception as e:
            logger.warning(f"Corp hangars weren't pulled: {e}")

        # Losses (plan 30.5): corporation killmails from each corporation's Director; insurance prices.
        if esi_features.enabled("losses_corporation"):
            try:
                from app.asset_handling.corp_pull import corp_pullers
                pullers, _, _ = await corp_pullers(esi_client, auth_service)
                await pull_corporation_killmails(esi_client, auth_service, pullers)
            except Exception as e:
                logger.warning(f"Corporation killmails weren't read: {e}")
        if esi_features.enabled("losses_insurance"):
            try:
                await pull_insurance(esi_client)
            except Exception as e:
                logger.warning(f"Insurance prices weren't read: {e}")

        # Contracts (ESI features plan 28.2, 28.3): your own, and your corporations' (alliance ones too).
        if esi_features.enabled("contracts"):
            try:
                await pull_contracts(esi_client, auth_service)
            except Exception as e:
                logger.warning(f"Contracts weren't pulled: {e}")

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
    for char_id in char_ids:
        name = auth_service.index.get(char_id, char_id)
        if char_id in sequence.succeeded:
            logger.info(f"Character {name}: SUCCESS")
        elif char_id in sequence.dropped:
            logger.info(f"Character {name}: SKIPPED ({sequence.dropped[char_id]})")
        else:
            logger.info(f"Character {name}: NOT PULLED")
    if sequence.held:
        logger.info("The pull is on hold until Tranquility is back.")
    elif sequence.stopped:
        logger.info("The pull was stopped.")
    logger.info("========================================")

    return sequence
