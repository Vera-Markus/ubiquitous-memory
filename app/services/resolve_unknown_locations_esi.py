"""
Names player structures in the location cache through ESI
(GET /universe/structures/{id}, scope esi-universe.read_structures.v1).

ESI only answers for structures the character can dock at, and characters
don't all have the same access. So each structure is tried with every
logged-in character in turn until one succeeds: first the characters with
assets in it, oldest first (character IDs are handed out in sequence, so a
lower ID is an older character), then everyone else, oldest first.

Entries looked up: those still "Unknown", and names entered by hand (an ESI
name replaces a manual one). Whatever no character can see stays as it is and
is tried again on the next pull; the GUI then offers to name it by hand.
"""
import asyncio
import json
import logging
from pathlib import Path
from typing import Awaitable, Callable, Dict, Iterable, List, Optional, Set

from app import paths
from app.esi_service.base_interfaces import ESIRequest
from app.esi_service.real_esi_client import RealESIClient

logger = logging.getLogger("StructureResolver")

ESI_STRUCTURE_ENDPOINT = "universe/structures"
BATCH_SIZE = 10


async def fetch_structure_info(esi_client: RealESIClient, structure_id: int,
                               log: Callable[[str], None] = logger.debug) -> tuple[dict | None, int, str]:
    """Fetches structure info from ESI. Returns (data or None, status code, body text)."""
    url = f"{ESI_STRUCTURE_ENDPOINT}/{structure_id}"
    try:
        response = await esi_client.request(ESIRequest(url=url, method="GET"))
        body_text = str(response.data)
        log(f"[ESI Resolver] {structure_id}: status {response.status_code}")
        if response.status_code == 200:
            return response.data, 200, body_text
        return None, response.status_code, body_text
    except Exception as e:
        log(f"[ESI Resolver] Exception for {structure_id}: {e}")
        return None, 0, str(e)


def needs_lookup(entry: dict) -> bool:
    """Still "Unknown", or named by hand (ESI's name replaces a manual one)."""
    return entry.get("name") == "Unknown" or bool(entry.get("manual"))


def lookup_order(structure_id: int, holders: Dict[int, Set[str]], characters: Iterable[str]) -> List[Optional[str]]:
    """
    The characters to try for one structure: those with assets in it, then the rest,
    each group oldest (lowest character ID) first.
    """
    characters = sorted({str(c) for c in characters}, key=int)
    if not characters:
        return [None]               # no character to choose: use the client as it is
    holding = {str(c) for c in holders.get(structure_id, ())}
    return [c for c in characters if c in holding] + [c for c in characters if c not in holding]


async def resolve_unknowns(esi_client: RealESIClient, cache_path: Optional[Path] = None,
                           log: Callable[[str], None] = logger.info,
                           holders: Optional[Dict[int, Set[str]]] = None,
                           characters: Iterable[str] = (),
                           use_character: Optional[Callable[[str], Awaitable[None]]] = None) -> int:
    """
    Looks up every structure that needs it (see needs_lookup) and saves the names ESI
    returns. holders maps a structure to the characters with assets in it; characters
    are every logged-in character, and use_character(character_id) points the client at
    one of them. Without characters, one pass is made with the client as it is.
    Returns the number of structures resolved.
    """
    cache_path = Path(cache_path or paths.GENERATED_DIR / "location_cache.json")
    if not cache_path.exists():
        log(f"[ESI Resolver] Location cache not found at {cache_path}")
        return 0

    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            cache_data = json.load(f)
    except (OSError, ValueError) as e:
        log(f"[ESI Resolver] Could not read the location cache: {e}")
        return 0

    pending = [int(loc_id) for loc_id, entry in cache_data.items() if needs_lookup(entry)]
    if not pending:
        log("[ESI Resolver] No unknown structures to look up.")
        return 0

    holders = holders or {}
    characters = list(characters)
    orders = {sid: lookup_order(sid, holders, characters) for sid in pending}
    log(f"[ESI Resolver] Looking up {len(pending)} structure(s) through ESI "
        f"with up to {len(orders[pending[0]])} character(s) each...")

    resolved: Set[int] = set()
    for round_index in range(max(len(order) for order in orders.values())):
        # This round, every unresolved structure tries its next character. One character
        # at a time, because the client sends the active character's token.
        by_character: Dict[Optional[str], List[int]] = {}
        for sid in pending:
            if sid not in resolved and round_index < len(orders[sid]):
                by_character.setdefault(orders[sid][round_index], []).append(sid)
        for character_id in sorted(by_character, key=lambda c: int(c) if c else 0):
            if character_id is not None and use_character is not None:
                await use_character(character_id)
            batch_ids = by_character[character_id]
            for i in range(0, len(batch_ids), BATCH_SIZE):
                batch = batch_ids[i:i + BATCH_SIZE]
                results = await asyncio.gather(*(fetch_structure_info(esi_client, sid) for sid in batch))
                for sid, (data, status_code, _body) in zip(batch, results):
                    if not data:
                        continue
                    position = data.get("position", {})
                    cache_data[str(sid)] = {
                        "name": data.get("name", "Unknown"),
                        "owner_id": data.get("owner_id", 0),
                        "position": {"x": position.get("x", 0), "y": position.get("y", 0), "z": position.get("z", 0)},
                        "solar_system_id": data.get("solar_system_id", 0),
                        "type_id": data.get("type_id", 0),
                    }
                    resolved.add(sid)
                    who = f" (as character {character_id})" if character_id else ""
                    log(f"[ESI Resolver] Resolved {sid} -> {cache_data[str(sid)]['name']}{who}")

    for sid in pending:
        if sid not in resolved:
            kept = " (keeping the name entered by hand)" if cache_data[str(sid)].get("manual") else ""
            log(f"[ESI Resolver] No character could look up {sid}{kept}.")

    try:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(cache_data, f, indent=4)
    except OSError as e:
        log(f"[ESI Resolver] Could not save the location cache: {e}")
        return 0

    log(f"[ESI Resolver] Done: {len(resolved)} of {len(pending)} resolved.")
    return len(resolved)
