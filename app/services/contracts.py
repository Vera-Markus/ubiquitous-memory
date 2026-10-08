"""
Contracts for the audit and shopping list (ESI features plan 28.2-28.5), read from what
contract_pull saved.

- **Own contracts as stock (28.2):** a linked character's live item exchange holds items they
  still own; they count as stock in its system, "in contract", before anything is bought.
- **Alliance contracts (28.3):** live item exchanges assigned to a linked character's
  corporation or alliance, offered for a hard requirement whose whole ship is missing (D8.1),
  in the requirement's system with its own station first (D8.2):
  - **exact:** the hull and every slot-fitted module of the fitting (extras are fine);
  - **near:** the hull and at least 90% of the slot-fitted modules (O4), with what's missing or extra;
  - a contract that asks for items in return (PLEX, say) is left out.
- **The choice (28.4):** Use This Contract gives the requirement a 2-hour pass, saved in
  data/config/contract_choices.json. It ends early when the ship turns up, or the contract
  is gone; a contract one of your characters accepted keeps it until the ship is in assets.
"""
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from app import paths
from app.asset_handling.contract_pull import is_live, load_contracts, load_items

NEAR = 0.9                  # O4: near fits hold at least 90% of the slot-fitted modules
PASS_HOURS = 2              # D8.3
CHOICES_FILE = "contract_choices.json"
SLOT_SECTIONS = ("high", "mid", "low", "rigs", "subsystem")
EXACT, NEAR_FIT, HULL = "exact", "near", "hull"


@dataclass
class Contract:
    contract_id: int
    source: str                 # "alliance", "corporation" or "own"
    price: float
    location_id: int
    system_id: Optional[int]
    title: str
    expires: str
    issuer_id: int
    items: Dict[int, int]       # included items: type ID -> quantity
    asks: bool                  # asks for items in return


@dataclass
class Offer:
    contract: Contract
    kind: str                   # EXACT, NEAR_FIT or HULL
    missing: Dict[int, int] = field(default_factory=dict)
    extra: Dict[int, int] = field(default_factory=dict)
    at_station: bool = False
    label: str = ""                         # a capital's match (C3): "Hull + rigs", another fitting's name…
    light_years: Optional[float] = None     # a capital's distance from the requirement's system

    @property
    def sort_key(self):
        return (not self.at_station, (EXACT, NEAR_FIT, HULL).index(self.kind), self.contract.price,
                self.contract.contract_id)


def slot_modules(fitting: dict) -> Dict[int, int]:
    """The fitting's slot-fitted modules (hard, D1.1): type ID -> quantity."""
    fit = fitting.get("fit", fitting)
    out: Dict[int, int] = {}
    for section in SLOT_SECTIONS:
        for type_id, entry in (fit.get(section) or {}).items():
            if str(type_id).isdigit():
                quantity = entry.get("quantity", 1) if isinstance(entry, dict) else entry
                out[int(type_id)] = out.get(int(type_id), 0) + int(quantity or 1)
    return out


def fitting_types(fitting: dict) -> set:
    fit = fitting.get("fit", fitting)
    return {int(t) for items in fit.values() if isinstance(items, dict) for t in items if str(t).isdigit()}


def match(contract: Contract, hull_type_id: int, modules: Dict[int, int], known: Iterable[int] = (),
          is_module: Callable[[int], bool] = lambda t: True, hull_only: bool = False) -> Optional[Offer]:
    """
    How well the contract fits: EXACT, NEAR_FIT, (with hull_only) HULL, or None. Extras are
    modules aboard that the fitting doesn't use (not its drones, charges or cargo).
    """
    if contract.asks or contract.items.get(hull_type_id, 0) < 1:
        return None
    missing = {t: q - contract.items.get(t, 0) for t, q in modules.items() if contract.items.get(t, 0) < q}
    needed = sum(modules.values())
    present = needed - sum(missing.values())
    known = set(known) | set(modules) | {hull_type_id}
    extra = {t: q for t, q in contract.items.items() if q > 0 and t not in known and is_module(t)}
    if not missing:
        return Offer(contract, EXACT, {}, extra)
    if needed and present / needed >= NEAR:
        return Offer(contract, NEAR_FIT, missing, extra)
    if hull_only:
        return Offer(contract, HULL, missing, extra)
    return None


class ContractBook:
    """The saved contracts, read once: alliance ones to offer, your own as stock, and what became of each."""

    def __init__(self, linked: Iterable[int], system_of: Callable[[int], Optional[int]],
                 folder: Optional[Path] = None, now: Optional[str] = None):
        self.linked = {int(c) for c in linked}
        saved = load_contracts(folder)
        items = load_items(folder)
        self.now = now or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._system_of = system_of
        self.alliance: List[Contract] = []
        self.own: List[Tuple[int, Contract]] = []      # (issuer character, contract)
        self.by_id: Dict[int, dict] = {}                # every contract seen, as ESI sent it
        for corp in saved["corporations"]:
            assigned = {int(corp.get("corporation_id") or 0), int(corp.get("alliance_id") or 0)} - {0}
            for c in corp.get("contracts", []):
                self.by_id.setdefault(int(c["contract_id"]), c)
                if (c.get("type") == "item_exchange" and is_live(c, self.now)
                        and int(c.get("assignee_id") or 0) in assigned
                        and int(c.get("issuer_id") or 0) not in self.linked):
                    contract = self._contract(c, "alliance", items)
                    if contract is not None:
                        self.alliance.append(contract)
        for char in saved["characters"]:
            for c in char.get("contracts", []):
                self.by_id[int(c["contract_id"])] = {**self.by_id.get(int(c["contract_id"]), {}), **c}
                if (c.get("type") == "item_exchange" and is_live(c, self.now)
                        and int(c.get("issuer_id") or 0) == int(char.get("character_id") or 0)):
                    contract = self._contract(c, "own", items)
                    if contract is not None:
                        self.own.append((int(c["issuer_id"]), contract))
        self.read = bool(saved["corporations"] or saved["characters"])

    def _contract(self, c: dict, source: str, items: Dict[str, list]) -> Optional[Contract]:
        listed = items.get(str(c["contract_id"]))
        if listed is None:
            return None             # items not read yet (next pull)
        included: Dict[int, int] = {}
        for i in listed:
            if i.get("is_included", True):
                included[int(i["type_id"])] = included.get(int(i["type_id"]), 0) + int(i.get("quantity", 1))
        location = int(c.get("start_location_id") or 0)
        return Contract(int(c["contract_id"]), source, float(c.get("price") or 0), location,
                        self._system_of(location), c.get("title") or "", c.get("date_expired") or "",
                        int(c.get("issuer_id") or 0), included,
                        any(not i.get("is_included", True) for i in listed))

    def offers(self, fitting: dict, system_id: Optional[int], station_id: Optional[int] = None,
               is_module: Callable[[int], bool] = lambda t: True) -> List[Offer]:
        """Exact and near alliance offers for a fitting in a system, station first, then exact, then cheapest."""
        hull = fitting.get("hull_type_id")
        if not hull or system_id is None:
            return []
        modules = slot_modules(fitting)
        known = fitting_types(fitting)
        out = []
        for contract in self.alliance:
            if contract.system_id != system_id:
                continue
            offer = match(contract, int(hull), modules, known, is_module)
            if offer is not None:
                offer.at_station = station_id is not None and contract.location_id == int(station_id)
                out.append(offer)
        return sorted(out, key=lambda o: o.sort_key)

    def fate(self, contract_id: int) -> Tuple[str, Optional[int]]:
        """
        What became of a chosen contract: ("live", None), ("accepted", the linked character who
        accepted it), or ("gone", None) when it's expired, deleted or someone else took it.
        """
        c = self.by_id.get(int(contract_id))
        if c is None:
            return "gone", None
        acceptor = int(c.get("acceptor_id") or 0)
        if acceptor in self.linked and c.get("status") in ("in_progress", "finished"):
            return "accepted", acceptor
        if is_live(c, self.now):
            return "live", None
        return "gone", None


# --- the 2-hour pass (28.4) ----------------------------------------------------------------------

def _key(character_id, req_uid) -> str:
    return f"{int(character_id)}:{int(req_uid)}"


class ContractChoices:
    """Use This Contract: {character:requirement -> the choice}, saved as it changes."""

    def __init__(self, config_dir: Optional[Path] = None, clock: Callable[[], datetime] = None):
        self.path = Path(config_dir or paths.CONFIG_DIR) / CHOICES_FILE
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        self.choices: Dict[str, dict] = data if isinstance(data, dict) else {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.choices, indent=1), encoding="utf-8")

    def choose(self, character_id, req_uid, offer: Offer, label: str) -> dict:
        until = self._clock() + timedelta(hours=PASS_HOURS)
        choice = {"contract_id": offer.contract.contract_id, "price": offer.contract.price, "label": label,
                  "location_id": offer.contract.location_id, "until": until.isoformat(timespec="seconds")}
        self.choices[_key(character_id, req_uid)] = choice
        self._save()
        return choice

    def get(self, character_id, req_uid) -> Optional[dict]:
        return self.choices.get(_key(character_id, req_uid))

    def cancel(self, character_id, req_uid) -> Optional[dict]:
        choice = self.choices.pop(_key(character_id, req_uid), None)
        if choice is not None:
            self._save()
        return choice

    def left(self, choice: dict) -> timedelta:
        try:
            until = datetime.fromisoformat(choice["until"])
        except (KeyError, ValueError):
            return timedelta(0)
        return until - self._clock()

    def review(self, character_id, req_uid, ship_found: bool, book: Optional[ContractBook]) -> Tuple[Optional[dict], str]:
        """
        The choice as it stands after an audit, and a note: the pass ends when the ship turns up
        or the contract's gone (D8.4); one accepted by a linked character waits for the ship, past
        the two hours. ("", the choice) while it's simply running.
        """
        choice = self.get(character_id, req_uid)
        if choice is None:
            return None, ""
        if ship_found:
            self.cancel(character_id, req_uid)
            return None, "ship found"
        fate, acceptor = book.fate(choice["contract_id"]) if book is not None else ("live", None)
        if fate == "accepted":
            if choice.get("accepted_by") != acceptor:
                choice["accepted_by"] = acceptor
                self._save()
            return choice, "accepted"
        if fate == "gone":
            self.cancel(character_id, req_uid)
            return None, "gone"
        if self.left(choice) <= timedelta(0):
            self.cancel(character_id, req_uid)
            return None, "expired"
        return choice, ""


def left_text(left: timedelta) -> str:
    minutes = max(int(left.total_seconds() // 60), 0)
    return f"{minutes // 60} h {minutes % 60:02d} m" if minutes >= 60 else f"{minutes} m"
