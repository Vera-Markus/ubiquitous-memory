import json
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, Set
from app.asset_handling.skill_pull import load_levels
from app import paths
from app.models.asset_models import AuditSnapshot
from app.loaders.doctrine_manager import DoctrineManager
from app.loaders.hierarchy_builder import HierarchyBuilder
from app.loaders.manual_contents import SHIP_CATEGORY, with_manual_contents
from app.loaders.fitting_loader import EVEdbLoader
from app.asset_handling.mutated_items import MutatedItems

def annotate_mutated(raw_assets, generated_dir: Path) -> None:
    """Each mutated item's base module, where Pull All has looked it up (plan 12.1-12.2)."""
    mutated = MutatedItems(Path(generated_dir) / "mutated_items.json")
    for a in raw_assets:
        base = mutated.base_of(a.get('item_id'))
        if base:
            a['mutated_base'] = base


class AuditCollectionService:
    """
    Service responsible for gathering all necessary data for an audit.
    It aggregates data from local files (assets) and local managers (doctrines/roles).
    """

    def __init__(self, doctrine_manager: DoctrineManager, sde_loader: EVEdbLoader, generated_dir: Path,
                 clones_dir: Optional[Path] = None):
        self.doctrine_manager = doctrine_manager
        self.sde_loader = sde_loader
        self.generated_dir = Path(generated_dir)
        self.clones_dir = clones_dir        # None: the app's data/clones, looked up when used
        # Skills sit beside clones (plan 26.3): with a clones folder given, its sibling; else the app's.
        self.skills_dir = Path(clones_dir).parent / "skills" if clones_dir else None

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
        # Ships whose contents were pasted from the game since the last pull (1.7.4)
        all_raw_assets = with_manual_contents(all_raw_assets, self.generated_dir,
                                              lambda t: self.sde_loader.get_type_category(t) == SHIP_CATEGORY)
        
        # Filter assets to only those belonging to this character_id
        raw_assets = [a for a in all_raw_assets if a.get('character_id') == character_id]
        annotate_mutated(raw_assets, self.generated_dir)
        
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
            clones=self._clones(character_id),
            skills=load_levels(character_id, self.skills_dir or paths.SKILLS_DIR),
        )

    def _clones(self, character_id: int):
        """The character's pulled clones and implants (plan 15.2); None when there's no file yet."""
        path = Path(self.clones_dir or paths.CLONES_DIR) / f"{character_id}.json"
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
