"""
The base module of each mutated (Abyssal) item (TRACKED_ITEMS_DESIGN.md §9a, plan step 12.1).

A mutated module's type is generic ("Abyssal Warp Scrambler"); what it was made from is
only known per item, from ESI's public endpoint:

    GET /dogma/dynamic/items/{type_id}/{item_id}/     no login; the answer never changes

Each item is looked up once, at Pull All, and kept in data/generated/mutated_items.json:
{item_id: {type_id, source_type_id, mutator_type_id}}. The rolls aren't kept (decision M3:
no stat checks). Private (item IDs), never exported; Full Reset removes it, Clear Asset
Data keeps it (a base never changes).
"""
import json
import logging
from pathlib import Path
from typing import Callable, Dict, Iterable, Optional

from app.esi_service.base_interfaces import ESIRequest
from app.paths import GENERATED_DIR

logger = logging.getLogger("MultiCharAssetPull")       # the pull's logger, so lines reach the GUI log


class MutatedItems:
    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path or GENERATED_DIR / "mutated_items.json")
        self.items: Dict[str, dict] = self._load()

    def _load(self) -> Dict[str, dict]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError) as e:
            logger.warning(f"Could not read {self.path.name}: {e}")
            return {}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.items, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)

    def base_of(self, item_id: int) -> Optional[int]:
        """The type the mutated item was made from, or None if it hasn't been looked up."""
        entry = self.items.get(str(item_id))
        return entry.get("source_type_id") if entry else None

    def unknown(self, assets: Iterable[dict], is_mutated: Callable[[int], bool]) -> Dict[int, int]:
        """item_id -> type_id for every mutated item whose base isn't known yet."""
        return {a["item_id"]: a["type_id"] for a in assets
                if a.get("item_id") is not None and str(a["item_id"]) not in self.items and is_mutated(a["type_id"])}

    async def refresh(self, esi_client, assets: Iterable[dict], is_mutated: Callable[[int], bool],
                      log: Callable[[str], None] = logger.info) -> int:
        """Looks up the base of every mutated item not known yet. Returns how many were added."""
        todo = self.unknown(assets, is_mutated)
        if not todo:
            return 0
        log(f"Looking up the base module of {len(todo)} mutated item(s)...")
        added = failed = 0
        for item_id, type_id in sorted(todo.items()):
            if getattr(esi_client, "paused_for", lambda: 0)() > 0:
                log("Stopping mutated item lookups: ESI's error limit is nearly used up; the rest wait for the next pull.")
                break
            r = await esi_client.request(ESIRequest(url=f"/dogma/dynamic/items/{type_id}/{item_id}/"))
            data = r.data if r.status_code == 200 and isinstance(r.data, dict) else None
            if not data or not data.get("source_type_id"):
                failed += 1
                logger.debug(f"Mutated item {item_id}: HTTP {r.status_code} {r.data}")
                continue
            self.items[str(item_id)] = {"type_id": type_id, "source_type_id": data["source_type_id"],
                                        "mutator_type_id": data.get("mutator_type_id")}
            added += 1
        if added:
            self.save()
        if failed:
            logger.warning(f"{failed} mutated item(s) couldn't be looked up; the audit treats them as unknown "
                           "until a later pull finds their base.")
        return added
