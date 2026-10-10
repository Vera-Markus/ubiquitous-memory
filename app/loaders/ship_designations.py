"""
Each ship's assigned fitting, its designation (Ships tab, plan step 16.1).

    data/generated/ship_designations.json   {"format": 1, "designations": [...]}

Keyed by the ship's item ID, which survives contracts and moves between characters and
corporations (TRACKED_ITEMS_DESIGN.md F1), so a designation follows its ship. Repackaging
or losing the ship gives it a new ID: the old designation then simply matches nothing.

- One fitting per ship; assigning again replaces it.
- An owner per ship (plan 18.2): the character or corporation it belongs to, which decides
  whose requirements it can serve (A2). Set to the holder when the ship is first assigned,
  kept when the fitting changes, overridden with set_owner. Records from before owners
  existed take their recorded holder as owner.
- A Home per ship (homes and priorities plan, H1/H2): {"system_id": N}, or {"anywhere": True}
  for ships that live in space (supers, titans). Set to the ship's system when it's first
  assigned a fitting, kept when the fitting changes, changed with set_home. Ships assigned
  before Homes existed have none (H7) and audit as before until given one.
- <Personal> (S5): a designation with "personal": True and no fitting. The audit, its pokes,
  onboarding and adoption never see the ship.
- A designation whose fitting no longer exists is dropped by prune(), which the Ships
  tab calls with every fitting UID each time it loads (so no deletion path leaves one).
- Clear Library and Full Reset clear the file. Remove Character keeps it: the ships
  may come back to another holder.
- A cache that outlives the ship's absence: when a ship leaves every linked character
  and pulled corporation (given to someone who isn't linked, or pulled out of a corp
  hangar and handed back), its record stays, so its fitting is still there when it
  returns. Each pull marks the ships it saw (last_seen); a record not seen for
  EXPIRY_DAYS is removed (refresh_from_pull).
- Private (item IDs): never exported, never in the release snapshot (D9).
"""
import json
import logging
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Set

from app.paths import GENERATED_DIR

logger = logging.getLogger(__name__)

FORMAT = 1
EXPIRY_DAYS = 30
ANYWHERE = {"anywhere": True}


def home_system(home: Optional[Dict[str, Any]]) -> Optional[int]:
    """A Home's solar system, or None for no Home or Home Anywhere."""
    return int(home["system_id"]) if home and home.get("system_id") is not None else None


def is_anywhere(home: Optional[Dict[str, Any]]) -> bool:
    return bool(home and home.get("anywhere"))


class ShipDesignations:
    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path or GENERATED_DIR / "ship_designations.json")
        self.designations: Dict[int, Dict[str, Any]] = {}
        self._lock = threading.RLock()      # Pull All updates last_seen on its own thread
        self._load()

    # --- file ----------------------------------------------------------------------------

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            for d in data.get("designations", []):
                d.setdefault("owner", dict(d.get("holder") or {}))      # Phase 16 records: the holder then
                self.designations[int(d["item_id"])] = d
        except (OSError, ValueError, KeyError, TypeError) as e:
            logger.warning(f"Could not read {self.path.name}: {e}; starting with no ship designations.")
            self.designations = {}

    def save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            data = {"format": FORMAT, "designations": [self.designations[k] for k in sorted(self.designations)]}
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            tmp.replace(self.path)

    # --- reading -------------------------------------------------------------------------

    def get(self, item_id: int) -> Optional[Dict[str, Any]]:
        return self.designations.get(int(item_id))

    def fit_uid(self, item_id: int) -> Optional[int]:
        d = self.get(item_id)
        return d.get("fit_uid") if d else None

    def home(self, item_id: int) -> Optional[Dict[str, Any]]:
        d = self.get(item_id)
        return d.get("home") if d else None

    def is_personal(self, item_id: int) -> bool:
        d = self.get(item_id)
        return bool(d and d.get("personal"))

    def owner(self, item_id: int) -> Optional[Dict[str, Any]]:
        d = self.get(item_id)
        return d.get("owner") if d else None

    def assigned_to(self, fit_uid: int, owner: Dict[str, Any]) -> List[Dict[str, Any]]:
        """The ships assigned this fitting that belong to this owner, by item ID (plan 18.3)."""
        want = {"kind": owner.get("kind"), "id": int(owner.get("id"))}
        return [d for _, d in sorted(self.designations.items())
                if d.get("fit_uid") == int(fit_uid) and {"kind": (d.get("owner") or {}).get("kind"),
                                                     "id": int((d.get("owner") or {}).get("id") or 0)} == want]

    def record_sighting(self, item_id: int, where: str, holder: Optional[Dict[str, Any]]) -> None:
        """Where the audit last found the ship, for "Missing since …: last seen …". Saved by save()."""
        with self._lock:
            d = self.designations.get(int(item_id))
            if d is not None:
                d["seen_where"], d["seen_holder"] = where, holder

    def __len__(self) -> int:
        return len(self.designations)

    # --- changing -------------------------------------------------------------------------

    def assign(self, item_id: int, type_id: int, fit_uid: int, holder: Dict[str, Any], custom_name: str,
               when: str, system_id: Optional[int] = None) -> Dict[str, Any]:
        """
        Gives the ship a fitting, replacing any earlier one (or <Personal>). The Home stays when
        the fitting changes; a ship without one gets system_id, the system it's in (H2).
        """
        holder = {"kind": holder.get("kind"), "id": holder.get("id")}
        earlier = self.get(item_id)
        designation = {"item_id": int(item_id), "type_id": int(type_id), "fit_uid": int(fit_uid),
                       "holder": holder,
                       "owner": dict(earlier["owner"]) if earlier and earlier.get("owner") else dict(holder),
                       "custom_name": custom_name or "", "assigned_at": when,
                       "last_seen": when}           # it's on screen, so it was in the last pull
        home = (earlier or {}).get("home") or ({"system_id": int(system_id)} if system_id is not None else None)
        if home:
            designation["home"] = dict(home)
        with self._lock:
            self.designations[int(item_id)] = designation
            self.save()
        return designation

    def mark_personal(self, item_id: int, type_id: int, holder: Dict[str, Any], custom_name: str,
                      when: str) -> Dict[str, Any]:
        """<Personal> (S5): the ship has no fitting and the audit never sees it. Replaces a fitting."""
        holder = {"kind": holder.get("kind"), "id": holder.get("id")}
        earlier = self.get(item_id)
        designation = {"item_id": int(item_id), "type_id": int(type_id), "fit_uid": None, "personal": True,
                       "holder": holder,
                       "owner": dict(earlier["owner"]) if earlier and earlier.get("owner") else dict(holder),
                       "custom_name": custom_name or "", "assigned_at": when, "last_seen": when}
        with self._lock:
            self.designations[int(item_id)] = designation
            self.save()
        return designation

    def set_home(self, item_ids: Iterable[int], home: Optional[Dict[str, Any]]) -> int:
        """
        Gives ships with a fitting a Home: {"system_id": N}, ANYWHERE, or None for none.
        Personal ships have no Home. Returns how many changed.
        """
        home = None if not home else (dict(ANYWHERE) if is_anywhere(home) else {"system_id": home_system(home)})
        with self._lock:
            changed = 0
            for item_id in map(int, item_ids):
                d = self.designations.get(item_id)
                if d is None or d.get("personal") or d.get("home") == home:
                    continue
                if home is None:
                    d.pop("home", None)
                else:
                    d["home"] = dict(home)
                changed += 1
            if changed:
                self.save()
        return changed

    def set_owner(self, item_ids: Iterable[int], owner: Dict[str, Any]) -> int:
        """Gives assigned ships an owner (a linked character or a corporation). Returns how many changed."""
        owner = {"kind": owner.get("kind"), "id": owner.get("id")}
        with self._lock:
            changed = 0
            for item_id in map(int, item_ids):
                d = self.designations.get(item_id)
                if d is not None and d.get("owner") != owner:
                    d["owner"] = dict(owner)
                    changed += 1
            if changed:
                self.save()
        return changed

    def unassign(self, item_ids: Iterable[int]) -> int:
        with self._lock:
            gone = [i for i in map(int, item_ids) if self.designations.pop(i, None) is not None]
            if gone:
                self.save()
        return len(gone)

    def refresh_seen(self, seen_item_ids: Set[int], now: str, days: int = EXPIRY_DAYS) -> List[Dict[str, Any]]:
        """
        After a pull: every record whose ship was in it gets last_seen = now; records not
        seen for `days` are removed. Returns the removed records.
        """
        cutoff = datetime.fromisoformat(now) - timedelta(days=days)
        with self._lock:
            for item_id, d in self.designations.items():
                if item_id in seen_item_ids:
                    d["last_seen"] = now
            expired = [d for d in self.designations.values()
                       if datetime.fromisoformat(d.get("last_seen") or d.get("assigned_at") or now) < cutoff]
            for d in expired:
                del self.designations[d["item_id"]]
            self.save()
        return expired

    def prune(self, existing_fit_uids: Iterable[int]) -> int:
        """Drops the designations whose fitting is gone. Returns how many went."""
        keep = {int(u) for u in existing_fit_uids}
        return self.unassign([i for i, d in self.designations.items()
                              if not d.get("personal") and d.get("fit_uid") not in keep])

    def clear(self) -> None:
        with self._lock:
            self.designations = {}
            if self.path.exists():
                self.path.unlink()


def pulled_item_ids(generated_dir: Path, corp_dir: Path) -> Set[int]:
    """Every item ID in the aggregated personal assets and every corporation pull."""
    ids: Set[int] = set()
    sources = [Path(generated_dir) / "all_assets.json"] + sorted(Path(corp_dir).glob("*.json"))
    for path in sources:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        assets = data.get("assets", []) if isinstance(data, dict) else data
        ids |= {a["item_id"] for a in assets if isinstance(a, dict) and a.get("item_id") is not None}
    return ids


def refresh_from_pull(designations: ShipDesignations, generated_dir: Path, corp_dir: Path,
                      log: Callable[[str], None], now: Optional[str] = None, noun: str = "ship") -> None:
    """Marks the ships (or containers: noun) this pull saw and removes records not seen for EXPIRY_DAYS (logged by name)."""
    now = now or datetime.now(timezone.utc).isoformat(timespec="seconds")
    seen = pulled_item_ids(generated_dir, corp_dir)
    if not seen:
        return          # no assets at all (a failed pull): don't age anything
    expired = designations.refresh_seen(seen, now)
    away = sum(1 for i in designations.designations if i not in seen)
    if away:
        log(f"[INFO] {away} {noun}(s) with an assigned fitting weren't in this pull; their fittings are kept "
            f"for {EXPIRY_DAYS} days in case they come back.")
    if expired:
        names = ", ".join(d.get("custom_name") or f"item {d['item_id']}" for d in expired)
        log(f"[INFO] Forgot the fitting of {len(expired)} {noun}(s) not seen for {EXPIRY_DAYS} days: {names}")
