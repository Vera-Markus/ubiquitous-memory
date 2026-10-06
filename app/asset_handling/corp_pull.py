"""
Corporation hangars, pulled with the assets (TRACKED_ITEMS_PLAN.md Phase 13; design §8.1, D8).

Only a Director can read a corporation's assets (anything less gets 403; probe of
2026-10-06). For each corporation a linked character belongs to, the puller is the
character with the highest permission (a Director), then the lowest character ID.

    GET  /characters/{id}/                          corporation (public)
    GET  /characters/{id}/roles/                    esi-characters.read_corporation_roles.v1
    GET  /corporations/{id}/                        name, ticker (public)
    GET  /corporations/{id}/assets/?page=n          esi-assets.read_corporation_assets.v1 (Director)
    GET  /corporations/{id}/divisions/              esi-corporations.read_divisions.v1 (Director)
    POST /corporations/{id}/assets/names            ships and containers only (one bad ID fails the batch)

Each corporation goes to data/corp/<corporation_id>.json, apart from data/raw/ (the
aggregation of personal assets reads every file there). A login without the corp
scopes is skipped without asking ESI. A corporation failure never fails the pull.
"""
import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.asset_handling.clone_pull import token_scopes
from app.asset_handling.esi_cache_times import cached_until, record_expires
from app.esi_service.base_interfaces import ESIRequest
from app.paths import CORP_DIR, EVE_DB_PATH

logger = logging.getLogger("MultiCharAssetPull")       # the pull's logger, so lines reach the GUI log

ROLES_SCOPE = "esi-characters.read_corporation_roles.v1"
PULL_SCOPES = ("esi-assets.read_corporation_assets.v1", "esi-corporations.read_divisions.v1")
DIRECTOR = "Director"
SHIP_CATEGORY = 6
CONTAINER_GROUPS = (12, 340, 448, 649)      # Cargo, Secure Cargo, Audit Log Secure, Freight Container


def cache_key(corporation_id: int) -> str:
    """The esi_cache_times key for a corporation's assets (characters use their ID)."""
    return f"corp:{corporation_id}"


def permission(roles: dict) -> int:
    """How much of the corporation's hangars a character can pull: 1 for a Director, else 0."""
    return 1 if DIRECTOR in (roles.get("roles") or []) else 0


async def corp_pullers(esi_client, auth: Any) -> Tuple[Dict[int, str], set, bool]:
    """
    ({corporation_id: character_id who pulls it}, every corporation seen, complete). complete is False when a
    character's corporation or roles couldn't be read, so callers don't take a missing
    corporation as "nobody can pull it". The client's active character is restored.
    """
    candidates: Dict[int, List[Tuple[int, int]]] = {}     # corp -> [(-permission, character_id)]
    complete = True
    original = auth.active_character_id
    try:
        for char_id in sorted(auth.profiles, key=int):
            auth.active_character_id = char_id
            name = auth.index.get(char_id, char_id)
            if ROLES_SCOPE not in token_scopes(auth.get_access_token()):
                logger.info(f"{name}'s login doesn't include corporation roles; "
                            "log in again (Characters ▸ Add Character) to include corp hangars.")
                continue
            info = await esi_client.request(ESIRequest(url=f"/characters/{char_id}/"))
            roles = await esi_client.request(ESIRequest(url=f"/characters/{char_id}/roles/"))
            if info.status_code != 200 or roles.status_code != 200:
                complete = False
                logger.warning(f"Couldn't read {name}'s corporation or roles "
                               f"(HTTP {info.status_code}/{roles.status_code}); corp hangars may be incomplete.")
                continue
            corp = int(info.data["corporation_id"])
            level = permission(roles.data or {})
            if level and set(PULL_SCOPES) <= token_scopes(auth.get_access_token()):
                candidates.setdefault(corp, []).append((-level, int(char_id)))
            else:
                candidates.setdefault(corp, [])
    finally:
        auth.active_character_id = original
    return {corp: str(min(c)[1]) for corp, c in candidates.items() if c}, set(candidates), complete


def _nameable_types() -> set:
    """Type IDs that can carry a player-given name: ships and containers."""
    if not Path(EVE_DB_PATH).exists():
        return set()
    with sqlite3.connect(f"file:{Path(EVE_DB_PATH).as_posix()}?mode=ro", uri=True) as db:
        rows = db.execute("SELECT t.typeID FROM invTypes t JOIN invGroups g USING(groupID) "
                          f"WHERE g.categoryID = ? OR t.groupID IN ({','.join('?' * len(CONTAINER_GROUPS))})",
                          (SHIP_CATEGORY, *CONTAINER_GROUPS))
        return {r[0] for r in rows}


async def _all_pages(esi_client, url: str, who: str) -> Tuple[Optional[list], dict]:
    """Every page, all from one ESI copy (last-modified checked; restart once). (items or None, headers of page 1)."""
    restarted = False
    while True:
        items, first, page, pages, changed = [], {}, 1, 1, False
        while page <= pages:
            r = await esi_client.request(ESIRequest(url=url, params={"page": page}))
            if r.status_code != 200:
                logger.warning(f"{who}: corp assets HTTP {r.status_code} on page {page} ({r.data}).")
                return None, {}
            if page == 1:
                first = r.headers
            elif r.headers.get("last-modified") and r.headers.get("last-modified") != first.get("last-modified"):
                changed = True
                break
            items.extend(r.data or [])
            try:
                pages = int(r.headers.get("x-pages") or 1)
            except ValueError:
                pages = 1
            page += 1
        if not changed:
            return items, first
        if restarted:
            logger.warning(f"{who}: ESI's copy changed during the pull twice; try again later.")
            return None, {}
        logger.warning(f"{who}: ESI's copy changed during the pull; starting again from page 1.")
        restarted = True


async def pull_corporation(esi_client, corporation_id: int, puller: str, puller_name: str = "",
                           corp_dir: Optional[Path] = None, nameable: Optional[Callable[[int], bool]] = None) -> bool:
    """Pulls one corporation's assets, divisions and names into corp_dir/<id>.json. True on success."""
    info = await esi_client.request(ESIRequest(url=f"/corporations/{corporation_id}/"))
    corp_name = (info.data or {}).get("name") if info.status_code == 200 else None
    who = corp_name or f"Corporation {corporation_id}"
    until = cached_until(cache_key(corporation_id))
    if until:
        logger.warning(f"ESI's copy of {who}'s assets is cached until {until.astimezone():%H:%M}; "
                       "this pull will return the same assets as the last one.")

    assets, headers = await _all_pages(esi_client, f"/corporations/{corporation_id}/assets/", who)
    if assets is None:
        return False

    divisions: Dict[str, str] = {}
    r = await esi_client.request(ESIRequest(url=f"/corporations/{corporation_id}/divisions/"))
    if r.status_code == 200:
        for d in (r.data or {}).get("hangar", []):
            divisions[str(d["division"])] = d.get("name") or f"Division {d['division']}"
    else:
        logger.warning(f"{who}: division names HTTP {r.status_code}; hangars are shown by number.")

    is_nameable = nameable or _nameable_types().__contains__
    ids = sorted(a["item_id"] for a in assets if a.get("is_singleton") and is_nameable(a["type_id"]))
    names: Dict[str, str] = {}
    for start in range(0, len(ids), 1000):
        r = await esi_client.request(ESIRequest(url=f"/corporations/{corporation_id}/assets/names",
                                                method="POST", json=ids[start:start + 1000]))
        if r.status_code != 200:
            logger.warning(f"{who}: asset names HTTP {r.status_code}; ships and containers keep their type names.")
            break
        names.update({str(n["item_id"]): n["name"] for n in (r.data or []) if (n.get("name") or "").strip()})

    record = {
        "corporation_id": int(corporation_id), "name": corp_name,
        "ticker": (info.data or {}).get("ticker") if info.status_code == 200 else None,
        "pulled_by": int(puller), "pulled_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "expires": headers.get("expires"), "last_modified": headers.get("last-modified"),
        "divisions": divisions, "names": names, "assets": assets,
    }
    folder = Path(corp_dir or CORP_DIR)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{corporation_id}.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(record), encoding="utf-8")
    tmp.replace(path)
    record_expires(cache_key(corporation_id), headers.get("expires"))
    logger.info(f"{who}: {len(assets)} corp item(s) saved (pulled by {puller_name or puller}).")
    return True


async def pull_corporations(esi_client, auth: Any, corp_dir: Optional[Path] = None) -> Dict[int, bool]:
    """Pulls every corporation a linked Director can read. Returns {corporation_id: success}."""
    folder = Path(corp_dir or CORP_DIR)
    pullers, seen, complete = await corp_pullers(esi_client, auth)
    for corp in sorted(seen - set(pullers)):
        info = await esi_client.request(ESIRequest(url=f"/corporations/{corp}/"))
        name = (info.data or {}).get("name") if info.status_code == 200 else f"Corporation {corp}"
        logger.info(f"{name}: no linked Director, so its corp hangars aren't pulled.")
    results: Dict[int, bool] = {}
    original = auth.active_character_id
    try:
        for corp, char_id in sorted(pullers.items()):
            auth.active_character_id = char_id
            try:
                results[corp] = await pull_corporation(esi_client, corp, char_id, auth.index.get(char_id, ""), folder)
            except Exception as e:
                logger.warning(f"Corporation {corp} wasn't pulled: {e}")
                results[corp] = False
    finally:
        auth.active_character_id = original
    # A corporation no linked Director can pull any more: its old file would only mislead.
    if complete and folder.exists():
        for path in folder.glob("*.json"):
            if path.stem.isdigit() and int(path.stem) not in pullers:
                path.unlink()
                logger.info(f"Corporation {path.stem}: no linked Director any more; its saved hangars were removed.")
    return results


def load_corporations(corp_dir: Optional[Path] = None) -> List[dict]:
    """Every saved corporation pull."""
    folder = Path(corp_dir or CORP_DIR)
    out = []
    for path in sorted(folder.glob("*.json")) if folder.exists() else []:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            out.append(data)
    return out
