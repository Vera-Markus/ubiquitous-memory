"""
The ship a character is sitting in (GET /characters/{id}/ship and /location, scopes
esi-location.read_ship_type.v1 and esi-location.read_location.v1).

ESI's asset list leaves out the ship a character is in, though it lists what's fitted to it.
A Titan or Supercarrier that never docks, or a cyno alt logged off in space, would never be
seen. After each character's asset pull this adds that ship to their saved assets, where it
is: in space (the solar system, as ESI lists a ship in space), at an NPC station, or in a
structure. Pods are left out. A login without both scopes is skipped quietly ("log in
again" is for features you turn on; this one has no switch).
"""
import json
import logging
from pathlib import Path
from typing import Iterable, Optional, Set

from app.esi_service.base_interfaces import ESIRequest
from app.esi_service import scopes as scope_ledger

logger = logging.getLogger("ActiveShip")

CAPSULES = {670, 33328}          # Capsule, Capsule - Genolution 'Auroral' 197-variant


def active_ship_row(ship: dict, location: dict, character_id: int) -> Optional[dict]:
    """The asset row for the ship, as ESI would list it, or None for a pod or a reply missing a part."""
    item_id, type_id = ship.get("ship_item_id"), ship.get("ship_type_id")
    if not item_id or not type_id or int(type_id) in CAPSULES:
        return None
    if location.get("station_id"):
        where, kind = location["station_id"], "station"
    elif location.get("structure_id"):
        where, kind = location["structure_id"], "item"
    elif location.get("solar_system_id"):
        where, kind = location["solar_system_id"], "solar_system"
    else:
        return None
    return {"item_id": int(item_id), "type_id": int(type_id), "location_id": int(where), "location_type": kind,
            "location_flag": "Hangar", "quantity": 1, "is_singleton": True, "character_id": int(character_id),
            "custom_name": ship.get("ship_name") or "None", "active_ship": True}


async def add_active_ship(esi_client, char_id: str, raw_path: Path, scopes: Iterable[str],
                          corporation_items: Iterable[int] = ()) -> Optional[dict]:
    """
    Adds the character's current ship to their saved assets (raw_path) unless it's a pod, already
    listed, or a corporation's ship (corporation_items: its hangars from the last pull). Returns the
    row added, or None.
    """
    if scope_ledger.missing("active_ship", scopes):
        return None
    ship = await esi_client.request(ESIRequest(url=f"/characters/{char_id}/ship/"))
    if ship.status_code != 200 or not isinstance(ship.data, dict):
        logger.warning(f"[{char_id}] The current ship wasn't read: ESI {ship.status_code}")
        return None
    if int(ship.data.get("ship_type_id") or 0) in CAPSULES:
        return None
    location = await esi_client.request(ESIRequest(url=f"/characters/{char_id}/location/"))
    if location.status_code != 200 or not isinstance(location.data, dict):
        logger.warning(f"[{char_id}] The current location wasn't read: ESI {location.status_code}")
        return None
    row = active_ship_row(ship.data, location.data, int(char_id))
    if row is None:
        return None
    assets = json.loads(Path(raw_path).read_text(encoding="utf-8"))
    listed: Set[int] = {a.get("item_id") for a in assets} | set(corporation_items)
    if row["item_id"] in listed:
        return None
    assets.append(row)
    Path(raw_path).write_text(json.dumps(assets, indent=4), encoding="utf-8")
    logger.info(f"[{char_id}] Added the ship the character is in: type {row['type_id']} at {row['location_id']}.")
    return row
