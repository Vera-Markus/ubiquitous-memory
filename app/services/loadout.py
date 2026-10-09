"""
What the Fittings tab's Loadout view shows (1.7.2 plan, 36.3), worked out without Tk.

- The fit: High, Mid, Low, Rigs (and Subsystems on a T3 cruiser), one name per fitted
  module, with how many slots the hull has left empty.
- The drone or fighter bay: fighters with their tube squadrons beside them, drones with
  the bay's size and how much is used, or a hull with neither (shown faded, "Drone bay 0 m³").
- The cargo (and fleet hangar, on hulls that have one): one "N × Name" line per item.
- An implant set (a Capsule fitting): its contents in one box instead of all that.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.services.doctrine_metadata_form import TubeRow, tube_rows
from app.services.implant_rules import is_implant_set

# (fit section, label, the hull's slot-count attribute)
SLOT_SECTIONS = (("high", "High", 14), ("mid", "Mid", 13), ("low", "Low", 12), ("rigs", "Rigs", 1137),
                 ("subsystem", "Subsystems", 1367))
DRONE_CAPACITY = 283            # droneCapacity
IMPLANT_CATEGORY = 20


@dataclass
class SlotSection:
    key: str
    label: str
    modules: List[str]          # one name per fitted module, in the fit's order
    empty: int                  # slots the hull has that nothing fills


@dataclass
class FighterLine:
    name: str
    quantity: int
    tube: Optional[TubeRow]     # its squadrons in the tubes; None when it can't go in a tube


@dataclass
class BayBox:
    kind: str                   # "fighters", "drones" or "none"
    title: str
    lines: List[str] = field(default_factory=list)        # drones, or the "none" text
    fighters: List[FighterLine] = field(default_factory=list)
    tubes: int = 0

    @property
    def faded(self) -> bool:
        return self.kind == "none"


def m3(value: float) -> str:
    return f"{value:,.0f} m³" if value >= 10 else f"{value:,.1f} m³"


def _fit(fitting: Dict[str, Any]) -> Dict[str, Any]:
    return fitting.get("fit", fitting) or {}


def _items(fitting: Dict[str, Any], key: str):
    return list((_fit(fitting).get(key) or {}).items())


def _hull(fitting: Dict[str, Any]) -> Optional[int]:
    return fitting.get("hull_type_id") or _fit(fitting).get("hull_type_id")


def stacks(fitting: Dict[str, Any], key: str) -> List[str]:
    """ "N × Name" for each item in a bay (cargo, drones), in the fit's order."""
    return [f"{int(item.get('quantity', 1)):,} × {item.get('name', 'Unknown')}" for _, item in _items(fitting, key)]


def volume(fitting: Dict[str, Any], key: str, sde: Any) -> float:
    total = 0.0
    for type_id, item in _items(fitting, key):
        try:
            total += (sde.get_type_volume(int(type_id)) or 0) * int(item.get("quantity", 1))
        except (TypeError, ValueError):
            pass
    return total


def slots(fitting: Dict[str, Any], sde: Any) -> List[SlotSection]:
    """The fit's sections, with empty slots; a section the hull doesn't have and the fit doesn't use is left out."""
    hull = _hull(fitting)
    counts = sde._attributes(hull, tuple(attr for _, _, attr in SLOT_SECTIONS)) if hull else {}
    found = []
    for key, label, attr in SLOT_SECTIONS:
        modules = [item.get("name", "Unknown") for _, item in _items(fitting, key)
                   for _ in range(int(item.get("quantity", 1)))]
        total = int(counts.get(attr) or 0)
        if modules or total:
            found.append(SlotSection(key, label, modules, max(total - len(modules), 0)))
    return found


def bay_box(fitting: Dict[str, Any], sde: Any, saved_tubes: Optional[Dict[int, int]] = None) -> BayBox:
    """The drone or fighter bay (no ship has both), or a faded empty drone bay."""
    hull = _hull(fitting)
    bays = sde.get_hull_bays(hull) if hull else {}
    if "fighters" in bays:
        tubes = sde.get_fighter_tubes(hull)
        by_name = {row.name: row for row in tube_rows(fitting, saved_tubes or {}, sde)} if tubes else {}
        fighters = [FighterLine(item.get("name", "Unknown"), int(item.get("quantity", 1)),
                                by_name.get(item.get("name"))) for _, item in _items(fitting, "fighters")]
        return BayBox("fighters", f"Fighter bay ({m3(bays['fighters'])}, {tubes} tubes)", fighters=fighters,
                      tubes=tubes)
    capacity = (sde._attributes(hull, (DRONE_CAPACITY,)).get(DRONE_CAPACITY) or 0) if hull else 0
    if capacity <= 0:
        return BayBox("none", "Drone bay 0 m³", ["This hull has no drone bay."])
    used = volume(fitting, "drones", sde)
    return BayBox("drones", f"Drone bay ({m3(used)} of {m3(capacity)})", stacks(fitting, "drones") or ["Empty"])


def cargo_title(fitting: Dict[str, Any], sde: Any) -> str:
    hull = _hull(fitting)
    used = m3(volume(fitting, "cargo", sde))
    if hull and "fleet_hangar" in sde.get_hull_bays(hull):
        return f"Cargo and fleet hangar ({used} of items)"
    return f"Cargo ({used} of items)"


def contents_title(fitting: Dict[str, Any], sde: Any) -> str:
    """An implant set's box: "Implants" when that's what it lists, "Contents" for anything else
    kept on a Capsule fitting (a container of ammo and fuel, say)."""
    categories = set()
    for type_id, _ in _items(fitting, "cargo"):
        try:
            categories.add(sde.get_type_category(int(type_id)))
        except (TypeError, ValueError):
            categories.add(None)
    return "Implants" if categories == {IMPLANT_CATEGORY} else "Contents"


def is_contents_only(fitting: Dict[str, Any]) -> bool:
    """A Capsule fitting: no slots or bays, just what it lists."""
    return is_implant_set(fitting)
