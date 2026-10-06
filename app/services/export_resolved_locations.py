"""
Builds data/generated/location_cache.json: a name, solar system and type for
every location in the aggregated assets that isn't itself an item.

NPC stations and solar systems come from the SDE. Any other location (a player
structure) is recorded as "Unknown" and then looked up through ESI by
resolve_unknown_locations_esi, trying each logged-in character in turn. The
asset pull pipeline runs this after every pull.

A structure no character can see can be named by hand (save_manual_location):
its entry is marked "manual" and is still looked up on later pulls, where an ESI
name replaces it. Skipping that prompt marks it "prompt_skipped" so the GUI
doesn't ask again after every pull (Tools ▸ Name Unknown Structures still can).
"""
import json
import logging
import sqlite3
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from app import paths
from app.esi_service.real_esi_client import RealESIClient
from app.services import resolve_unknown_locations_esi

logger = logging.getLogger("LocationCache")

# Internal flags to identify "junk" locations (sub-locations inside containers/ships)
INTERNAL_FLAGS = {
    "MedSlot1", "MedSlot2", "MedSlot3", "MedSlot4",
    "Cargo", "DroneBay", "AutoFit", "Silo", "FuelBay",
    "Inventory", "ShipSlot", "Hold"
}

UNKNOWN_ENTRY = {
    "name": "Unknown",
    "owner_id": 0,
    "position": {"x": 0, "y": 0, "z": 0},
    "solar_system_id": 0,
    "type_id": 0,
}


def get_locations_from_db(db_path: Path) -> Optional[Dict[str, dict]]:
    """Solar systems (type 10000) and NPC stations (type 30000) from the SDE, keyed by str(ID)."""
    if not Path(db_path).exists():
        logger.error(f"Database not found at {db_path}")
        return None

    resolved_map: Dict[str, dict] = {}
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute("SELECT solarSystemID, solarSystemName, x, y, z FROM mapSolarSystems")
        for sys_id, sys_name, x, y, z in cur.fetchall():
            resolved_map[str(sys_id)] = {
                "name": sys_name,
                "owner_id": 0,
                "position": {"x": x or 0, "y": y or 0, "z": z or 0},
                "solar_system_id": 0,
                "type_id": 10000,
            }
        cur.execute("SELECT stationID, stationName, solarSystemID, x, y, z FROM staStations")
        for sta_id, sta_name, sys_id, x, y, z in cur.fetchall():
            resolved_map[str(sta_id)] = {
                "name": sta_name,
                "owner_id": 0,
                "position": {"x": x or 0, "y": y or 0, "z": z or 0},
                "solar_system_id": sys_id or 0,
                "type_id": 30000,
            }
    finally:
        conn.close()
    return resolved_map


def build_location_cache(
    assets_path: Optional[Path] = None,
    db_path: Optional[Path] = None,
    cache_path: Optional[Path] = None,
    log: Callable[[str], None] = logger.info,
) -> Optional[dict]:
    """
    Updates the location cache from the aggregated assets and the SDE, without
    calling ESI. Entries already resolved (for example structures named on an
    earlier run) are kept. Returns a summary, or None if an input is missing.
    """
    assets_path = Path(assets_path or paths.GENERATED_DIR / "all_assets.json")
    db_path = Path(db_path or paths.EVE_DB_PATH)
    cache_path = Path(cache_path or paths.GENERATED_DIR / "location_cache.json")

    db_locations = get_locations_from_db(db_path)
    if db_locations is None:
        return None
    if not assets_path.exists():
        log(f"[ERROR] Assets file not found at {assets_path}")
        return None

    with open(assets_path, "r", encoding="utf-8") as f:
        assets = json.load(f)

    # location_id -> location_flags seen with it, and the characters with assets there
    asset_location_map: Dict[int, set] = {}
    holders = location_holders(assets)
    all_item_ids = set()
    for asset in assets:
        loc_id = asset.get("location_id")
        item_id = asset.get("item_id")
        if item_id is not None:
            all_item_ids.add(item_id)
        if loc_id is None:
            continue
        asset_location_map.setdefault(loc_id, set())
        if asset.get("location_flag"):
            asset_location_map[loc_id].add(asset["location_flag"])

    item_ids, known_ids, unknown_ids = [], [], []
    for loc_id, flags in asset_location_map.items():
        is_junk = flags and all(f in INTERNAL_FLAGS for f in flags)
        if is_junk or loc_id in all_item_ids:
            item_ids.append(loc_id)          # a ship, container or internal sub-location
        elif str(loc_id) in db_locations:
            known_ids.append(loc_id)
        else:
            unknown_ids.append(loc_id)

    cache: Dict[str, dict] = {}
    if cache_path.exists():
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                cache = {str(k): v for k, v in json.load(f).items()}
        except ValueError:
            log(f"[WARNING] Could not parse {cache_path}. Starting a new location cache.")

    for loc_id in known_ids:
        cache[str(loc_id)] = db_locations[str(loc_id)]
    for loc_id in unknown_ids:
        key = str(loc_id)
        if key not in cache:        # an existing entry keeps its name and its manual / prompt_skipped marks
            cache[key] = dict(UNKNOWN_ENTRY, position=dict(UNKNOWN_ENTRY["position"]))

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=4)

    still_unknown = [loc_id for loc_id in unknown_ids if cache[str(loc_id)].get("name") == "Unknown"]
    log(f"[INFO] Location cache: {len(known_ids)} NPC locations, "
        f"{len(unknown_ids) - len(still_unknown)} structures already named, "
        f"{len(still_unknown)} still unknown.")
    return {
        "total": len(asset_location_map),
        "items": len(item_ids),
        "known": len(known_ids),
        "unknown_ids": still_unknown,
        "entries": len(cache),
        "holders": {loc_id: holders.get(loc_id, set()) for loc_id in unknown_ids},
    }


async def update_location_cache(
    esi_client: Optional[RealESIClient] = None,
    assets_path: Optional[Path] = None,
    db_path: Optional[Path] = None,
    cache_path: Optional[Path] = None,
    log: Callable[[str], None] = logger.info,
    auth_service: Any = None,
) -> Optional[dict]:
    """
    Builds the cache, then names structures through ESI when a client is given. With an
    AuthService, each structure is tried with every logged-in character in turn.
    """
    summary = build_location_cache(assets_path, db_path, cache_path, log)
    if summary is None:
        return None
    summary["resolved"] = 0
    if esi_client is not None:      # also re-checks names entered by hand
        summary["resolved"] = await resolve_unknown_locations_esi.resolve_unknowns(
            esi_client, cache_path=cache_path, log=log, holders=summary["holders"],
            characters=list(auth_service.profiles) if auth_service is not None else (),
            use_character=auth_service.switch_character if auth_service is not None else None,
        )
    return summary


# --- who holds assets where ---------------------------------------------------------

def location_holders(assets: List[dict]) -> Dict[int, Set[str]]:
    """location_id -> the character IDs (as strings) with an item directly in it."""
    holders: Dict[int, Set[str]] = {}
    for asset in assets:
        if asset.get("location_id") is not None and asset.get("character_id") is not None:
            holders.setdefault(asset["location_id"], set()).add(str(asset["character_id"]))
    return holders


# --- naming structures by hand ------------------------------------------------------

def _read_cache(cache_path: Path) -> Dict[str, dict]:
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _write_cache(cache_path: Path, cache: Dict[str, dict]) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=4)


def structures_to_name(include_skipped: bool = False, cache_path: Optional[Path] = None,
                       assets_path: Optional[Path] = None) -> List[Tuple[int, List[str]]]:
    """
    Structures in the current assets that no character could look up, with the IDs of
    the characters holding assets there (oldest first). Ones the user skipped are left
    out unless include_skipped.
    """
    cache_path = Path(cache_path or paths.GENERATED_DIR / "location_cache.json")
    assets_path = Path(assets_path or paths.GENERATED_DIR / "all_assets.json")
    cache = _read_cache(cache_path)
    try:
        with open(assets_path, "r", encoding="utf-8") as f:
            holders = location_holders(json.load(f))
    except (OSError, ValueError):
        return []
    found = []
    for key, entry in cache.items():
        location_id = int(key)
        if (entry.get("name") == "Unknown" and location_id in holders
                and (include_skipped or not entry.get("prompt_skipped"))):
            found.append((location_id, sorted(holders[location_id], key=int)))
    return sorted(found)


def save_manual_location(structure_id: int, name: str, solar_system_id: int,
                         cache_path: Optional[Path] = None) -> None:
    """Names a structure by hand. It's still looked up on later pulls; an ESI name replaces this one."""
    cache_path = Path(cache_path or paths.GENERATED_DIR / "location_cache.json")
    cache = _read_cache(cache_path)
    cache[str(structure_id)] = dict(UNKNOWN_ENTRY, position=dict(UNKNOWN_ENTRY["position"]),
                                    name=name.strip(), solar_system_id=int(solar_system_id), manual=True)
    _write_cache(cache_path, cache)


def skip_location_prompt(structure_id: int, cache_path: Optional[Path] = None) -> None:
    """Don't ask after every pull for this structure (it's still looked up through ESI)."""
    cache_path = Path(cache_path or paths.GENERATED_DIR / "location_cache.json")
    cache = _read_cache(cache_path)
    if str(structure_id) in cache:
        cache[str(structure_id)]["prompt_skipped"] = True
        _write_cache(cache_path, cache)

