"""
Locking within a role (homes and priorities plan, step 22.3, P4/P5).

A hard requirement covers the role's other requirements for the same fitting inside its
area, and those count as soft: Any system ⊃ a system (any station) ⊃ a station in it. One
ship can then serve both. Nothing is saved: the lock is worked out each time, so softening
or removing the wider requirement gives the narrower one back its own priority.

    effective_priority(role, requirement)   # "hard" or "soft"
    covering(role, requirement)             # the hard requirement that locks it, or None
    would_lock(role, candidate)             # what making `candidate` hard would turn soft
"""
from typing import Any, Dict, List, Optional

from app.loaders.role_manager import fitting_in_use, requirement_priority

Requirement = Dict[str, Any]


def contains(wider: Requirement, narrower: Requirement) -> bool:
    """Whether wider's area holds narrower's, and is larger (a requirement doesn't contain itself)."""
    w_system, w_location = wider.get("system_id"), wider.get("location_id")
    n_system, n_location = narrower.get("system_id"), narrower.get("location_id")
    if w_system is None:                                # any system, any station
        return n_system is not None
    if w_location is None:                              # a system, any station
        return n_system == w_system and n_location is not None
    return False                                        # a station holds nothing smaller


def _rank(requirement: Requirement) -> int:
    """Widest first: any system, then a system, then a station."""
    return 0 if requirement.get("system_id") is None else 1 if requirement.get("location_id") is None else 2


def covering(role: Dict[str, Any], requirement: Requirement) -> Optional[Requirement]:
    """The widest hard requirement in the role, for the same fitting in use, whose area holds this one's."""
    fit_uid = fitting_in_use(requirement)
    wider = [r for r in role.get("requirements", [])
             if r is not requirement and r.get("req_uid") != requirement.get("req_uid")
             and fitting_in_use(r) == fit_uid and requirement_priority(r) == "hard" and contains(r, requirement)]
    return min(wider, key=_rank) if wider else None


def effective_priority(role: Dict[str, Any], requirement: Requirement) -> str:
    """Its own priority, unless a wider hard requirement for the same fitting locks it soft."""
    if requirement_priority(requirement) == "soft" or covering(role, requirement) is not None:
        return "soft"
    return "hard"


def would_lock(role: Dict[str, Any], candidate: Requirement) -> List[Requirement]:
    """
    The role's requirements that are hard now and that `candidate`, as a hard requirement,
    would lock soft (P5's warning). candidate needs fit_uid, system_id and location_id; it
    may be a requirement of the role being made hard, or one about to be added.
    """
    fit_uid = fitting_in_use(candidate)
    return [r for r in role.get("requirements", [])
            if r.get("req_uid") != candidate.get("req_uid") and fitting_in_use(r) == fit_uid
            and contains(candidate, r) and effective_priority(role, r) == "hard"]
