"""
Bay results for one ship (design §9.2-9.6).

- Fuel bay: a view of what the inventory and configuration questions already
  found for the fuel requirements, with every count (the fuel summary needs
  required and aboard even when nothing is short). Fuel anywhere aboard
  counts (D20); fuel elsewhere than the fuel bay is a refit.
- Ship maintenance bay: strict (D19). Only ships of the type inside this
  carrier's maintenance bay count. A shortfall is a FAIL on the bay row.
- Escape bay: the §9.6 rules. Every problem is a WARN (D8), never a FAIL.

The fleet hangar has no row of its own: its requirements are pooled with the
cargo (D2) and reported with the rest of the ship's items.
"""
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set

from app.loaders.sde_rules import SdeRules
from app.models.asset_models import CarriedShip, ShipAsset
from app.models.audit_models import BayResult, ItemShortfall, RefitMove, RequirementStatus
from app.models.bay_registry import ESCAPE_BAY, FUEL_BAY, SHIP_MAINTENANCE_BAY
from app.services.audit.configuration import evaluate_configuration
from app.services.audit.expectations import build_expectations, metadata_keys
from app.services.audit.inventory import InventoryResult, evaluate_inventory, items_aboard

S = RequirementStatus


@dataclass
class BayContext:
    """What the bay checks need beyond the ship itself."""
    carried: List[CarriedShip] = field(default_factory=list)          # every carried ship in the snapshot
    ships_by_item: Dict[int, ShipAsset] = field(default_factory=dict)   # assembled ships, for the escape fit check
    get_fitting: Callable[[int], Optional[Dict[str, Any]]] = lambda uid: None

    def inside(self, carrier_item_id: int, bay_key: str) -> List[CarriedShip]:
        return sorted((c for c in self.carried if c.carrier_item_id == carrier_item_id and c.bay_key == bay_key),
                      key=lambda c: c.item_id)


def evaluate_bays(ship: ShipAsset, fitting: Dict[str, Any], inventory: InventoryResult, moves: List[RefitMove],
                  context: BayContext, rules: SdeRules) -> List[BayResult]:
    results = []
    fuel = fuel_bay_result(fitting, inventory, moves, rules)
    if fuel:
        results.append(fuel)
    smb = ship_maintenance_bay_result(ship, fitting, context)
    if smb:
        results.append(smb)
    escape = escape_bay_result(ship, fitting, context, rules)
    if escape:
        results.append(escape)
    return results


# --- fuel bay ----------------------------------------------------------------------------------

def fuel_bay_result(fitting: Dict[str, Any], inventory: InventoryResult, moves: List[RefitMove],
                    rules: SdeRules) -> Optional[BayResult]:
    required = metadata_keys(fitting, FUEL_BAY.key)
    if not required:
        return None
    names = {int(r["type_id"]): r.get("name", "") for r in fitting["doctrine_metadata"]["bays"][FUEL_BAY.key]
             if isinstance(r, dict) and r.get("type_id")}
    counts, shortfalls = [], []
    for type_id in sorted(required):
        key = rules.equivalence_key(type_id)
        # Totals come from the inventory, which pools every requirement on the key.
        count = ItemShortfall(type_id, names.get(type_id, str(type_id)), inventory.required[key],
                              min(inventory.aboard[key], inventory.required[key]))
        counts.append(count)
        if inventory.missing_by_key[key]:
            shortfalls.append(ItemShortfall(type_id, count.name, inventory.required[key],
                                            inventory.required[key] - inventory.missing_by_key[key]))
    misplaced = [m for m in moves if m.to_location == FUEL_BAY.key]
    status = S.FAIL if shortfalls else S.WARN if misplaced else S.PASS
    message = ""
    if misplaced:
        message = "; ".join(f"{m.quantity:,}× {m.name} in {m.from_location.replace('_', ' ')}, move to the fuel bay"
                            for m in misplaced)
    return BayResult(FUEL_BAY.key, status, shortfalls=shortfalls, counts=counts, message=message)


# --- ship maintenance bay (D19) ---------------------------------------------------------------

def ship_maintenance_bay_result(ship: ShipAsset, fitting: Dict[str, Any], context: BayContext) -> Optional[BayResult]:
    required = metadata_keys(fitting, SHIP_MAINTENANCE_BAY.key)
    if not required:
        return None
    names = {int(r["type_id"]): r.get("name", "") for r in fitting["doctrine_metadata"]["bays"][SHIP_MAINTENANCE_BAY.key]
             if isinstance(r, dict) and r.get("type_id")}
    inside = Counter()
    for carried in context.inside(ship.asset.item_id, SHIP_MAINTENANCE_BAY.key):
        inside[carried.type_id] += carried.quantity
    counts = [ItemShortfall(t, names.get(t, str(t)), q, min(inside[t], q)) for t, q in sorted(required.items())]
    shortfalls = [c for c in counts if c.missing]
    return BayResult(SHIP_MAINTENANCE_BAY.key, S.FAIL if shortfalls else S.PASS, shortfalls=shortfalls, counts=counts)


# --- escape bay (§9.6) --------------------------------------------------------------------------

def escape_requirement(fitting: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    entries = ((fitting.get("doctrine_metadata") or {}).get("bays") or {}).get(ESCAPE_BAY.key) or []
    return entries[0] if entries and isinstance(entries[0], dict) else None


def escape_bay_result(ship: ShipAsset, fitting: Dict[str, Any], context: BayContext, rules: SdeRules) -> Optional[BayResult]:
    requirement = escape_requirement(fitting)
    if requirement is None:
        return None             # no escape ship chosen: whatever is there is "still packed" (§9.5)
    inside = context.inside(ship.asset.item_id, ESCAPE_BAY.key)
    found = inside[0] if inside else None

    def warn(message, **kw):
        return BayResult(ESCAPE_BAY.key, S.WARN, message=message, **kw)

    if requirement.get("match") == "any_ship":
        if found is None:
            return warn("empty")
        return BayResult(ESCAPE_BAY.key, S.PASS, message=f"{found.name} (any)")

    recommended = context.get_fitting(requirement.get("fit_uid"))
    if recommended is None:
        return warn("recommended fitting no longer exists, choose a new one")
    fit_name = recommended.get("fit_name", requirement.get("name", ""))
    if found is None:
        return warn(f"empty, {fit_name} recommended")
    if found.type_id != recommended.get("hull_type_id"):
        return warn(f"expected {fit_name}, found {found.name}")

    escape_ship = context.ships_by_item.get(found.item_id)
    if escape_ship is None:
        return warn(f"{found.name} is packaged; assemble and fit it as {fit_name}")
    expectations = build_expectations(recommended)
    aboard = items_aboard(escape_ship, rules)
    inventory = evaluate_inventory(expectations, aboard, rules)
    moves = evaluate_configuration(expectations, aboard, inventory, rules)
    unexpected = [name for _, _, name, _ in inventory.unexpected]
    if inventory.shortfalls or moves or inventory.substitutions or unexpected:
        # One warning on the bay row, with what differs beneath it (D10).
        return warn(f"{found.name} differs from {fit_name}", shortfalls=inventory.shortfalls, unexpected=unexpected)
    return BayResult(ESCAPE_BAY.key, S.PASS, message=f"{found.name} ({fit_name})")


def escape_covered_carriers(fittings_by_carrier: Dict[int, List[Dict[str, Any]]]) -> Set[int]:
    """Carriers whose matched fittings say what goes in the escape bay: those ships are never "still packed"."""
    return {carrier for carrier, fittings in fittings_by_carrier.items()
            if any(escape_requirement(f) is not None for f in fittings)}
