"""
Implant set slot rules (TRACKED_ITEMS_DESIGN.md §5.3; the user, 2026-10-06).

An implant set is a fitting whose hull is the Capsule; the implants it lists are checked
slot by slot against the implants a clone has:

- Slots 1-6: the exact implant listed. Anything else, or nothing, fails.
- Slots 7-10: any implant in the slot at strength 05 or better (the last two digits of the
  code in its name: MR-705 is 05, HG-1008 is 08). Slot 10 also takes any mindlink.
- A slot that lists 06 or stronger needs at least 06; between 06 and what's listed it passes
  with a warning ("1008 preferred"). A listed strength below 05 is the minimum itself.
- An implant with no code in its name (Zor's, the "Modified" faction ones, ...) can't be
  judged by name: a warning, "check by hand".

Only the slots the set lists are checked. eve.db doesn't link implant grades as variants,
so this works from the slot (attribute 331) and the name.
"""
import re
from typing import Dict, Iterable, List, Optional, Tuple

from app.models.audit_models import ImplantSlotResult, RequirementStatus

CAPSULE_TYPE_ID = 670
EXACT_SLOTS = range(1, 7)
MINDLINK_SLOT = 10
MINIMUM_STRENGTH = 5
RAISED_MINIMUM = 6

_CODE = re.compile(r"\b[A-Z]{2,3}-(\d{1,2})(\d{2})\b")

PASS, WARN, FAIL = RequirementStatus.PASS, RequirementStatus.WARN, RequirementStatus.FAIL


def is_implant_set(fitting: Optional[dict]) -> bool:
    """A fitting whose hull is the Capsule is an implant set (D5)."""
    if not fitting:
        return False
    hull_type_id = fitting.get("hull_type_id") or (fitting.get("fit") or {}).get("hull_type_id")
    return hull_type_id == CAPSULE_TYPE_ID or (hull_type_id is None and fitting.get("hull") == "Capsule")


def set_name(fitting: dict) -> str:
    """An implant set's name: '(Implants) High-Grade Amulets' -> 'High-Grade Amulets'."""
    name = (fitting.get("fit_name") or "").strip()
    if name.casefold().startswith("(implants)"):
        name = name[len("(implants)"):].strip()
    return name or "Implant set"


def code(name: str) -> Optional[str]:
    """The implant's code digits: "Motion Prediction MR-705" -> "705", "HG-1008" -> "1008"; None without one."""
    found = _CODE.findall(name or "")
    return "".join(found[-1]) if found else None


def strength(name: str) -> Optional[int]:
    """The strength in an implant's name: the code's last two digits (MR-705 -> 5); None without a code."""
    digits = code(name)
    return int(digits[-2:]) if digits else None


def is_mindlink(name: str) -> bool:
    return "mindlink" in (name or "").casefold()


def check_slot(slot: int, listed_id: int, listed_name: str,
               worn_id: Optional[int], worn_name: str = "") -> ImplantSlotResult:
    """One listed slot against what the clone has in it (worn_id None: the slot is empty)."""
    result = ImplantSlotResult(slot=slot, status=PASS, listed_type_id=listed_id, listed_name=listed_name,
                               worn_type_id=worn_id, worn_name=worn_name)
    if worn_id == listed_id:
        return result
    if worn_id is None:
        result.status, result.reason = FAIL, "empty"
        return result
    if slot in EXACT_SLOTS:
        result.status, result.reason = FAIL, f"needs {listed_name}"
        return result
    if slot == MINDLINK_SLOT and is_mindlink(worn_name):
        return result
    worn_strength = strength(worn_name)
    if worn_strength is None:
        result.status, result.reason = WARN, "check by hand"
        return result
    listed_strength = strength(listed_name)
    if listed_strength is None:
        minimum, preferred = MINIMUM_STRENGTH, MINIMUM_STRENGTH
    elif listed_strength >= RAISED_MINIMUM:
        minimum, preferred = RAISED_MINIMUM, listed_strength
    else:
        minimum = preferred = min(listed_strength, MINIMUM_STRENGTH)
    if worn_strength < minimum:
        result.status, result.reason = FAIL, f"needs {minimum:02d} or better"
    elif worn_strength < preferred:
        result.status, result.reason = WARN, f"{code(listed_name)} preferred"
    return result


def listed_implants(fitting: dict, implant_slot, type_name) -> Dict[int, Tuple[int, str]]:
    """{slot: (type ID, name)} for every implant an implant set lists, whatever section it's in."""
    listed: Dict[int, Tuple[int, str]] = {}
    for section in (fitting.get("fit") or fitting).values():      # a library record, or parse_fit's output
        if not isinstance(section, dict):
            continue
        for key, item in section.items():
            if not str(key).isdigit():
                continue
            type_id = int(key)
            slot = implant_slot(type_id)
            if slot and slot not in listed:
                name = (item or {}).get("name") if isinstance(item, dict) else None
                listed[slot] = (type_id, name or type_name(type_id))
    return listed


def check_set(listed: Dict[int, Tuple[int, str]], worn: Iterable[int], implant_slot,
              type_name) -> Tuple[RequirementStatus, List[ImplantSlotResult]]:
    """
    A set (from listed_implants) against a clone's implant type IDs: the worst slot's status,
    and every listed slot's result in slot order.
    """
    by_slot: Dict[int, int] = {}
    for type_id in worn:
        slot = implant_slot(type_id)
        if slot:
            by_slot.setdefault(slot, type_id)
    results = []
    for slot in sorted(listed):
        listed_id, listed_name = listed[slot]
        worn_id = by_slot.get(slot)
        results.append(check_slot(slot, listed_id, listed_name, worn_id,
                                  type_name(worn_id) if worn_id is not None else ""))
    statuses = {r.status for r in results}
    status = FAIL if FAIL in statuses else WARN if WARN in statuses else PASS
    return status, results
