"""
Shopping lists from the audit's structured results (design §10.2, defect F9).

Only what's genuinely missing is bought: a ship's item shortfalls (fitting,
cargo, fleet hangar and fuel requirements) and ships short in its
maintenance bay (D19). Refits, substitutes, unexpected items and escape bay
problems never are, because everything they involve is already aboard or is
only a warning. Quantities are the real shortfalls (25,000 isotopes, not +1
per click), and identical-stat twins are named as alternatives (D11).

Per requirement, only a requirement that isn't ready needs buying for, and
then only for its best listed ship: one ready ship is enough (D13). A
requirement with no ship of its hull at its location needs a whole
replacement ship; that's only bought when asked for on that requirement
(items_for_requirement), never as part of a whole audit's lists.
"""
from collections import defaultdict
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from app.models.audit_models import AuditResult, ItemShortfall, RequirementResult, RequirementStatus, ShipRequirementResult
from app.services.audit.expectations import build_expectations
from app.services.implant_rules import is_implant_set
from app.models.shopping_list_models import (CharacterShoppingList, DoctrineShoppingList, FleetShoppingList,
                                             ShoppingList, ShoppingListItem)

BOUGHT_BAYS = ("ship_maintenance_bay",)     # bay rows whose shortfalls are bought; the fuel bay's are already item shortfalls

Needs = Dict[int, Tuple[str, int]]          # type ID -> (name, quantity)


class ShoppingListService:
    def __init__(self, rules=None, type_name: Optional[Callable[[int], str]] = None):
        """`rules` (SdeRules) and `type_name` name the twins; without them no alternatives are listed."""
        self.rules = rules
        self.type_name = type_name

    # --- one ship -----------------------------------------------------------------------

    @staticmethod
    def ship_needs(ship: ShipRequirementResult) -> Needs:
        """What this ship is missing, by type."""
        needs: Needs = {}
        shortfalls: List[ItemShortfall] = list(ship.shortfalls)
        for bay in ship.bay_results:
            if bay.bay_key in BOUGHT_BAYS:
                shortfalls.extend(bay.shortfalls)
        for s in shortfalls:
            if s.missing > 0:
                name, quantity = needs.get(s.type_id, (s.name, 0))
                needs[s.type_id] = (name, quantity + s.missing)
        return needs

    def items_for_ship(self, ship: ShipRequirementResult) -> List[ShoppingListItem]:
        return self.items(self.ship_needs(ship))

    @staticmethod
    def hull_needs(requirement: RequirementResult) -> Needs:
        """A requirement with no ship at its location needs a replacement: the hull, its fit and metadata."""
        if requirement.ship_results:
            return {}
        return {s.type_id: (s.name, s.missing) for s in requirement.shortfalls if s.missing > 0}

    def items_for_requirement(self, requirement: RequirementResult) -> List[ShoppingListItem]:
        return self.items(self.hull_needs(requirement))

    # --- whole audits -------------------------------------------------------------------

    @staticmethod
    def _character_needs(results: Iterable[AuditResult]) -> Dict[int, Dict[object, Needs]]:
        """
        character -> ship item ID -> needs. A ship needed by several
        requirements is bought for once (D14).
        """
        found: Dict[int, Dict[object, Needs]] = defaultdict(dict)
        for result in results:
            for requirement in result.requirement_results:
                # A missing ship's replacement is only bought from its own requirement row.
                if requirement.status != RequirementStatus.FAIL or not requirement.ship_results:
                    continue
                best = requirement.ship_results[0]
                ship_key = best.ship_item_id if best.ship_item_id is not None else id(best)
                needs = ShoppingListService.ship_needs(best)
                merged = found[result.character_id].setdefault(ship_key, {})
                for type_id, (name, quantity) in needs.items():
                    previous = merged.get(type_id, (name, 0))[1]
                    merged[type_id] = (name, max(previous, quantity))
        return found

    def generate_shopping_lists(self, audit_results: List[AuditResult],
                                character_names: Optional[Dict[int, str]] = None) -> ShoppingList:
        """Character, doctrine and fleet lists for a set of audit results."""
        character_names = character_names or {}
        shopping = ShoppingList()
        by_character = self._character_needs(audit_results)

        fleet: Needs = {}
        for character_id in sorted(by_character):
            needs = _sum(by_character[character_id].values())
            if needs:
                shopping.character_lists.append(CharacterShoppingList(
                    character_id, character_names.get(character_id, str(character_id)), self.items(needs)))
                fleet = _sum([fleet, needs])

        doctrines: Dict[int, Tuple[str, List[AuditResult]]] = {}
        for result in audit_results:
            if result.doctrine_uid is not None:
                doctrines.setdefault(result.doctrine_uid, (result.doctrine_name or str(result.doctrine_uid), []))[1].append(result)
        for doctrine_uid in sorted(doctrines):
            name, results = doctrines[doctrine_uid]
            needs = _sum(ship for ships in self._character_needs(results).values() for ship in ships.values())
            if needs:
                shopping.doctrine_lists.append(DoctrineShoppingList(doctrine_uid, name, self.items(needs)))

        shopping.fleet_list = FleetShoppingList(self.items(fleet))
        return shopping

    # --- items ----------------------------------------------------------------------------

    def items(self, needs: Needs) -> List[ShoppingListItem]:
        """
        The needs as list items. Mutated modules never go on a shopping list (decision M4,
        TRACKED_ITEMS_DESIGN.md §9a): their wanted base and bonuses are shared as text.
        """
        return sorted((ShoppingListItem(name, quantity, type_id, self.alternatives(type_id))
                       for type_id, (name, quantity) in needs.items()
                       if not (self.rules is not None and self.rules.is_mutated(type_id))),
                      key=lambda i: i.item_name.casefold())

    def alternatives(self, type_id: int) -> List[str]:
        if self.rules is None or self.type_name is None:
            return []
        return [self.type_name(t) for t in self.rules.equivalent_types(type_id) if t != type_id]


def _sum(needs_list: Iterable[Needs]) -> Needs:
    total: Needs = {}
    for needs in needs_list:
        for type_id, (name, quantity) in needs.items():
            total[type_id] = (total.get(type_id, (name, 0))[0], total.get(type_id, (name, 0))[1] + quantity)
    return total


def item_key(item: ShoppingListItem) -> object:
    """What identifies an item on the list: its type, or its name when the type isn't known."""
    return item.type_id if item.type_id is not None else item.item_name


def without_items(items: List[ShoppingListItem], keys) -> List[ShoppingListItem]:
    """The list without the items whose item_key is in keys (UI rework step 6.1)."""
    keys = set(keys)
    return [item for item in items if item_key(item) not in keys]


def fitting_items(fitting: Dict, is_mutated: Optional[Callable[[int], bool]] = None) -> List[ShoppingListItem]:
    """
    Everything to buy for one ship of a fitting: the hull, every item in its
    EFT (slots, drones, fighters, cargo) and its doctrine requirements' items
    (fleet hangar, fuel bay), summed by type. Ships in the ship maintenance
    bay and escape bay are left out, and so are mutated modules (M4) when
    is_mutated (SdeRules.is_mutated) is given.
    """
    needs: Needs = {}
    hull = fitting.get("hull")
    if hull and not is_implant_set(fitting):        # an implant set's Capsule isn't bought
        needs[fitting.get("hull_type_id") or -1] = (hull, 1)
    for items in build_expectations(fitting).values():
        _merge_need(needs, items)
    return [ShoppingListItem(name, quantity, type_id if type_id != -1 else None)
            for type_id, (name, quantity) in needs.items()
            if not (is_mutated is not None and type_id != -1 and is_mutated(type_id))]


def _merge_need(needs: Needs, items) -> None:
    for item in items.values():
        name, quantity = needs.get(item.type_id, (item.name, 0))
        needs[item.type_id] = (name, quantity + item.quantity)


def items_text(items: List[ShoppingListItem]) -> str:
    """The list as text, one line per item: what Copy (and Export) produce."""
    return "\n".join(item.line() for item in items)


def merge_items(current: List[ShoppingListItem], added: List[ShoppingListItem]) -> List[ShoppingListItem]:
    """Add one list to another, summing quantities by type."""
    by_type: Dict[object, ShoppingListItem] = {}
    for item in list(current) + list(added):
        key = item_key(item)
        if key in by_type:
            by_type[key] = ShoppingListItem(by_type[key].item_name, by_type[key].quantity + item.quantity,
                                            item.type_id, by_type[key].alternatives or item.alternatives)
        else:
            by_type[key] = ShoppingListItem(item.item_name, item.quantity, item.type_id, list(item.alternatives))
    return sorted(by_type.values(), key=lambda i: i.item_name.casefold())
