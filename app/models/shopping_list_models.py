from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class ShoppingListItem:
    """
    One item to buy. `alternatives` are its identical-stat twins (D11): a
    buyer facing a sold-out market can buy any of them instead. The quantity
    is never split between them.
    """
    item_name: str
    quantity: int
    type_id: Optional[int] = None
    alternatives: List[str] = field(default_factory=list)

    def line(self) -> str:
        """
        "Helium Isotopes x25000": the line Copy and Export produce, ready for the game's
        Multibuy. Only the fitting's own item and no thousands comma; the alternatives
        are shown in the Audit tab's list, not pasted.
        """
        return f"{self.item_name} x{self.quantity}"


@dataclass
class CharacterShoppingList:
    """
    A shopping list for a specific character.
    """
    character_id: int
    character_name: str
    missing_items: List[ShoppingListItem] = field(default_factory=list)


@dataclass
class DoctrineShoppingList:
    """
    A shopping list for a specific doctrine.
    """
    doctrine_uid: int
    doctrine_name: str
    missing_items: List[ShoppingListItem] = field(default_factory=list)


@dataclass
class FleetShoppingList:
    """
    An aggregated shopping list for the entire fleet.
    """
    missing_items: List[ShoppingListItem] = field(default_factory=list)


@dataclass
class ShoppingList:
    """
    A container for all types of shopping lists generated from an audit.
    """
    character_lists: List[CharacterShoppingList] = field(default_factory=list)
    doctrine_lists: List[DoctrineShoppingList] = field(default_factory=list)
    fleet_list: FleetShoppingList = field(default_factory=FleetShoppingList)
