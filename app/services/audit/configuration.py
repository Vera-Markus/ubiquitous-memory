"""
Question 2: is each item aboard where the fitting puts it? (design §9.2-9.3)

Only quantities that are aboard are considered: a missing item is already a
failure and isn't also a refit. The answer is a list of refit moves:
"fit X (from cargo)", "unfit Y (to cargo)", "reconfigure subsystem Z".
Slot numbers never matter; only the slot category does.
"""
from collections import Counter, defaultdict
from typing import Dict, List

from app.loaders.sde_rules import SdeRules
from app.models.audit_models import RefitMove
from app.services.audit.expectations import EXPECTED_LOCATIONS, SLOT_LOCATIONS, Expectations
from app.services.audit.inventory import AboardItem, InventoryResult

# Where moves come from, in preference order: the cargo pool first, then bays, then slots.
SOURCE_ORDER = ("cargo", "fuel_bay", "drones", "fighters") + SLOT_LOCATIONS


def evaluate_configuration(expectations: Expectations, aboard_items: List[AboardItem],
                           inventory: InventoryResult, rules: SdeRules) -> List[RefitMove]:
    expected: Dict[str, Counter] = defaultdict(Counter)
    actual: Dict[str, Counter] = defaultdict(Counter)
    names: Dict[int, str] = {}
    for location, items in expectations.items():
        for item in items.values():
            key = rules.equivalence_key(item.type_id)
            expected[location][key] += item.quantity
            names.setdefault(key, item.name)
    for item in aboard_items:
        key = rules.equivalence_key(item.type_id)
        actual[item.location][key] += item.quantity
        names.setdefault(key, item.name)

    locations = set(expected) | set(actual)
    deficit = {loc: Counter({k: expected[loc][k] - actual[loc][k] for k in expected[loc] if expected[loc][k] > actual[loc][k]})
               for loc in locations}
    excess = {loc: Counter({k: actual[loc][k] - expected[loc][k] for k in actual[loc] if actual[loc][k] > expected[loc][k]})
              for loc in locations}

    # How much of each item is aboard but in the wrong place: its total shortfall across
    # locations, minus what's missing outright and what a variant already stands in for.
    movable = Counter()
    for loc in locations:
        for key, quantity in deficit[loc].items():
            movable[key] += quantity
    for key in list(movable):
        movable[key] = max(0, movable[key] - inventory.missing_by_key[key] - inventory.substituted_by_key[key])

    moves: List[RefitMove] = []
    for destination in EXPECTED_LOCATIONS:
        for key in sorted(deficit.get(destination, ())):
            need = min(deficit[destination][key], movable[key])
            for source in SOURCE_ORDER:
                if need <= 0:
                    break
                if source == destination or excess.get(source, Counter())[key] <= 0:
                    continue
                quantity = min(need, excess[source][key])
                moves.append(RefitMove(key, names[key], quantity, source, destination))
                excess[source][key] -= quantity
                movable[key] -= quantity
                need -= quantity

    # A variant standing in for a doctrine module stays where it is: it's a substitute, not a refit.
    for key, quantity in inventory.substitute_used.items():
        for location in SLOT_LOCATIONS + ("drones", "fighters", "cargo"):
            if quantity <= 0:
                break
            used = min(quantity, excess.get(location, Counter())[key])
            if used:
                excess[location][key] -= used
                quantity -= used

    # Anything left in a fitted slot doesn't belong there: unfit it.
    for location in SLOT_LOCATIONS:
        for key in sorted(excess.get(location, ())):
            if excess[location][key] > 0:
                moves.append(RefitMove(key, names[key], excess[location][key], location, "cargo"))

    # Subsystem changes reshape the whole slot layout, so they come first.
    return sorted(moves, key=lambda m: 0 if "subsystem" in (m.from_location, m.to_location) else 1)
