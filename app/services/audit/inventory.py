"""
Question 1: is everything the fitting needs on board? (design §9.2-9.4)

Counts are by equivalence key (D11), so a module and its identical-stat twins
count as one type. Nothing else stands in (user rule, 2026-10-06): a variant
aboard instead of the fitting's module, or other ammo, leaves it missing.
The one exception is bling: a better version of the module (SdeRules.is_bling)
covers it as a warning, and isn't bought. Only missing items fail.

Mutated modules (TRACKED_ITEMS_DESIGN.md §9a): one generic type per group, so each
item is judged by its own base module. A mutated item whose base is the fitting's
module (or an identical twin), or a better version of it, covers it as bling (M1).
A fitting that lists the mutated type itself is matched by type (M2). A mutated
item whose base isn't known yet covers nothing.
"""
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from app.loaders.sde_rules import CATEGORY_CHARGE, CATEGORY_SHIP, SdeRules
from app.models.asset_models import Asset, ShipAsset
from app.models.audit_models import ItemShortfall, Substitution
from app.services.audit.expectations import FIGHTER_TUBES, Expectations

# Spaces aboard that hold items but are never expected locations in phase 1.
# Their contents count toward totals but are never reported as unexpected.
UNCHECKED_LOCATIONS = ("fuel_bay",)


@dataclass(frozen=True)
class AboardItem:
    location: str          # an expectation location ("high", "cargo", ...) or "fuel_bay"
    type_id: int
    name: str
    quantity: int
    mutated_base: Optional[int] = None      # set for a mutated item whose base is known


def items_aboard(ship: ShipAsset, rules: SdeRules, split_tubes: bool = False) -> List[AboardItem]:
    """
    Everything on the ship itself, by location. Ships carried inside it are
    separate ships (design §9.5) and are left out. split_tubes: squadrons in tubes
    are "fighter_tubes", apart from the bay (plan 20.3).
    """
    fitting = ship.fitting
    found: List[AboardItem] = []

    def add(location: str, asset: Asset, quantity: int = None) -> None:
        if rules.category(asset.type_id) == CATEGORY_SHIP:
            return
        found.append(AboardItem(location, asset.type_id, asset.name, asset.quantity if quantity is None else quantity,
                                asset.mutated_base))

    for location, assets in (("high", fitting.high), ("mid", fitting.med), ("low", fitting.low),
                             ("rigs", fitting.rigs), ("subsystem", fitting.subsystems)):
        for asset in assets:
            # Ammo, crystals and scripts loaded in a module share its slot flag. They're
            # consumables aboard, so they count with the cargo, not as fitted items.
            add("cargo" if rules.category(asset.type_id) == CATEGORY_CHARGE else location, asset)
    for asset in fitting.drones:
        add("drones", asset)
    for asset in fitting.fighters:
        # A squadron loaded in a tube is one item with quantity 1 in ESI; count it as full (D22).
        in_tube = (asset.location_flag or "").startswith("FighterTube")
        loaded = asset.is_singleton and in_tube
        add(FIGHTER_TUBES if split_tubes and in_tube else "fighters", asset,
            rules.squadron_size(asset.type_id) if loaded else None)
    for asset in fitting.cargo:
        add("cargo", asset)
    for asset in fitting.fleet_hangar:
        add("cargo", asset)                # cargo and fleet hangar are one pooled space (D2)
    for asset in fitting.fuel_bay:
        add("fuel_bay", asset)
    return found


@dataclass
class InventoryResult:
    shortfalls: List[ItemShortfall] = field(default_factory=list)
    substitutions: List[Substitution] = field(default_factory=list)
    # Items aboard that the fitting doesn't call for at all: (location, type_id, name, quantity)
    unexpected: List[Tuple[str, int, str, int]] = field(default_factory=list)
    required: Counter = field(default_factory=Counter)          # equivalence key -> quantity
    aboard: Counter = field(default_factory=Counter)            # equivalence key -> quantity
    missing_by_key: Counter = field(default_factory=Counter)    # equivalence key -> quantity still missing
    substituted_by_key: Counter = field(default_factory=Counter)  # expected key -> quantity covered by variants
    substitute_used: Counter = field(default_factory=Counter)   # fitted key -> quantity used as a substitute


def evaluate_inventory(expectations: Expectations, aboard_items: List[AboardItem], rules: SdeRules) -> InventoryResult:
    result = InventoryResult()
    names: Dict[int, str] = {}
    types: Dict[int, int] = {}          # equivalence key -> the type the fitting names (for buying)

    for items in expectations.values():
        for item in items.values():
            key = rules.equivalence_key(item.type_id)
            result.required[key] += item.quantity
            names.setdefault(key, item.name)
            types.setdefault(key, item.type_id)
    aboard_names: Dict[int, str] = {}
    aboard_types: Dict[int, int] = {}
    mutated: Dict[int, Counter] = {}    # mutated type's key -> Counter(base -> quantity); None = base unknown
    for item in aboard_items:
        key = rules.equivalence_key(item.type_id)
        result.aboard[key] += item.quantity
        aboard_names.setdefault(key, item.name)
        aboard_types.setdefault(key, item.type_id)
        if rules.is_mutated(item.type_id):
            mutated.setdefault(key, Counter())[item.mutated_base] += item.quantity

    shortfall = Counter({k: q - result.aboard[k] for k, q in result.required.items() if q > result.aboard[k]})
    surplus = Counter({k: q - result.required[k] for k, q in result.aboard.items() if q > result.required[k]})

    # Bling: better versions aboard that the fitting doesn't otherwise need cover a shortfall, as a warning.
    carries_t2_ammo = any(rules.is_tech_ii_charge(t) for t in types.values())
    def covers(expected: int, base: int) -> bool:
        return (rules.equivalence_key(base) == rules.equivalence_key(expected)
                or rules.is_bling(expected, base, carries_t2_ammo))

    for key in sorted(shortfall):
        for spare in sorted(surplus):
            if shortfall[key] <= 0:
                break
            if surplus[spare] <= 0:
                continue
            if spare in mutated:
                # Each mutated item by its own base (M1); unknown bases cover nothing.
                pool = mutated[spare]
                for base in sorted(b for b in pool if b is not None):
                    if shortfall[key] <= 0 or surplus[spare] <= 0:
                        break
                    if pool[base] <= 0 or not covers(types[key], base):
                        continue
                    used = min(shortfall[key], surplus[spare], pool[base])
                    fitted_name = f"{aboard_names[spare]} (from {rules.type_name(base)})"
                    result.substitutions.append(Substitution(types[key], names[key], aboard_types[spare], fitted_name, used))
                    result.substituted_by_key[key] += used
                    result.substitute_used[spare] += used
                    shortfall[key] -= used
                    surplus[spare] -= used
                    pool[base] -= used
                continue
            if not rules.is_bling(types[key], aboard_types[spare], carries_t2_ammo):
                continue
            used = min(shortfall[key], surplus[spare])
            result.substitutions.append(Substitution(types[key], names[key], aboard_types[spare], aboard_names[spare], used))
            result.substituted_by_key[key] += used
            result.substitute_used[spare] += used
            shortfall[key] -= used
            surplus[spare] -= used

    for key in sorted(shortfall):
        if shortfall[key] > 0:
            result.missing_by_key[key] = shortfall[key]
            result.shortfalls.append(ItemShortfall(types[key], names[key], result.required[key],
                                                   result.required[key] - shortfall[key]))

    # Unexpected: items the fitting doesn't call for at all, and that weren't counted as bling.
    remaining_spare = Counter(surplus)
    for item in aboard_items:
        key = rules.equivalence_key(item.type_id)
        if result.required[key] or item.location in UNCHECKED_LOCATIONS or remaining_spare[key] <= 0:
            continue
        quantity = min(item.quantity, remaining_spare[key])
        remaining_spare[key] -= quantity
        name = item.name
        if item.mutated_base is None and rules.is_mutated(item.type_id):
            name += " (base not known yet: it's looked up at the next Pull All)"
        result.unexpected.append((item.location, item.type_id, name, quantity))
    return result
