"""
Losses: recent killmails, pulled with the assets when Options ▸ ESI Features ▸ Losses is on
(ESI features plan 30.1, 30.5; D7.2).

    GET /characters/{id}/killmails/recent           the last 90 days' kills and losses    esi-killmails.read_killmails.v1
    GET /corporations/{id}/killmails/recent         a corporation's (Directors only)      esi-killmails.read_corporation_killmails.v1
    GET /killmails/{killmail_id}/{killmail_hash}    one killmail in full                  public
    GET /insurance/prices                           every hull's insurance levels         public

The recent list is one call a character (its route allows 30 a quarter hour: plenty for an hourly
pull). Each killmail is fetched once and kept in data/killmails/<id>.json; only losses matter here,
but the list doesn't say which they are until a killmail is read. A failure never fails the pull.
"""
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from app import paths
from app.asset_handling.clone_pull import token_scopes
from app.esi_service.base_interfaces import ESIRequest

logger = logging.getLogger("MultiCharAssetPull")       # the pull's logger, so lines reach the GUI log

CHARACTER_SCOPE = "esi-killmails.read_killmails.v1"
CORPORATION_SCOPE = "esi-killmails.read_corporation_killmails.v1"
INSURANCE_FILE = "insurance.json"


def killmails_dir(folder: Optional[Path] = None) -> Path:
    return Path(folder or paths.KILLMAILS_DIR)


async def _fetch_new(esi_client, listed: List[dict], folder: Path) -> List[int]:
    new = []
    for entry in listed:
        kid = int(entry["killmail_id"])
        path = folder / f"{kid}.json"
        if path.exists():
            continue
        r = await esi_client.request(ESIRequest(url=f"/killmails/{kid}/{entry['killmail_hash']}"))
        if r.status_code == 200 and isinstance(r.data, dict):
            folder.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(r.data), encoding="utf-8")
            new.append(kid)
    return new


async def pull_killmails_for_character(esi_client, char_id: str, name: str = "", scopes: Optional[set] = None,
                                       folder: Optional[Path] = None) -> List[int]:
    """The character's recent killmails not read before. Returns their IDs."""
    who = name or char_id
    if scopes is not None and CHARACTER_SCOPE not in scopes:
        logger.info(f"{who}'s login doesn't include killmails; log in again (Characters ▸ Add Character) for Losses.")
        return []
    r = await esi_client.request(ESIRequest(url=f"/characters/{char_id}/killmails/recent"))
    if r.status_code != 200 or not isinstance(r.data, list):
        logger.warning(f"Couldn't read {who}'s recent killmails (HTTP {r.status_code}).")
        return []
    return await _fetch_new(esi_client, r.data, killmails_dir(folder))


async def pull_corporation_killmails(esi_client, auth: Any, pullers: Dict[int, str],
                                     folder: Optional[Path] = None) -> List[int]:
    """Each corporation's recent killmails, read by its Director (D7.6): corp ships' losses."""
    new: List[int] = []
    original = auth.active_character_id
    try:
        for corp, char_id in sorted(pullers.items()):
            auth.active_character_id = char_id
            if CORPORATION_SCOPE not in token_scopes(auth.get_access_token()):
                continue
            r = await esi_client.request(ESIRequest(url=f"/corporations/{corp}/killmails/recent"))
            if r.status_code == 200 and isinstance(r.data, list):
                new += await _fetch_new(esi_client, r.data, killmails_dir(folder))
            else:
                logger.warning(f"Couldn't read corporation {corp}'s killmails (HTTP {r.status_code}).")
    finally:
        auth.active_character_id = original
    return new


async def pull_insurance(esi_client, folder: Optional[Path] = None) -> bool:
    """Every hull's insurance levels (the insurance estimate, D7.6)."""
    r = await esi_client.request(ESIRequest(url="/insurance/prices"))
    if r.status_code != 200 or not isinstance(r.data, list):
        return False
    folder = killmails_dir(folder)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / INSURANCE_FILE).write_text(json.dumps({str(p["type_id"]): p.get("levels", []) for p in r.data}),
                                         encoding="utf-8")
    return True


def load_killmails(folder: Optional[Path] = None) -> Dict[int, dict]:
    folder = killmails_dir(folder)
    out = {}
    for path in folder.glob("*.json") if folder.exists() else []:
        if not path.stem.isdigit():
            continue
        try:
            out[int(path.stem)] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
    return out


def insurance_payout(type_id: int, folder: Optional[Path] = None, level: str = "Platinum") -> Optional[float]:
    try:
        prices = json.loads((killmails_dir(folder) / INSURANCE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    for entry in prices.get(str(int(type_id)), []):
        if entry.get("name") == level:
            return float(entry.get("payout") or 0) or None
    return None
