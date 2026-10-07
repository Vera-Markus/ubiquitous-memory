"""
Ships shared out among one system's requirements by where they're parked (homes and
priorities plan, step 23.3: H3, H9, H10).

For one character, one fitting and one system S, the ships whose Home is S are shared out
among the character's requirements for that fitting in S:

1. a ship parked at a required station serves that station (HOME);
2. a system-wide requirement (any station) takes any ship in S (HOME);
3. each requirement still without a ship, first station alphabetically first, takes a ship
   elsewhere in S (IN_SYSTEM: move it), else one away from S (DEPLOYED: bring it back),
   else one found nowhere (MISSING);
4. ships left over are still listed: under the station they're parked at, else under the
   first requirement, so every ship with Home S shows somewhere.

A requirement a wider hard one covers (P4, requirement_priority.covering) shares the wider
one's ships instead of taking its own: it lists every ship, placed against its own station.
A requirement with no ship left fails hard (a purchase, H9); one with only IN_SYSTEM or
DEPLOYED ships fails soft (a move).
"""
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence

HOME, IN_SYSTEM, DEPLOYED, MISSING = "HOME", "IN_SYSTEM", "DEPLOYED", "MISSING"
# Taken in this order for a requirement still without a ship (step 3).
PREFERENCE = (IN_SYSTEM, DEPLOYED, MISSING)


@dataclass
class Placed:
    designation: Dict[str, Any]
    sighting: Optional[Any]             # universe.Sighting, or None when the ship is found nowhere
    placement: str


def place(sighting: Optional[Any], system_id: int, location_id: Optional[int]) -> str:
    """Where a ship with Home in system_id is, against a requirement at location_id (None: any station)."""
    if sighting is None:
        return MISSING
    if sighting.system_id != system_id:
        return DEPLOYED
    if location_id is None or sighting.root_location_id == location_id:
        return HOME
    return IN_SYSTEM


def share_out(system_id: int, requirements: Sequence[Dict[str, Any]], ships: Sequence[tuple],
              covered: Callable[[Dict[str, Any]], bool],
              station_name: Callable[[Dict[str, Any]], str]) -> Dict[int, List[Placed]]:
    """
    requirements: the character's requirements for one fitting in system_id (dicts with
    req_uid and location_id). ships: (designation, sighting or None) for each ship of that
    fitting whose Home is system_id. Returns req_uid -> the ships listed under it.
    """
    out: Dict[int, List[Placed]] = {r["req_uid"]: [] for r in requirements}
    own = sorted((r for r in requirements if not covered(r)),
                 key=lambda r: (r.get("location_id") is None, station_name(r).casefold(), r["req_uid"]))
    pool = list(ships)
    required = {r.get("location_id") for r in own if r.get("location_id") is not None}

    def take(requirement, wanted: str) -> bool:
        # A ship parked at a required station is moved last: one parked where nothing wants it goes first.
        order = sorted(range(len(pool)), key=lambda i: getattr(pool[i][1], "root_location_id", None) in required)
        for i in order:
            d, sighting = pool[i]
            if place(sighting, system_id, requirement.get("location_id")) == wanted:
                out[requirement["req_uid"]].append(Placed(d, sighting, wanted))
                del pool[i]
                return True
        return False

    for requirement in own:                     # 1 and 2: parked where it's wanted
        take(requirement, HOME)
    for requirement in own:                     # 3: the rest, first station alphabetically first
        if not out[requirement["req_uid"]]:
            any(take(requirement, wanted) for wanted in PREFERENCE)
    for d, sighting in pool:                    # 4: every other ship still shows
        at = next((r for r in own if place(sighting, system_id, r.get("location_id")) == HOME), None)
        target = at or (own[0] if own else None)
        if target is not None:
            out[target["req_uid"]].append(Placed(d, sighting, place(sighting, system_id, target.get("location_id"))))
    for requirement in requirements:
        if covered(requirement):
            out[requirement["req_uid"]] = [Placed(d, s, place(s, system_id, requirement.get("location_id")))
                                           for d, s in ships]
    return out
