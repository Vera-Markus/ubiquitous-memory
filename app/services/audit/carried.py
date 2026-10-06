"""
The "suitcase" check (design §9.5, decision D7).

Capitals carry ships between deployments. A ship left inside another ship
after the move hasn't been unpacked, and the FC needs to know. Any carried
ship that the carrier's doctrine metadata doesn't call for is a warning
("still packed"), never a failure.

A carried ship is expected when the carrier was audited against a fitting
whose metadata asks for that ship type in that bay; with several fittings,
the highest quantity per (bay, type) applies. Ships beyond what's expected
warn: "not_in_metadata" when none were expected, "exceeds_requirement"
otherwise. Ships in an escape bay whose fitting chooses an escape ship are
judged by the escape bay rules only (§9.6), so they never warn here.
"""
from collections import defaultdict
from dataclasses import replace
from typing import Dict, Iterable, List, Optional, Set, Tuple

from app.models.asset_models import AuditSnapshot
from app.models.audit_models import AuditResult, PackedShipWarning

# The fleet hangar and the cargo are one pooled space (D2).
POOLED = {"cargo": "fleet_hangar"}

Expected = Dict[Tuple[int, str, int], int]      # (carrier item ID, bay key, ship type ID) -> quantity


def pooled(bay_key: str) -> str:
    return POOLED.get(bay_key, bay_key)


def check_carried_ships(snapshot: AuditSnapshot, results: List[AuditResult], expected: Optional[Expected] = None,
                        escape_covered: Iterable[int] = ()) -> List[PackedShipWarning]:
    """
    One warning per carried ship entry beyond what its carrier's metadata
    expects, whether or not the carrier belongs to any role. `results` are the
    character's role audits; `expected` and `escape_covered` come from them
    (AuditEngine.check_carried_ships works them out).
    """
    expected = expected or {}
    covered: Set[int] = set(escape_covered)
    allowance: Dict[Tuple[int, str, int], int] = defaultdict(int, expected)
    warnings = []
    for ship in sorted(snapshot.carried_ships, key=lambda s: s.item_id):
        if ship.bay_key == "escape_bay" and ship.carrier_item_id in covered:
            continue
        key = (ship.carrier_item_id, pooled(ship.bay_key), ship.type_id)
        allowed = allowance[key]
        if allowed >= ship.quantity:
            allowance[key] -= ship.quantity
            continue
        allowance[key] = 0
        reason = "not_in_metadata" if not expected.get(key) else "exceeds_requirement"
        excess = ship if allowed == 0 else replace(ship, quantity=ship.quantity - allowed)
        warnings.append(PackedShipWarning(ship=excess, reason=reason))
    return sorted(warnings, key=lambda w: (w.ship.carrier_name, w.ship.carrier_custom_name or "", w.ship.name,
                                           w.ship.custom_name or "", w.ship.item_id))
