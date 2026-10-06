import json
from pathlib import Path
from datetime import datetime, timezone
from typing import Set
from app.models.asset_models import AuditSnapshot
from app.loaders.doctrine_manager import DoctrineManager
from app.loaders.hierarchy_builder import HierarchyBuilder
from app.loaders.fitting_loader import EVEdbLoader

class AuditCollectionService:
    """
    Service responsible for gathering all necessary data for an audit.
    It aggregates data from local files (assets) and local managers (doctrines/roles).
    """

    def __init__(self, doctrine_manager: DoctrineManager, sde_loader: EVEdbLoader, generated_dir: Path):
        self.doctrine_manager = doctrine_manager
        self.sde_loader = sde_loader
        self.generated_dir = Path(generated_dir)

    async def collect_audit_snapshot(self, character_id: int) -> AuditSnapshot:
        """
        Collects a complete snapshot of a character's current state for auditing.
        
        Args:
            character_id: The EVE Online character ID.
        
        Returns:
            An AuditSnapshot containing assets, ships, systems, stations, and assigned roles.
        """
        # 1. Load raw assets from local JSON
        assets_path = self.generated_dir / "all_assets.json"
        with open(assets_path, 'r') as f:
            all_raw_assets = json.load(f)
        
        # Filter assets to only those belonging to this character_id
        raw_assets = [a for a in all_raw_assets if a.get('character_id') == character_id]
        
        # 2. Ships (with their fittings), loose hangar items, and ships packed inside
        # other ships across the whole inventory (design §9.5)
        hierarchy = HierarchyBuilder(raw_assets, self.sde_loader)
        ships = hierarchy.build_ships()
        hangar = hierarchy.build_hangar()
        carried_ships = hierarchy.build_carried_ships()
        
        # 3. Classify each top-level location. NPC stations and solar systems
        # (ships logged off in space) come from the SDE. Anything else is a
        # player structure, which the engine names through the location cache.
        systems: Set[int] = set()
        stations: Set[int] = set()

        root_locations = {asset.location_id for asset in hangar if asset.location_id}
        root_locations |= {ship.root_location_id for ship in ships if ship.root_location_id}
        for location_id in root_locations:
            if self.sde_loader.is_station(location_id):
                stations.add(location_id)
            elif self.sde_loader.is_solar_system(location_id):
                systems.add(location_id)

        # 4. Identify all roles assigned to this character across all doctrines
        assigned_role_uids: Set[int] = set()
        for doctrine in self.doctrine_manager.doctrines.values():
            assignments = doctrine.get('character_assignments', {})
            for role_uid, char_list in assignments.items():
                if str(character_id) in char_list:
                    assigned_role_uids.add(int(role_uid))

        return AuditSnapshot(
            character_id=character_id,
            snapshot_time=datetime.now(timezone.utc).isoformat(),
            assets=hangar,
            ships=ships,
            systems=list(systems),
            stations=list(stations),
            assigned_role_uids=list(assigned_role_uids),
            carried_ships=carried_ships,
        )
