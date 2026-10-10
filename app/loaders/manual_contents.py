"""
A ship's contents pasted from the game, until the next pull (1.7.4).

    data/generated/manual_contents.json   {"format": 1, "ships": {"<item ID>": {...}}}

Assets ▸ right-click a ship ▸ Update Contents from Game…: paste the ship's inventory as the
game copies it (Name, Group, Location, Quantity, tab-separated, one item per line). It's a way
to check a refit without waiting for ESI, which caches assets for up to an hour.

- Kept by the ship's item ID with the holder it was pasted for and when ({"holder", "at", "items"}).
- Wherever the pulled assets are read (the Assets tab, the Doctrines audit), the ship's own
  contents are replaced by the pasted ones (with_manual_contents). Ships carried inside it, and
  what's in them, stay as pulled: the paste can't say which ship is which.
- The ship is marked unverified (its raw entry gets "manual_at") until a pull of its holder
  replaces the paste (refresh_after_pull): a character's when that character is pulled, a
  corporation's when its hangars are pulled after the paste.
- Slots have no numbers in the paste: modules are numbered in turn, and a charge loaded in a slot
  goes in one of that slot's modules (any one: charges count as cargo either way).
- Private (item IDs): never exported, never in the release snapshot. Full Reset clears it with
  the rest of data/generated.
"""
import json
import logging
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

from app.paths import GENERATED_DIR

logger = logging.getLogger(__name__)

FORMAT = 1
SHIP_CATEGORY, CHARGE_CATEGORY = 6, 8

# The game's location names (lower case) -> ESI location flag. Slots get their numbers in turn.
SLOT_FLAGS = {"high slot": "HiSlot", "medium slot": "MedSlot", "mid slot": "MedSlot", "low slot": "LoSlot",
              "rig slot": "RigSlot", "subsystem slot": "SubSystemSlot", "fighter launch tube": "FighterTube"}
BAY_FLAGS = {"cargo hold": "Cargo", "cargo": "Cargo", "drone bay": "DroneBay", "fighter bay": "FighterBay",
             "fleet hangar": "FleetHangar", "fuel bay": "SpecializedFuelBay", "frigate escape bay": "FrigateEscapeBay",
             "ship maintenance bay": "ShipHangar", "subsystem hold": "SubSystemBay", "subsystem bay": "SubSystemBay"}


@dataclass
class PastedContents:
    items: List[Dict[str, Any]] = field(default_factory=list)  # {type_id, name, location_flag, quantity, is_singleton}
    unknown_names: List[str] = field(default_factory=list)     # lines whose item isn't in the database
    unknown_places: List[str] = field(default_factory=list)    # locations not recognised: their items went in the cargo
    ships_skipped: List[str] = field(default_factory=list)     # carried ships: left as pulled


def _quantity(text: str) -> int:
    digits = re.sub(r"[^\d]", "", text or "")
    return int(digits) if digits else 1


def _cells(line: str) -> List[str]:
    """A pasted line's columns: tab-separated, or (typed by hand) two or more spaces."""
    parts = line.split("\t") if "\t" in line else re.split(r"\s{2,}", line.strip())
    return [p.strip() for p in parts]


def parse_inventory_paste(text: str, sde: Any) -> PastedContents:
    """
    The game's copied inventory (Name, Group, Location, Quantity) as raw items with location flags.
    sde: get_typeid_by_name and get_type_category.
    """
    found = PastedContents()
    modules: Dict[str, List[int]] = {}      # slot flag prefix -> slot numbers given to modules
    charges: List[tuple] = []               # (prefix, type_id, name, quantity), loaded once modules are numbered
    for line in (text or "").splitlines():
        if not line.strip() or set(line.strip()) <= set("-"):
            continue
        cells = _cells(line)
        if cells[0].casefold() == "name":
            continue                         # the header
        name = cells[0]
        place = cells[2].casefold() if len(cells) > 2 else "cargo hold"
        quantity = _quantity(cells[3]) if len(cells) > 3 else 1
        type_id = sde.get_typeid_by_name(name)
        if not type_id:
            found.unknown_names.append(name)
            continue
        category = sde.get_type_category(type_id)
        if category == SHIP_CATEGORY:
            found.ships_skipped.append(name)
            continue
        if place in SLOT_FLAGS:
            prefix = SLOT_FLAGS[place]
            if category == CHARGE_CATEGORY:
                charges.append((prefix, type_id, name, quantity))
                continue
            for _ in range(quantity):
                slot = len(modules.setdefault(prefix, []))
                modules[prefix].append(slot)
                found.items.append({"type_id": type_id, "name": name, "location_flag": f"{prefix}{slot}",
                                    "quantity": 1, "is_singleton": True})
            continue
        flag = BAY_FLAGS.get(place)
        if flag is None:
            found.unknown_places.append(cells[2] if len(cells) > 2 else place)
            flag = "Cargo"
        found.items.append({"type_id": type_id, "name": name, "location_flag": flag, "quantity": quantity,
                            "is_singleton": False})
    turn: Dict[str, int] = {}
    for prefix, type_id, name, quantity in charges:
        slots = modules.get(prefix)
        if slots:
            n = turn.get(prefix, 0)
            turn[prefix] = n + 1
            flag = f"{prefix}{slots[n % len(slots)]}"
        else:
            flag = "Cargo"                   # loaded in a slot the paste lists no module for
        found.items.append({"type_id": type_id, "name": name, "location_flag": flag, "quantity": quantity,
                            "is_singleton": False})
    return found


class ManualContents:
    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path or GENERATED_DIR / "manual_contents.json")
        self.ships: Dict[int, Dict[str, Any]] = {}
        self._lock = threading.RLock()      # Pull All drops entries on its own thread
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.ships = {int(k): v for k, v in (data.get("ships") or {}).items()}
        except (OSError, ValueError, AttributeError, TypeError) as e:
            logger.warning(f"Could not read {self.path.name}: {e}; no pasted ship contents.")
            self.ships = {}

    def save(self) -> None:
        with self._lock:
            if not self.ships:
                if self.path.exists():
                    self.path.unlink()
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps({"format": FORMAT, "ships": {str(k): self.ships[k] for k in sorted(self.ships)}},
                                      indent=2), encoding="utf-8")
            tmp.replace(self.path)

    def get(self, item_id: int) -> Optional[Dict[str, Any]]:
        return self.ships.get(int(item_id))

    def set(self, item_id: int, holder: Dict[str, Any], items: List[Dict[str, Any]], when: str) -> None:
        keep = ("type_id", "location_flag", "quantity", "is_singleton")
        with self._lock:
            self.ships[int(item_id)] = {"holder": {"kind": holder.get("kind"), "id": int(holder.get("id"))},
                                        "at": when, "items": [{k: i[k] for k in keep} for i in items]}
            self.save()

    def remove(self, item_ids: Iterable[int]) -> int:
        with self._lock:
            gone = [i for i in map(int, item_ids) if self.ships.pop(i, None) is not None]
            if gone:
                self.save()
        return len(gone)

    def refresh_after_pull(self, pulled_characters: Iterable[Any], corporations: Iterable[dict]) -> int:
        """Drops the pastes a pull replaced: characters pulled now, corporations pulled after the paste."""
        characters = {int(c) for c in pulled_characters}
        pulled_at = {int(c["corporation_id"]): c.get("pulled_at") or "" for c in corporations
                     if c.get("corporation_id") is not None}
        with self._lock:
            gone = [item_id for item_id, entry in self.ships.items()
                    if (entry["holder"]["kind"] == "character" and int(entry["holder"]["id"]) in characters)
                    or (entry["holder"]["kind"] == "corporation"
                        and pulled_at.get(int(entry["holder"]["id"]), "") > (entry.get("at") or ""))]
        return self.remove(gone)


def synthetic_id(ship_item_id: int, n: int) -> int:
    """An item ID for a pasted item: negative, so it never meets a real one."""
    return -(int(ship_item_id) * 1000 + n)


def with_manual_contents(assets: List[dict], generated_dir: Path, is_ship_type: Callable[[int], bool]) -> List[dict]:
    """
    The pulled assets with each pasted ship's contents in place of what the pull found aboard
    (carried ships and what's in them stay). The ship's entry gets "manual_at". A new list;
    the given one isn't changed.
    """
    manual = ManualContents(Path(generated_dir) / "manual_contents.json")
    if not manual.ships:
        return assets
    by_id = {a.get("item_id"): a for a in assets}
    pasted = {item_id: entry for item_id, entry in manual.ships.items() if item_id in by_id}
    if not pasted:
        return assets
    children: Dict[Any, List[dict]] = {}
    for a in assets:
        children.setdefault(a.get("location_id"), []).append(a)
    dropped = set()
    for ship_id in pasted:
        pending = [c for c in children.get(ship_id, []) if not is_ship_type(c["type_id"])]
        while pending:
            item = pending.pop()
            dropped.add(item.get("item_id"))
            pending.extend(children.get(item.get("item_id"), []))
    out = []
    for a in assets:
        if a.get("item_id") in dropped:
            continue
        if a.get("item_id") in pasted:
            a = dict(a, manual_at=pasted[a["item_id"]].get("at") or "")
        out.append(a)
    for ship_id, entry in pasted.items():
        ship = by_id[ship_id]
        for n, item in enumerate(entry.get("items", [])):
            raw = {"item_id": synthetic_id(ship_id, n), "type_id": int(item["type_id"]), "location_id": ship_id,
                   "location_flag": item.get("location_flag") or "Cargo", "location_type": "item",
                   "quantity": int(item.get("quantity") or 1), "is_singleton": bool(item.get("is_singleton")),
                   "manual": True}
            if ship.get("character_id") is not None:
                raw["character_id"] = ship["character_id"]
            out.append(raw)
    return out
