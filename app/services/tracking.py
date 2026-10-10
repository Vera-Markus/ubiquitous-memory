"""
What the audit needs to follow assigned ships (UI_THOUGHTS_PLAN.md 18.3; first built for
Phase 14's bindings, which it replaced).

One TrackingContext per audit run: the universe (every item by ID, across linked characters
and corporations), the ship designations (each ship's fitting and owner), and each holder's
ships, built only when an assigned ship turns out to be held by someone other than the
character being audited. The Ships tab uses one too.
"""
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.loaders.hierarchy_builder import HierarchyBuilder
from app.loaders.manual_contents import SHIP_CATEGORY, with_manual_contents
from app.models.asset_models import CarriedShip, ShipAsset
from app.services.audit_collection_service import annotate_mutated
from app.services.universe import Sighting, Universe

Holder = Tuple[str, int]


class TrackingContext:
    def __init__(self, universe: Universe, designations: Any, sde: Any, generated_dir: Path,
                 holder_assets: Dict[Holder, List[dict]], character_names: Optional[Dict[str, str]] = None,
                 now: Optional[str] = None):
        self.universe = universe
        self.designations = designations        # ShipDesignations, or None (the Ships tab passes its own)
        self.sde = sde
        self.generated_dir = Path(generated_dir)
        self._holder_assets = holder_assets
        self._built: Dict[Holder, Tuple[Dict[int, ShipAsset], List[CarriedShip]]] = {}
        self._inside: Dict[Holder, Dict[int, List[dict]]] = {}      # holder -> location item ID -> its items
        self.character_names = {str(k): v for k, v in (character_names or {}).items()}
        self.now = now or datetime.now(timezone.utc).isoformat(timespec="seconds")
        # Ships whose contents were pasted from the game (1.7.4): item ID -> when
        self.manual_at: Dict[int, str] = {a["item_id"]: a["manual_at"] for assets in holder_assets.values()
                                          for a in assets if a.get("manual_at")}

    @classmethod
    def load(cls, generated_dir: Path, corporations: List[dict], sde: Any, designations: Any = None,
             character_names: Optional[Dict[str, str]] = None, now: Optional[str] = None) -> "TrackingContext":
        """The universe and each holder's assets, from all_assets.json and the corporation pulls."""
        generated_dir = Path(generated_dir)
        path = generated_dir / "all_assets.json"
        personal = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []

        # Ships whose contents were pasted from the game since the last pull (1.7.4)
        def is_ship_type(type_id):
            return sde.get_type_category(type_id) == SHIP_CATEGORY
        personal = with_manual_contents(personal, generated_dir, is_ship_type)
        corporations = [dict(c, assets=with_manual_contents(c.get("assets", []), generated_dir, is_ship_type))
                        for c in corporations]
        holder_assets: Dict[Holder, List[dict]] = {}
        for a in personal:
            if isinstance(a, dict) and a.get("character_id") is not None:
                holder_assets.setdefault(("character", int(a["character_id"])), []).append(a)
        for corp in corporations:
            cid = int(corp["corporation_id"])
            holder_assets[("corporation", cid)] = [dict(a, character_id=cid) for a in corp.get("assets", [])]
        return cls(Universe.build(personal, corporations, sde), designations, sde, generated_dir, holder_assets,
                   character_names, now)

    build = load

    # --- holders --------------------------------------------------------------------------

    def holder_info(self, sighting: Sighting) -> Dict[str, Any]:
        name = sighting.holder_name if sighting.holder_kind == "corporation" else \
            self.character_names.get(str(sighting.holder_id), f"Character {sighting.holder_id}")
        return {"kind": sighting.holder_kind, "id": sighting.holder_id, "name": name}

    def where(self, sighting: Sighting) -> str:
        place = self.sde.location_label(sighting.root_location_id)
        return f"{place} ({sighting.aboard})" if sighting.aboard else place

    def ships_of(self, sighting: Sighting) -> Tuple[Optional[ShipAsset], List[CarriedShip], Dict[int, ShipAsset]]:
        """The ship as the audit sees it (with its fitting), from its holder's own assets."""
        holder = (sighting.holder_kind, sighting.holder_id)
        if holder not in self._built:
            raw = [dict(a) for a in self._holder_assets.get(holder, [])]
            annotate_mutated(raw, self.generated_dir)
            hierarchy = HierarchyBuilder(raw, self.sde)
            self._built[holder] = ({s.asset.item_id: s for s in hierarchy.build_ships()}, hierarchy.build_carried_ships())
        ships, carried = self._built[holder]
        return ships.get(sighting.item_id), carried, ships

    def contents_of(self, sighting: Sighting) -> List[dict]:
        """The items directly inside an item (a container), from its holder's own assets (1.7.4)."""
        holder = (sighting.holder_kind, sighting.holder_id)
        if holder not in self._inside:
            raw = [dict(a) for a in self._holder_assets.get(holder, [])]
            annotate_mutated(raw, self.generated_dir)
            inside: Dict[int, List[dict]] = {}
            for a in raw:
                inside.setdefault(a.get("location_id"), []).append(a)
            self._inside[holder] = inside
        return list(self._inside[holder].get(sighting.item_id, []))
