from typing import List, Dict, Any, Optional, Set
from app.models.asset_models import Asset, CarriedShip, ShipAsset, Fitting
from app.models.asset_glossary import classify_location_flag, SlotCategory
from app.models.bay_registry import bay_for_flag
from app.loaders.fitting_loader import EVEdbLoader

SHIP_CATEGORY_ID = 6


def carried_bay_key(flag: Optional[str]) -> str:
    """Where a carried ship sits: a registry bay key, "cargo", "fleet_hangar", or the raw flag."""
    bay = bay_for_flag(flag)
    if bay is not None:
        return bay.key
    return {"Cargo": "cargo", "FleetHangar": "fleet_hangar"}.get(flag or "", flag or "unknown")

# Slot category -> the Fitting list it fills
FITTING_ATTRIBUTE = {
    SlotCategory.HIGH: "high",
    SlotCategory.MEDIUM: "med",
    SlotCategory.LOW: "low",
    SlotCategory.RIGS: "rigs",
    SlotCategory.CARGO: "cargo",
    SlotCategory.DRONES: "drones",
    SlotCategory.SUBSYSTEMS: "subsystems",
    SlotCategory.FIGHTERS: "fighters",
    SlotCategory.FUEL_BAY: "fuel_bay",
    SlotCategory.FLEET_HANGAR: "fleet_hangar",
    SlotCategory.SHIP_MAINTENANCE_BAY: "ship_maintenance_bay",
    SlotCategory.ESCAPE_BAY: "escape_bay",
}


class HierarchyBuilder:
    """
    Turns ESI's flat asset list into a tree. Every item's children (by
    location_id) are bucketed into its Fitting by location_flag.

    A ship is any assembled item of SDE category 6 (Ship), wherever it sits:
    in a hangar, in a container, or carried inside another ship (Ship
    Maintenance Bay, escape bay, ...). Assembled modules, containers and
    deployables are not ships. Packaged ships are items, not ships.
    """

    def __init__(self, raw_assets: List[Dict[str, Any]], sde_loader: EVEdbLoader):
        self.raw_assets = raw_assets
        self.sde = sde_loader
        self.assets_by_id: Dict[int, Asset] = self._build_asset_map()
        self.children_by_location: Dict[int, List[Asset]] = {}
        for asset in self.assets_by_id.values():
            self.children_by_location.setdefault(asset.location_id, []).append(asset)
        self._processed_ids: Set[int] = set()

    def _build_asset_map(self) -> Dict[int, Asset]:
        asset_map = {}
        for item in self.raw_assets:
            item_id = item['item_id']
            type_id = item['type_id']
            asset = Asset(
                item_id=item_id,
                type_id=type_id,
                location_id=item['location_id'],
                location_flag=item.get('location_flag'),
                quantity=item['quantity'],
                is_singleton=item['is_singleton'],
                character_id=item.get('character_id', 0),
                name=self.sde.get_type_name(type_id),
                custom_name=item.get('custom_name')
            )
            asset_map[item_id] = asset
        return asset_map

    def is_ship(self, asset: Asset) -> bool:
        """An assembled item whose SDE category is Ship."""
        return asset.is_singleton and self.sde.get_type_category(asset.type_id) == SHIP_CATEGORY_ID

    def root_location_id(self, asset: Asset) -> int:
        """The station, structure or solar system holding the asset's outermost container."""
        seen: Set[int] = set()
        current = asset
        while current.location_id in self.assets_by_id and current.item_id not in seen:
            seen.add(current.item_id)
            current = self.assets_by_id[current.location_id]
        return current.location_id

    def carrier_item_id(self, asset: Asset) -> Optional[int]:
        """The item ID of the ship directly holding this asset, if it's inside a ship."""
        parent = self.assets_by_id.get(asset.location_id)
        return parent.item_id if parent is not None and self.is_ship(parent) else None

    def _process_asset(self, asset: Asset) -> None:
        """Recursively builds the hierarchy for a given asset."""
        if asset.item_id in self._processed_ids:
            return
        self._processed_ids.add(asset.item_id)

        children = self.children_by_location.get(asset.item_id, [])
        if not children:
            return

        # If there are children, this asset has a fitting
        if asset.fitting is None:
            asset.fitting = Fitting()

        for child in children:
            # Recurse first to build the child's own hierarchy
            self._process_asset(child)

            # Classify where the child goes in the current asset's fitting.
            # Anything that isn't a slot or a registered bay (a container's
            # contents, an unregistered bay) goes in "contents".
            _, category = classify_location_flag(child.location_flag)
            getattr(asset.fitting, FITTING_ATTRIBUTE.get(category, "contents")).append(child)

    def build_ships(self) -> List[ShipAsset]:
        """Every assembled ship in the asset list, including ships carried inside other ships."""
        ships = []
        for asset in self.assets_by_id.values():
            if not self.is_ship(asset):
                continue
            self._process_asset(asset)
            ships.append(ShipAsset(
                asset=asset,
                fitting=asset.fitting if asset.fitting else Fitting(),
                root_location_id=self.root_location_id(asset),
                carrier_item_id=self.carrier_item_id(asset),
            ))
        return ships

    def build_carried_ships(self) -> List[CarriedShip]:
        """
        Every ship (assembled or packaged) directly inside another ship, in any
        bay: the "suitcase" check (design §9.5). A packaged stack is one entry
        with its quantity.
        """
        carried = []
        for asset in self.assets_by_id.values():
            carrier = self.assets_by_id.get(asset.location_id)
            if carrier is None or not self.is_ship(carrier):
                continue
            if self.sde.get_type_category(asset.type_id) != SHIP_CATEGORY_ID:
                continue
            carried.append(CarriedShip(
                item_id=asset.item_id,
                type_id=asset.type_id,
                name=asset.name,
                custom_name=asset.custom_name,
                carrier_item_id=carrier.item_id,
                carrier_name=carrier.name,
                carrier_custom_name=carrier.custom_name,
                bay_key=carried_bay_key(asset.location_flag),
                location_id=self.root_location_id(carrier),
                quantity=asset.quantity,
            ))
        return carried

    def build_hangar(self) -> List[Asset]:
        """Top-level items that aren't ships: anything sitting directly in a station, structure or in space."""
        hangar = []
        for asset in self.assets_by_id.values():
            if asset.location_id in self.assets_by_id or self.is_ship(asset):
                continue
            self._process_asset(asset)
            hangar.append(asset)
        return hangar
