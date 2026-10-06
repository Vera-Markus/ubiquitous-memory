"""
Ship status, listing order and requirement roll-up (design §9.4, decision D13).
"""
from typing import List

from app.models.audit_models import RequirementStatus, ShipRequirementResult

STATUS_RANK = {RequirementStatus.PASS: 0, RequirementStatus.WARN: 1, RequirementStatus.FAIL: 2}


def ship_status(result: ShipRequirementResult) -> RequirementStatus:
    """
    FAIL if anything is missing (a ship short in the maintenance bay counts, D19),
    WARN if anything needs attention (escape bay problems only ever warn), otherwise PASS.
    """
    bays = {b.status for b in result.bay_results}
    if result.shortfalls or RequirementStatus.FAIL in bays:
        return RequirementStatus.FAIL
    if result.refit_moves or result.substitutions or result.unexpected_items or RequirementStatus.WARN in bays:
        return RequirementStatus.WARN
    return RequirementStatus.PASS


def listing_order(results: List[ShipRequirementResult]) -> List[ShipRequirementResult]:
    """Best first: PASS, then WARN with the fewest changes, then FAIL with the fewest shortfalls."""
    def key(r: ShipRequirementResult):
        changes = (len(r.refit_moves) + len(r.substitutions) + len(r.unexpected_items)
                   + sum(1 for b in r.bay_results if b.status == RequirementStatus.WARN))
        missing = sum(s.missing for s in r.shortfalls) + sum(s.missing for b in r.bay_results for s in b.shortfalls
                                                              if b.bay_key == "ship_maintenance_bay")
        return (STATUS_RANK[r.status], missing, changes, r.custom_name or "", r.ship_item_id or 0)
    return sorted(results, key=key)


def requirement_status(ordered: List[ShipRequirementResult]) -> RequirementStatus:
    """The best listed ship's status; FAIL when no ship of the hull is at the location."""
    return ordered[0].status if ordered else RequirementStatus.FAIL
