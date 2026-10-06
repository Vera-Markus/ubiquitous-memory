"""
Every item the app can see, by item ID (TRACKED_ITEMS_DESIGN.md §6, plan step 14.1).

Built once per audit run from the aggregated personal assets (every linked character)
and every corporation pull (data/corp/). It answers "where is item X now, and who holds
it?" and "which ships of hull H are at location L?". Read-only: it never writes back
into all_assets.json, which stays personal.

A sighting gives the holder (a character or a corporation), the top-level location
(station, structure or solar system) and its system, and where aboard the item is, as
text: "hangar", "deliveries", "hangar 2 (Doctrine Subcaps)", "ship maintenance bay of
'Sister Leveling'", outermost first. Capsules aren't ships here (design F3).
"""
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

SHIP_CATEGORY = 6
CAPSULE_GROUP = 29
OFFICE_TYPE = 27            # a corporation office: corp hangar items sit in it

FLAG_TEXT = {
    "Hangar": "hangar", "CorpDeliveries": "deliveries", "Deliveries": "deliveries", "AssetSafety": "asset safety",
    "ShipHangar": "ship maintenance bay", "FleetHangar": "fleet hangar", "Cargo": "cargo", "DroneBay": "drone bay",
    "FighterBay": "fighter bay", "Impounded": "impounded", "FrigateEscapeBay": "escape bay",
}
CONTENTS_FLAGS = ("AutoFit", "Unlocked", "Locked")      # inside a container: just "in '<container>'"


@dataclass(frozen=True)
class Sighting:
    item_id: int
    type_id: int
    custom_name: str
    holder_kind: str            # "character" or "corporation"
    holder_id: int
    holder_name: str            # a corporation's name; characters are named by the caller
    location_id: int            # where the item directly is (an item ID when aboard something)
    location_flag: str
    root_location_id: int       # station, structure or solar system
    system_id: Optional[int]
    aboard: str                 # "hangar", "deliveries / ship maintenance bay of 'Sister Leveling'", ...
    carrier_item_id: Optional[int] = None      # the ship it's packed in, if any
    assembled: bool = True                     # is_singleton: a packaged ship can't be bound
    quantity: int = 1                          # the stack's size (stock to pull, plan 21)

    @property
    def holder(self) -> Dict[str, Any]:
        return {"kind": self.holder_kind, "id": self.holder_id}


def _flag_text(flag: str, divisions: Dict[str, str]) -> str:
    if flag and flag.startswith("CorpSAG") and flag[7:].isdigit():
        n = flag[7:]
        return f"hangar {n} ({divisions[n]})" if divisions.get(n) else f"hangar {n}"
    return FLAG_TEXT.get(flag, flag or "?")


class Universe:
    def __init__(self, sde: Any):
        self.sde = sde                   # EVEdbLoader: type category and group, system_of
        self._items: Dict[int, Sighting] = {}
        self._systems: Dict[int, Optional[int]] = {}

    # --- building ----------------------------------------------------------------------

    @classmethod
    def build(cls, personal_assets: Iterable[dict], corporations: Iterable[dict], sde: Any) -> "Universe":
        universe = cls(sde)
        by_character: Dict[int, List[dict]] = {}
        for asset in personal_assets:
            if isinstance(asset, dict) and asset.get("character_id") is not None:
                by_character.setdefault(int(asset["character_id"]), []).append(asset)
        for char_id, assets in by_character.items():
            universe._add("character", char_id, "", assets, {}, {})
        for corp in corporations:       # after the characters: a corporation copy wins (newer owner)
            universe._add("corporation", int(corp["corporation_id"]), corp.get("name") or "",
                          corp.get("assets", []), corp.get("names", {}), corp.get("divisions", {}))
        return universe

    def _add(self, kind: str, holder_id: int, holder_name: str, assets: List[dict],
             names: Dict[str, str], divisions: Dict[str, str]) -> None:
        by_id = {a["item_id"]: a for a in assets if a.get("item_id") is not None}

        def name_of(item: dict) -> str:
            return item.get("custom_name") or names.get(str(item["item_id"])) or ""

        for item in by_id.values():
            parts: List[str] = []
            carrier = None
            flag, loc = item.get("location_flag") or "", item.get("location_id")
            while loc in by_id:
                parent = by_id[loc]
                if parent["type_id"] == OFFICE_TYPE:            # corp office: the item's flag is its division
                    parts.append(_flag_text(flag, divisions))
                else:
                    label = name_of(parent) or self.sde.get_type_name(parent["type_id"])
                    if flag in CONTENTS_FLAGS:
                        parts.append(f"in '{label}'")
                    else:
                        parts.append(f"{_flag_text(flag, divisions)} of '{label}'")
                    if carrier is None and self._is_ship_type(parent["type_id"]):
                        carrier = parent["item_id"]
                flag, loc = parent.get("location_flag") or "", parent.get("location_id")
                if parent["type_id"] == OFFICE_TYPE:
                    flag = ""                                   # the office's own flag says nothing useful
            if flag:
                parts.append(_flag_text(flag, divisions))
            self._items[item["item_id"]] = Sighting(
                item_id=item["item_id"], type_id=item["type_id"], custom_name=name_of(item),
                holder_kind=kind, holder_id=holder_id, holder_name=holder_name,
                location_id=item.get("location_id"), location_flag=item.get("location_flag") or "",
                root_location_id=loc, system_id=self._system_of(loc),
                aboard=" / ".join(reversed(parts)) if parts else "", carrier_item_id=carrier,
                assembled=bool(item.get("is_singleton")), quantity=int(item.get("quantity") or 1))

    # --- questions ------------------------------------------------------------------------

    def _is_ship_type(self, type_id: int) -> bool:
        return self.sde.get_type_category(type_id) == SHIP_CATEGORY and self.sde.get_type_group(type_id) != CAPSULE_GROUP

    def _system_of(self, location_id: Optional[int]) -> Optional[int]:
        if location_id is None:
            return None
        if location_id not in self._systems:
            self._systems[location_id] = self.sde.system_of(location_id)
        return self._systems[location_id]

    def find(self, item_id: int) -> Optional[Sighting]:
        return self._items.get(item_id)

    def is_ship(self, sighting: Sighting) -> bool:
        return sighting.assembled and self._is_ship_type(sighting.type_id)

    @staticmethod
    def at(sighting: Sighting, system_id: Optional[int], location_id: Optional[int]) -> bool:
        """Whether the item is at a home: the station or structure, and in the system (None means any)."""
        if location_id is not None and sighting.root_location_id != location_id:
            return False
        return system_id is None or sighting.system_id == system_id

    def ships_at(self, type_id: int, system_id: Optional[int], location_id: Optional[int],
                 holder: Optional[Dict[str, Any]] = None) -> List[Sighting]:
        """Assembled ships of the hull at the home, optionally held by one holder; by item ID."""
        return sorted((s for s in self._items.values()
                       if s.type_id == type_id and self.is_ship(s) and self.at(s, system_id, location_id)
                       and (holder is None or s.holder == holder)),
                      key=lambda s: s.item_id)

    def ships_held_by(self, holder: Dict[str, Any]) -> List[Sighting]:
        """Every assembled ship one holder has, wherever it is (aboard others too); by item ID."""
        return sorted((s for s in self._items.values() if s.holder == holder and self.is_ship(s)),
                      key=lambda s: s.item_id)

    def items(self) -> List[Sighting]:
        """Every item seen, by item ID."""
        return [self._items[i] for i in sorted(self._items)]

    def __len__(self) -> int:
        return len(self._items)
