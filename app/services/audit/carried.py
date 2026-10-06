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
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple

from app.models.asset_models import AuditSnapshot, CarriedShip
from app.models.audit_models import AuditResult, PackedShipWarning

# The fleet hangar and the cargo are one pooled space (D2).
POOLED = {"cargo": "fleet_hangar"}

Expected = Dict[Tuple[int, str, int], int]      # (carrier item ID, bay key, ship type ID) -> quantity
# (carrier item ID, ship type ID) -> the maintenance bay's entries for the hull: (fit_uid or None for any, quantity)
FitEntries = Dict[Tuple[int, int], List[Tuple[Optional[int], int]]]
# (carried ship, fit_uid) -> None when it matches the fitting, else (fitting name, what differs)
FitCheck = Callable[[CarriedShip, int], Optional[Tuple[str, str]]]


def pooled(bay_key: str) -> str:
    return POOLED.get(bay_key, bay_key)


def check_carried_ships(snapshot: AuditSnapshot, results: List[AuditResult], expected: Optional[Expected] = None,
                        escape_covered: Iterable[int] = (), fit_entries: Optional[FitEntries] = None,
                        fit_check: Optional[FitCheck] = None) -> List[PackedShipWarning]:
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
    if fit_entries and fit_check is not None:
        warnings += _wrong_fits(snapshot, fit_entries, fit_check, {w.ship.item_id for w in warnings})
    return sorted(warnings, key=lambda w: (w.ship.carrier_name, w.ship.carrier_custom_name or "", w.ship.name,
                                           w.ship.custom_name or "", w.ship.item_id))


def _wrong_fits(snapshot: AuditSnapshot, fit_entries: FitEntries, fit_check: FitCheck,
                already: Set[int]) -> List[PackedShipWarning]:
    """
    Expected maintenance bay ships checked against the saved fittings their entries name
    (plan 20.2). Ships that match an entry's fitting fill it first, then entries for any
    fitting; a ship left over fills an entry it doesn't match and warns, inside the same
    carried-ship warning (U10: never a second warning, never a failure).
    """
    warnings = []
    for (carrier, type_id), entries in sorted(fit_entries.items()):
        if not any(uid is not None for uid, _ in entries):
            continue
        ships = sorted((s for s in snapshot.carried_ships if s.carrier_item_id == carrier and s.type_id == type_id
                        and s.bay_key == "ship_maintenance_bay" and s.item_id not in already),
                       key=lambda s: s.item_id)
        left = [[uid, quantity] for uid, quantity in entries]
        pending = []
        for ship in ships:
            for _ in range(ship.quantity):
                slot = next((e for e in left if e[1] > 0 and e[0] is not None and fit_check(ship, e[0]) is None), None)
                if slot is None:
                    pending.append(ship)
                else:
                    slot[1] -= 1
        for ship in pending:
            slot = next((e for e in left if e[1] > 0 and e[0] is None), None) or \
                next((e for e in left if e[1] > 0), None)
            if slot is None:
                continue
            slot[1] -= 1
            if slot[0] is not None:
                fit_name, detail = fit_check(ship, slot[0])
                warnings.append(PackedShipWarning(ship=replace(ship, quantity=1), reason="wrong_fit",
                                                  fit_name=fit_name, detail=detail))
    return warnings
