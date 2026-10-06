"""
Implant sets matched to a character's clones (TRACKED_ITEMS_DESIGN.md §9, D7; plan 15.2).

An implant set requirement is a role requirement whose fitting is a Capsule (D5). Its
location is the set's home. The character's clones come from data/clones/<id>.json: the
active clone (where it is isn't pulled) and each jump clone (with its station or structure).

Each clone covers at most one requirement (D7), matched in this order:
1. a jump clone at the requirement's home that has the set: HOME;
2. the active clone, for one requirement still unmatched: IN USE (ready, no warning);
3. a jump clone elsewhere that has the set: AWAY (WARN);
4. nothing: MISSING (FAIL), with the slots the closest clone lacks.

A clone "has the set" when check_set passes or only warns. No clone IDs are kept (a swap
gives the clone a new ID), so swapping never loses a set.
"""
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.models.audit_models import (ImplantSetResult, ItemShortfall, RequirementResult, RequirementStatus,
                                     is_ready)
from app.services.implant_rules import check_set, listed_implants, set_name

HOME, IN_USE, AWAY, MISSING, UNKNOWN = "HOME", "IN USE", "AWAY", "MISSING", "UNKNOWN"
NO_CLONE_DATA = ("No clone data for this character: log in again (Characters ▸ Add Character) "
                 "and Pull All to include implants")


@dataclass
class ImplantNeed:
    """One implant set requirement: the requirement, the fitting in use, and a key to find its result."""
    key: Any
    requirement: Dict[str, Any]
    fitting: Dict[str, Any]


@dataclass
class _Clone:
    implants: List[int]
    location_id: Optional[int]          # None: the active clone
    jump_clone_id: Optional[int] = None

    @property
    def active(self) -> bool:
        return self.location_id is None


def clones_of(record: Optional[Dict[str, Any]]) -> List[_Clone]:
    """The active clone first, then each jump clone, from a data/clones/<id>.json record."""
    if not record:
        return []
    clones = [_Clone(list(record.get("active_implants") or []), None)]
    for jump in record.get("jump_clones") or []:
        clones.append(_Clone(list(jump.get("implants") or []), jump.get("location_id"), jump.get("jump_clone_id")))
    return clones


def audit_implants(needs: List[ImplantNeed], clone_record: Optional[Dict[str, Any]], rules,
                   system_of: Callable[[int], Optional[int]],
                   location_label: Callable[[int], str]) -> Dict[Any, RequirementResult]:
    """
    {need.key: RequirementResult} for every implant set requirement, each carrying an
    ImplantSetResult. Without a clone record (no clone scope, or not pulled yet) every set
    is a warning that says to log in again.
    """
    results: Dict[Any, RequirementResult] = {}
    if clone_record is None:
        for need in needs:
            results[need.key] = RequirementResult(
                req_uid=need.requirement["req_uid"], status=RequirementStatus.WARN, requirement_details=need.requirement,
                message=NO_CLONE_DATA,
                implant_set=ImplantSetResult(set_name=set_name(need.fitting), placement=UNKNOWN,
                                             status=RequirementStatus.WARN))
        return results

    clones = clones_of(clone_record)
    listed = {id(need): listed_implants(need.fitting, rules.implant_slot, rules.type_name) for need in needs}
    checks: Dict[Tuple[int, int], Tuple[RequirementStatus, list]] = {}

    def check(need: ImplantNeed, index: int):
        key = (id(need), index)
        if key not in checks:
            checks[key] = check_set(listed[id(need)], clones[index].implants, rules.implant_slot, rules.type_name)
        return checks[key]

    def at_home(need: ImplantNeed, clone: _Clone) -> bool:
        system_id, location_id = need.requirement.get("system_id"), need.requirement.get("location_id")
        if location_id is not None and clone.location_id != location_id:
            return False
        return system_id is None or system_of(clone.location_id) == system_id

    used: set = set()
    matched: Dict[int, Tuple[str, int]] = {}            # id(need) -> (placement, clone index)

    def match(placement: str, candidates) -> None:
        for need in needs:
            if id(need) in matched:
                continue
            for index in candidates(need):
                if index in used or not is_ready(check(need, index)[0]):
                    continue
                matched[id(need)] = (placement, index)
                used.add(index)
                break

    jump = [i for i, c in enumerate(clones) if not c.active]
    active = [i for i, c in enumerate(clones) if c.active]
    match(HOME, lambda need: sorted((i for i in jump if at_home(need, clones[i])), key=lambda i: _rank(check(need, i))))
    match(IN_USE, lambda need: active)
    match(AWAY, lambda need: sorted((i for i in jump if not at_home(need, clones[i])),
                                    key=lambda i: _rank(check(need, i))))

    for need in needs:
        name = set_name(need.fitting)
        requirement = need.requirement
        if id(need) in matched:
            placement, index = matched[id(need)]
            status, slots = check(need, index)
            clone = clones[index]
            where = location_label(clone.location_id) if clone.location_id is not None else ""
            if placement == AWAY:
                status = RequirementStatus.WARN
            message = {HOME: f"Jump clone at home ({where})", IN_USE: "In use (active clone)",
                       AWAY: f"Away: jump clone in {where}"}[placement]
            results[need.key] = RequirementResult(
                req_uid=requirement["req_uid"], status=status, requirement_details=requirement, message=message,
                implant_set=ImplantSetResult(set_name=name, placement=placement, status=status, clone=_describe(clone),
                                             where=where, slots=slots))
            continue
        # Missing: report against the closest unused clone (most slots ready; at home first on a tie),
        # or an empty one.
        spare = [i for i in range(len(clones)) if i not in used]
        best = min(spare, key=lambda i: (_missing_count(check(need, i)),
                                         clones[i].active or not at_home(need, clones[i])), default=None)
        if best is not None:
            _, slots = check(need, best)
            clone = clones[best]
        else:
            _, slots = check_set(listed[id(need)], [], rules.implant_slot, rules.type_name)
            clone = None
        shortfalls = [ItemShortfall(s.listed_type_id, s.listed_name, 1, 0) for s in slots
                      if s.status == RequirementStatus.FAIL and s.listed_type_id is not None]
        results[need.key] = RequirementResult(
            req_uid=requirement["req_uid"], status=RequirementStatus.FAIL, requirement_details=requirement,
            message=f"No clone with the {name} set", shortfalls=shortfalls,
            implant_set=ImplantSetResult(set_name=name, placement=MISSING, status=RequirementStatus.FAIL,
                                         clone=_describe(clone) if clone else "",
                                         where=location_label(clone.location_id) if clone and clone.location_id else "",
                                         slots=slots))
    return results


def _rank(result) -> int:
    """PASS before WARN, so a clean clone is used before one that only warns."""
    return 0 if result[0] == RequirementStatus.PASS else 1


def _missing_count(result) -> int:
    return sum(1 for s in result[1] if s.status == RequirementStatus.FAIL)


def _describe(clone: _Clone) -> str:
    return "active clone" if clone.active else "jump clone"

