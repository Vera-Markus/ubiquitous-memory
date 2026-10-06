"""
Each character's clones and implants, pulled with the assets (RC2_PLAN.md §3, implants in doctrines).

    GET /characters/{id}/implants/   the active clone's implants (type IDs)   esi-clones.read_implants.v1
    GET /characters/{id}/clones/     home station, jump clones and theirs     esi-clones.read_clones.v1

Saved as ESI sends it, one file per character in data/clones/<id>.json, kept apart
from data/raw/ (the asset aggregation reads every file there). Names and slots are
added when the data is used, from eve.db. Nothing reads these files yet.

A login made before the clone scopes were added is skipped without asking ESI (a refused
call counts against ESI's error limit): the pull says to log in again and carries on.
"""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import jwt

from app.esi_service.base_interfaces import ESIRequest
from app.paths import CLONES_DIR

logger = logging.getLogger("MultiCharAssetPull")       # the pull's logger, so lines reach the GUI log

OK, NO_SCOPE, FAILED = "ok", "no scope", "failed"
CLONE_SCOPES = ("esi-clones.read_implants.v1", "esi-clones.read_clones.v1")


def token_scopes(access_token: Optional[str]) -> set:
    """The scopes a login's access token carries (read without verifying: only to decide what to ask ESI)."""
    if not access_token:
        return set()
    try:
        scp = jwt.decode(access_token, options={"verify_signature": False}).get("scp", [])
    except jwt.PyJWTError:
        return set()
    return {scp} if isinstance(scp, str) else set(scp)


def _missing_scope(response) -> bool:
    return response.status_code in (401, 403) and "scope" in str(response.data)


async def pull_clones_for_character(esi_client, char_id: str, name: str = "",
                                    clones_dir: Optional[Path] = None, scopes: Optional[set] = None) -> str:
    """
    Pulls and saves one character's implants and clones. Returns OK, NO_SCOPE or FAILED.
    scopes: the login's scopes, when known; without both clone scopes ESI isn't asked.
    """
    who = name or char_id
    if scopes is not None and not set(CLONE_SCOPES) <= scopes:
        logger.warning(f"{who}'s login doesn't include clones and implants yet; "
                       "log in again (Characters ▸ Add Character) to include them.")
        return NO_SCOPE
    implants = await esi_client.request(ESIRequest(url=f"/characters/{char_id}/implants/"))
    clones = await esi_client.request(ESIRequest(url=f"/characters/{char_id}/clones/"))

    if _missing_scope(implants) or _missing_scope(clones):
        logger.warning(f"{who}'s login doesn't include clones and implants yet; "
                       "log in again (Characters ▸ Add Character) to include them.")
        return NO_SCOPE
    if implants.status_code != 200 or clones.status_code != 200:
        logger.warning(f"Couldn't pull {who}'s clones and implants "
                       f"(HTTP {implants.status_code}/{clones.status_code}); the assets are unaffected.")
        return FAILED

    data = clones.data or {}
    record = {
        "character_id": int(char_id),
        "pulled_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "expires": {"implants": implants.headers.get("expires"), "clones": clones.headers.get("expires")},
        "active_implants": list(implants.data or []),
        "home_location": data.get("home_location"),
        "last_clone_jump_date": data.get("last_clone_jump_date"),
        "last_station_change_date": data.get("last_station_change_date"),
        "jump_clones": data.get("jump_clones", []),
    }
    folder = Path(clones_dir or CLONES_DIR)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{char_id}.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(record, indent=2), encoding="utf-8")
    tmp.replace(path)
    logger.info(f"{who}: {len(record['active_implants'])} active implant(s), "
                f"{len(record['jump_clones'])} jump clone(s) saved.")
    return OK
