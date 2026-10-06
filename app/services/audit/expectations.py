"""
What a fitting expects, by location (design §9.2).

Locations are the fitting's sections: the slot categories ("high", "mid",
"low", "rigs", "subsystem"), "drones", "fighters" and "cargo", plus the
doctrine metadata bays that hold items:

- fleet_hangar requirements are added to "cargo": the two are one pooled
  space (D2), and requirements add up (a cloak in the EFT cargo and one in
  the fleet hangar metadata means two cloaks);
- fuel_bay requirements are their own location, "fuel_bay".

Ships in the ship maintenance bay and escape bay aren't items aboard; they're
checked in bays.py.
"""
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

SLOT_LOCATIONS = ("subsystem", "high", "mid", "low", "rigs")
FIT_LOCATIONS = SLOT_LOCATIONS + ("drones", "fighters", "cargo")
METADATA_ITEM_BAYS = {"fleet_hangar": "cargo", "fuel_bay": "fuel_bay"}     # bay key -> expected location
# Fighters pre-loaded in tubes (plan 20.3): their own location once a fitting says how many
# squadrons go in them; until then tubes and bay are one pooled "fighters" location.
FIGHTER_TUBES = "fighter_tubes"
EXPECTED_LOCATIONS = FIT_LOCATIONS + ("fuel_bay", FIGHTER_TUBES)


@dataclass(frozen=True)
class ExpectedItem:
    type_id: int
    name: str
    quantity: int


Expectations = Dict[str, Dict[int, ExpectedItem]]


def _add(expectations: Expectations, location: str, type_id: int, name: str, quantity: int) -> None:
    if quantity <= 0:
        return
    items = expectations.setdefault(location, {})
    previous = items.get(type_id)
    items[type_id] = ExpectedItem(type_id, (previous.name if previous else None) or name or str(type_id),
                                  quantity + (previous.quantity if previous else 0))


def tube_squadrons(fitting: Dict[str, Any]) -> Dict[int, int]:
    """{fighter type: squadrons pre-loaded in tubes} from a fitting record's metadata (plan 20.3)."""
    tubes = (fitting.get("doctrine_metadata") or {}).get("tubes") or {} if isinstance(fitting, dict) else {}
    return {int(t): int(n) for t, n in tubes.items() if str(t).isdigit() and int(n or 0) > 0}


def build_expectations(fitting: Dict[str, Any], squadron_size: Optional[Callable[[int], int]] = None) -> Expectations:
    """
    {location: {type_id: ExpectedItem}} from a fitting record (or a bare fit dict).
    With squadron_size (fighters per full squadron) and pre-loaded tubes in the metadata,
    the fit's fighters are split: full squadrons in "fighter_tubes", the rest in "fighters".
    """
    if not isinstance(fitting, dict):
        return {}
    fit = fitting.get("fit", fitting)
    expectations: Expectations = {}
    for location in FIT_LOCATIONS:
        section = fit.get(location)
        if not isinstance(section, dict):
            continue
        for type_id_str, info in section.items():
            if str(type_id_str).isdigit():
                _add(expectations, location, int(type_id_str), info.get("name"), int(info.get("quantity", 1) or 0))

    tubes = tube_squadrons(fitting) if fit is not fitting and squadron_size is not None else {}
    if tubes:
        bay = expectations.pop("fighters", {})
        for type_id, item in bay.items():
            loaded = min(item.quantity, tubes.get(type_id, 0) * squadron_size(type_id))
            _add(expectations, FIGHTER_TUBES, type_id, item.name, loaded)
            _add(expectations, "fighters", type_id, item.name, item.quantity - loaded)
        expectations.setdefault(FIGHTER_TUBES, {})            # the split is on, even with nothing in tubes

    bays = ((fitting.get("doctrine_metadata") or {}).get("bays") or {}) if fit is not fitting else {}
    for bay_key, location in METADATA_ITEM_BAYS.items():
        for requirement in bays.get(bay_key) or []:
            if isinstance(requirement, dict) and requirement.get("type_id") and (requirement.get("match") or "type") == "type":
                _add(expectations, location, int(requirement["type_id"]), requirement.get("name"),
                     int(requirement.get("min_quantity", 1) or 0))
    return expectations


def metadata_keys(fitting: Dict[str, Any], bay_key: str) -> Dict[int, int]:
    """{type_id: min_quantity} for one metadata bay of a fitting record; an entry naming a saved
    fitting (the maintenance bay, plan 20.2) counts its hull."""
    bays = (fitting.get("doctrine_metadata") or {}).get("bays") or {} if isinstance(fitting, dict) else {}
    found: Dict[int, int] = {}
    for requirement in bays.get(bay_key) or []:
        if isinstance(requirement, dict) and requirement.get("type_id") and (requirement.get("match") or "type") in ("type", "fit"):
            found[int(requirement["type_id"])] = found.get(int(requirement["type_id"]), 0) + int(requirement.get("min_quantity", 1) or 0)
    return found
