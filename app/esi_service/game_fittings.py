"""
In-game fitting sync (ESI features plan 29; D4).

    GET    /characters/{id}/fittings                 the character's saved fits   esi-fittings.read_fittings.v1
    POST   /characters/{id}/fittings                 save one: the new ID         esi-fittings.write_fittings.v1
    DELETE /characters/{id}/fittings/{fitting_id}    delete one (204)             esi-fittings.write_fittings.v1

Lab tests (2026-10-07):
- **The list is cached about 5 minutes,** so a fit just saved or deleted doesn't show in it yet. The
  app trusts the replies instead: a save's 201 gives the new ID, a delete's 204 means it's gone.
  What was saved or deleted in this session is laid over the list until the cache catches up.
- **A name over 50 characters is refused (400),** so names are trimmed. A fit the game can't read
  answers 520. Either way that fit is reported and the rest carry on.
- **There's no edit:** replacing is a delete (backed up first) and a save, which gets a new ID.
  The marker at the end of the description, `[EFMT <fit_uid> v<version>]` (D4.6), links any copy
  back to its library fitting.
"""
import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from app import paths
from app.asset_handling.clone_pull import token_scopes
from app.esi_service.base_interfaces import ESIRequest
from app.esi_service.game_client import _AsCharacter
from app.esi_service.esi_settings import ESI_BASE_URL
from app.esi_service.real_esi_client import RealESIClient

READ = "esi-fittings.read_fittings.v1"
WRITE = "esi-fittings.write_fittings.v1"
NAME_LIMIT, DESCRIPTION_LIMIT = 50, 500
LIST_CACHE = 300                    # seconds: how long the game's list can lag behind (lab test)
BACKUP_DAYS = 14                    # D4.2
MARKER = re.compile(r"\[EFMT (\d+) v(\d+)\]\s*$")

# Library section -> the game's slot flag (numbered per module) or bay flag.
SLOT_FLAGS = {"high": "HiSlot", "mid": "MedSlot", "low": "LoSlot", "rigs": "RigSlot", "subsystem": "SubSystemSlot"}
BAY_FLAGS = {"drones": "DroneBay", "fighters": "FighterBay", "cargo": "Cargo"}


def marker(fit_uid: int, version: int) -> str:
    return f"[EFMT {int(fit_uid)} v{int(version)}]"


def read_marker(description: str) -> Optional[Tuple[int, int]]:
    """(fit_uid, version) from a description ending in the marker, or None."""
    found = MARKER.search(description or "")
    return (int(found.group(1)), int(found.group(2))) if found else None


def _section_items(fitting: dict, section: str) -> Dict[int, int]:
    fit = fitting.get("fit", fitting)
    out: Dict[int, int] = {}
    for type_id, entry in (fit.get(section) or {}).items():
        if str(type_id).isdigit():
            quantity = entry.get("quantity", 1) if isinstance(entry, dict) else entry
            out[int(type_id)] = out.get(int(type_id), 0) + int(quantity or 1)
    return out


def to_game(fitting: dict, description: str = "") -> dict:
    """A library fitting as the game saves it: the name trimmed to 50, the marker at the description's end."""
    items: List[dict] = []
    for section, flag in SLOT_FLAGS.items():
        slot = 0
        for type_id, quantity in _section_items(fitting, section).items():
            for _ in range(quantity):
                items.append({"type_id": type_id, "flag": f"{flag}{slot}", "quantity": 1})
                slot += 1
    for section, flag in BAY_FLAGS.items():
        for type_id, quantity in _section_items(fitting, section).items():
            items.append({"type_id": type_id, "flag": flag, "quantity": quantity})
    tag = marker(fitting["fit_uid"], fitting.get("version", 1))
    room = DESCRIPTION_LIMIT - len(tag) - 1
    text = (description or "").strip()[:max(room, 0)]
    return {"name": (fitting.get("fit_name") or fitting.get("hull") or "Fitting")[:NAME_LIMIT],
            "description": (text + " " + tag).strip(), "ship_type_id": int(fitting["hull_type_id"]), "items": items}


def from_game(fit: dict, type_name: Callable[[int], str]) -> dict:
    """A fit saved in the game as a parsed library fitting (what create_fitting takes)."""
    parsed: Dict[str, Any] = {"hull_type_id": int(fit["ship_type_id"]), "hull": type_name(int(fit["ship_type_id"])),
                              "fit_name": fit.get("name") or "Imported fit", "unresolved": []}
    for item in fit.get("items", []):
        flag = item.get("flag", "")
        section = next((s for s, f in SLOT_FLAGS.items() if flag.startswith(f)), None) or \
            next((s for s, f in BAY_FLAGS.items() if flag == f), None)
        if section is None:
            continue        # a flag the library has no section for (none seen in the lab's 228 fits)
        entry = parsed.setdefault(section, {}).setdefault(str(item["type_id"]),
                                                          {"name": type_name(int(item["type_id"])), "quantity": 0})
        entry["quantity"] += int(item.get("quantity", 1))
    return parsed


@dataclass
class Result:
    ok: bool
    message: str
    fitting_id: Optional[int] = None


@dataclass
class _Recent:
    saved: Dict[int, Tuple[float, dict]] = field(default_factory=dict)     # id -> (when, the fit as saved)
    deleted: Dict[int, float] = field(default_factory=dict)                # id -> when


class GameFittings:
    """One character's fits in the game. Calls are made as that character by ID."""

    def __init__(self, auth_service: Any, client_factory: Optional[Callable[[Any], Any]] = None,
                 clock: Callable[[], float] = time.time):
        self._auth = auth_service
        self._factory = client_factory or (lambda auth: RealESIClient(ESI_BASE_URL, auth))
        self._clock = clock
        self._recent: Dict[str, _Recent] = {}

    def name(self, char_id) -> str:
        return self._auth.index.get(str(char_id), f"Character {char_id}")

    def can(self, char_id, write: bool = False) -> bool:
        scopes = token_scopes(self._auth.get_access_token(str(char_id)))
        return READ in scopes and (not write or WRITE in scopes)

    async def _request(self, char_id, request: ESIRequest):
        client = self._factory(_AsCharacter(self._auth, str(char_id)))
        try:
            return await client.request(request)
        finally:
            close = getattr(client, "close", None)
            if close is not None:
                await close()

    async def list(self, char_id) -> Tuple[Optional[List[dict]], str]:
        """The character's fits, with this session's saves and deletes laid over the cached list."""
        if not self.can(char_id):
            return None, f"{self.name(char_id)}'s login doesn't include fittings; log in again (Characters ▸ Add Character)."
        r = await self._request(char_id, ESIRequest(url=f"/characters/{char_id}/fittings"))
        if r.status_code != 200 or not isinstance(r.data, list):
            return None, f"Couldn't read {self.name(char_id)}'s fittings (HTTP {r.status_code})."
        recent = self._recent.get(str(char_id), _Recent())
        fresh = self._clock() - LIST_CACHE
        fits = {int(f["fitting_id"]): f for f in r.data if self._recent_deleted(recent, f["fitting_id"], fresh) is False}
        for fid, (when, fit) in recent.saved.items():
            if when >= fresh and fid not in fits and fid not in recent.deleted:
                fits[fid] = {**fit, "fitting_id": fid}
        return list(fits.values()), ""

    @staticmethod
    def _recent_deleted(recent: _Recent, fitting_id, fresh: float) -> bool:
        when = recent.deleted.get(int(fitting_id))
        return when is not None and when >= fresh

    async def save(self, char_id, body: dict) -> Result:
        if not self.can(char_id, write=True):
            return Result(False, f"{self.name(char_id)}'s login can't save fittings; log in again.")
        r = await self._request(char_id, ESIRequest(url=f"/characters/{char_id}/fittings", method="POST", json=body))
        if r.status_code in (200, 201) and isinstance(r.data, dict) and r.data.get("fitting_id"):
            fid = int(r.data["fitting_id"])
            self._recent.setdefault(str(char_id), _Recent()).saved[fid] = (self._clock(), body)
            return Result(True, f"Saved {body['name']} for {self.name(char_id)}.", fid)
        why = r.data.get("error") if isinstance(r.data, dict) else ""
        return Result(False, f"{body['name']}: not saved (HTTP {r.status_code}{': ' + why if why else ''}).")

    async def delete(self, char_id, fit: dict, backups: "FittingBackups") -> Result:
        """Backs the fit up, then deletes it (D4.2). The backup stays if the delete fails."""
        if not self.can(char_id, write=True):
            return Result(False, f"{self.name(char_id)}'s login can't delete fittings; log in again.")
        path = backups.add(char_id, fit)
        fid = int(fit["fitting_id"])
        r = await self._request(char_id, ESIRequest(url=f"/characters/{char_id}/fittings/{fid}", method="DELETE"))
        if r.status_code in (200, 204):
            self._recent.setdefault(str(char_id), _Recent()).deleted[fid] = self._clock()
            return Result(True, f"Deleted {fit.get('name')} from {self.name(char_id)}'s fittings (backed up).", fid)
        backups.remove(path)
        return Result(False, f"{fit.get('name')}: not deleted (HTTP {r.status_code}).", fid)


class FittingBackups:
    """Deleted fits, kept 14 days in data/fitting_backups/ (D4.2), for Deleted from Game… ▸ Restore."""

    def __init__(self, folder: Optional[Path] = None, clock: Callable[[], datetime] = None):
        self.folder = Path(folder or paths.FITTING_BACKUPS_DIR)
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def add(self, char_id, fit: dict) -> Path:
        now = self._clock()
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self.folder / f"{now:%Y%m%d_%H%M%S}_{int(char_id)}_{int(fit['fitting_id'])}.json"
        path.write_text(json.dumps({"character_id": int(char_id), "deleted_at": now.isoformat(timespec="seconds"),
                                    "fit": fit}, indent=1), encoding="utf-8")
        return path

    def remove(self, path: Path) -> None:
        try:
            Path(path).unlink()
        except OSError:
            pass

    def prune(self) -> int:
        """Removes backups older than 14 days; returns how many."""
        cutoff = self._clock() - timedelta(days=BACKUP_DAYS)
        gone = 0
        for path, entry in self._entries():
            try:
                when = datetime.fromisoformat(entry["deleted_at"])
            except (KeyError, ValueError):
                continue
            if when < cutoff:
                self.remove(path)
                gone += 1
        return gone

    def _entries(self):
        if not self.folder.exists():
            return []
        out = []
        for path in sorted(self.folder.glob("*.json"), reverse=True):
            try:
                out.append((path, json.loads(path.read_text(encoding="utf-8"))))
            except (OSError, ValueError):
                continue
        return out

    def list(self) -> List[Tuple[Path, dict]]:
        """(file, {character_id, deleted_at, fit}), newest first, after pruning."""
        self.prune()
        return self._entries()

    def days_left(self, entry: dict) -> int:
        try:
            when = datetime.fromisoformat(entry["deleted_at"])
        except (KeyError, ValueError):
            return 0
        left = (when + timedelta(days=BACKUP_DAYS) - self._clock()).total_seconds()
        return max(0, -int(-left // 86400))         # whole days, rounded up: 14 on the day it's deleted


def restore_body(fit: dict) -> dict:
    """What to save to bring a backed-up fit back (it gets a new ID; its marker links it again)."""
    return {k: fit[k] for k in ("name", "description", "ship_type_id", "items") if k in fit}
