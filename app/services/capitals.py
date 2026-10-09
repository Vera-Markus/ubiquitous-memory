"""
Capitals (ESI features plan 28.6; D8.6-D8.12, O3, C1-C3).

A capital is a ship with a jump drive (`jumpDriveRange` > 0), except Supercarriers, Titans and
Black Ops (D8.12): Carriers, Command Carriers, Force Auxiliaries, Dreadnoughts, Lancers,
Rorquals and Jump Freighters. Its hull is never priced on the market: it comes from contracts,
alliance ones first, within jump range.

Distances use Fenris Creations' data only (D8.8): light-years between `mapSolarSystems` coordinates, the
hull's range at Jump Drive Calibration V (`jumpDriveRange` × 2), and jumps as the straight-line
distance over the range, rounded up: an estimate (C1). Stations give their system from
`staStations`; a structure only through the location cache (docking access).

Public contracts (the search window): one region list at a time, each region at most once
per 30 minutes (ESI caches them that long). Contracts of 1,000,000 m³ or more are the
candidates (lab: The Forge's 35,000 contracts came down to 85); only their items are read,
paced, and kept by contract ID in data/contracts/public_items.json.
"""
import json
import math
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple

import httpx

from app import paths
from app.esi_service.esi_settings import ESI_BASE_URL, esi_headers
from app.services.contracts import EXACT, HULL, NEAR_FIT, Contract, match, slot_modules

LIGHT_YEAR = 9.4607e15          # metres
JUMP_DRIVE_RANGE = 867          # dogma attribute: light-years at Jump Drive Calibration 0
NOT_CAPITALS = (30, 659, 898)   # Titan, Supercarrier, Black Ops (D8.12)
SHIP_CATEGORY = 6
MAX_JUMPS = 5                   # the search window's widest net (O3)
CAPITAL_VOLUME = 1_000_000.0    # m³: a capital hull's contract is at least this
REGION_TTL = 1800               # seconds: ESI caches a region's public contracts about 30 minutes
PUBLIC_ITEMS_FILE = "public_items.json"
ITEM_PACE = 0.35


class Galaxy:
    """Systems, stations and capital ranges from eve.db, read once."""

    def __init__(self, db_path, system_of: Optional[Callable[[int], Optional[int]]] = None):
        self._system_of = system_of
        with sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True) as db:
            self.systems: Dict[int, Tuple[str, int, float, float, float, float]] = {
                r[0]: (r[1], r[2], r[3], r[4], r[5], r[6] or 0.0) for r in db.execute(
                    "SELECT solarSystemID, solarSystemName, regionID, x, y, z, security FROM mapSolarSystems")}
            self.stations: Dict[int, int] = dict(db.execute("SELECT stationID, solarSystemID FROM staStations"))
            self.ranges: Dict[int, float] = {r[0]: float(r[1]) * 2 for r in db.execute(
                "SELECT t.typeID, COALESCE(a.valueFloat, a.valueInt) FROM invTypes t "
                "JOIN invGroups g USING(groupID) JOIN dgmTypeAttributes a ON a.typeID = t.typeID "
                f"WHERE a.attributeID = ? AND g.categoryID = ? AND t.groupID NOT IN ({','.join('?' * len(NOT_CAPITALS))}) "
                "AND COALESCE(a.valueFloat, a.valueInt) > 0", (JUMP_DRIVE_RANGE, SHIP_CATEGORY, *NOT_CAPITALS))}
            self.regions: Dict[int, str] = dict(db.execute("SELECT regionID, regionName FROM mapRegions")) \
                if db.execute("SELECT name FROM sqlite_master WHERE name = 'mapRegions'").fetchone() else {}
        self.names = {v[0].casefold(): k for k, v in self.systems.items()}

    def is_capital(self, type_id: Optional[int]) -> bool:
        return type_id is not None and int(type_id) in self.ranges

    def jump_range(self, type_id: int) -> float:
        """Light-years per jump at Jump Drive Calibration V."""
        return self.ranges.get(int(type_id), 0.0)

    def name(self, system_id: Optional[int]) -> str:
        return self.systems[system_id][0] if system_id in self.systems else "?"

    def system_of(self, location_id: Optional[int]) -> Optional[int]:
        if location_id is None:
            return None
        location_id = int(location_id)
        if location_id in self.systems:
            return location_id
        if location_id in self.stations:
            return self.stations[location_id]
        return self._system_of(location_id) if self._system_of else None

    def light_years(self, a: int, b: int) -> float:
        _, _, ax, ay, az, _ = self.systems[a]
        _, _, bx, by, bz, _ = self.systems[b]
        return math.dist((ax, ay, az), (bx, by, bz)) / LIGHT_YEAR

    def within(self, centre: int, light_years: float) -> Dict[int, float]:
        """{system: light-years} for every system within reach of the centre (the centre at 0)."""
        _, _, cx, cy, cz, _ = self.systems[centre]
        reach = light_years * LIGHT_YEAR
        out = {}
        for system, (_, _, x, y, z, _) in self.systems.items():
            d = math.dist((cx, cy, cz), (x, y, z))
            if d <= reach:
                out[system] = d / LIGHT_YEAR
        return out


def jumps(light_years: float, jump_range: float) -> int:
    """C1: straight-line distance over the range, rounded up (0 in the same system)."""
    if light_years <= 0 or jump_range <= 0:
        return 0
    return max(1, math.ceil(light_years / jump_range - 1e-9))


# --- matching for the search window (C3) -------------------------------------------------------------

RIG_SECTION = "rigs"


@dataclass
class Found:
    contract: Contract
    label: str                  # "Exact fit", "Near fit", "Hull + rigs", "Hull", or another doctrine fitting's name
    kind: str                   # EXACT, NEAR_FIT or HULL (another fitting's exact match counts as EXACT)
    light_years: Optional[float]
    jumps: Optional[int]
    missing: Dict[int, int] = field(default_factory=dict)
    extra: Dict[int, int] = field(default_factory=dict)

    @property
    def sort_key(self):
        return (self.jumps is None, self.jumps or 0, (EXACT, NEAR_FIT, HULL).index(self.kind),
                self.contract.price, self.light_years or 0)


def label_for(contract: Contract, hull: int, fitting: Optional[dict], others: Iterable[dict],
              is_module: Callable[[int], bool], rigs: Optional[Dict[int, int]] = None) -> Optional[Tuple[str, str, dict, dict]]:
    """
    (label, kind, missing, extra) for a contract holding the hull (C3): the requirement's fitting
    exactly or nearly; else another of your fittings for the hull exactly, by name; else the
    hull, with its rigs when every rig is there.
    """
    if fitting is not None:
        offer = match(contract, hull, slot_modules(fitting), is_module=is_module, hull_only=True)
        if offer is None:
            return None
        if offer.kind in (EXACT, NEAR_FIT):
            return ("Exact fit" if offer.kind == EXACT else "Near fit"), offer.kind, offer.missing, offer.extra
    elif contract.asks or contract.items.get(hull, 0) < 1:
        return None
    for other in others:
        exact = match(contract, hull, slot_modules(other), is_module=is_module)
        if exact is not None and exact.kind == EXACT:
            return other.get("fit_name") or "Another fitting", EXACT, {}, {}
    rigs = rigs or {}
    has_rigs = bool(rigs) and all(contract.items.get(r, 0) >= q for r, q in rigs.items())
    return ("Hull + rigs" if has_rigs else "Hull"), HULL, {}, {}


def rig_counts(fitting: Optional[dict]) -> Dict[int, int]:
    """The fitting's rigs: type ID -> how many."""
    rigs = (fitting or {}).get("fit", fitting or {}).get(RIG_SECTION) or {}
    return {int(t): int(e.get("quantity", 1) if isinstance(e, dict) else e or 1) for t, e in rigs.items()
            if str(t).isdigit()}


# --- public contracts --------------------------------------------------------------------------------

class PublicContracts:
    """Region lists (30 minutes each) and candidates' items (kept by contract ID), from the public routes."""

    def __init__(self, folder: Optional[Path] = None, get: Optional[Callable] = None,
                 clock: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep):
        self.folder = Path(folder or paths.CONTRACTS_DIR)
        self._get = get
        self._clock = clock
        self._sleep = sleep
        self._regions: Dict[int, Tuple[float, list]] = {}
        try:
            self.items = json.loads((self.folder / PUBLIC_ITEMS_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.items = {}

    def _client_get(self, client, url, params=None):
        if self._get is not None:
            return self._get(url, params or {})
        r = client.get(url, params=params or {})
        try:
            body = r.json() if r.status_code == 200 else None
        except ValueError:
            body = None
        return r.status_code, body, dict(r.headers)

    def region(self, region_id: int, client=None) -> Optional[list]:
        """Every public contract in the region, all pages; None when ESI didn't answer."""
        cached = self._regions.get(region_id)
        if cached and cached[0] > self._clock():
            return cached[1]
        out, page, pages = [], 1, 1
        while page <= pages:
            status, body, headers = self._client_get(client, f"/contracts/public/{region_id}", {"page": page})
            if status != 200:
                return None if page == 1 else out
            out.extend(body or [])
            try:
                pages = int(headers.get("x-pages") or 1)
            except (TypeError, ValueError):
                pages = 1
            page += 1
        self._regions[region_id] = (self._clock() + REGION_TTL, out)
        return out

    def contract_items(self, contract_id: int, client=None, paced: bool = True) -> Optional[list]:
        key = str(contract_id)
        if key in self.items:
            return self.items[key]
        if paced:
            self._sleep(ITEM_PACE)
        status, body, _ = self._client_get(client, f"/contracts/public/items/{contract_id}")
        if status == 200 and isinstance(body, list):
            self.items[key] = [{"type_id": i["type_id"], "quantity": i.get("quantity", 1),
                                "is_included": i.get("is_included", True)} for i in body]
            return self.items[key]
        if status in (204, 403, 404):
            self.items[key] = []        # gone or accepted: nothing to read again
        return None

    def save(self, listed: Iterable[int]) -> None:
        """Saves the items read. A contract found gone is dropped once it's no longer listed either."""
        listed = {str(k) for k in listed}
        self.items = {k: v for k, v in self.items.items() if v or k in listed}
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder / PUBLIC_ITEMS_FILE).write_text(json.dumps(self.items), encoding="utf-8")

    def client(self):
        return httpx.Client(base_url=ESI_BASE_URL, headers=esi_headers(), timeout=20.0) if self._get is None else None


def to_contract(c: dict, items: list, system_id: Optional[int], source: str = "public") -> Contract:
    included: Dict[int, int] = {}
    for i in items:
        if i.get("is_included", True):
            included[int(i["type_id"])] = included.get(int(i["type_id"]), 0) + int(i.get("quantity", 1))
    location = int(c.get("start_location_id") or 0)
    return Contract(int(c["contract_id"]), source, float(c.get("price") or 0), location, system_id,
                    c.get("title") or "", c.get("date_expired") or "", int(c.get("issuer_id") or 0), included,
                    any(not i.get("is_included", True) for i in items))


def search(galaxy: Galaxy, hull: int, centre: int, alliance: Iterable[Contract], public: PublicContracts,
           fitting: Optional[dict], others: Iterable[dict], is_module: Callable[[int], bool],
           progress: Callable[[str], None] = lambda text: None, max_jumps: int = MAX_JUMPS,
           now: Optional[str] = None, on_found: Callable[[List["Found"]], None] = lambda found: None,
           stopped: Callable[[], bool] = lambda: False, include_public: bool = True) -> List[Found]:
    """
    Every contract holding the hull within max_jumps of the centre (O3): alliance ones (already
    pulled), then public ones. Public contracts' items are read nearest first, so the short
    jumps fill in first; on_found gets the results so far as they come. Contracts in structures
    the app can't place are read last and listed last. stopped() ends it early.
    """
    from datetime import datetime, timezone
    now = now or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    reach = galaxy.jump_range(hull)
    systems = galaxy.within(centre, reach * max_jumps)
    rigs = rig_counts(fitting)
    others = [o for o in others if o is not fitting and o.get("hull_type_id") == hull]
    found: List[Found] = []

    def add(contract: Contract, source: str):
        labelled = label_for(contract, hull, fitting, others, is_module, rigs)
        if labelled is None:
            return
        label, kind, missing, extra = labelled
        ly = systems.get(contract.system_id) if contract.system_id is not None else None
        contract.source = source
        found.append(Found(contract, label, kind, ly, jumps(ly, reach) if ly is not None else None, missing, extra))

    progress("Alliance contracts…")
    for contract in alliance:
        if contract.system_id in systems:
            add(contract, "alliance")
    on_found(sorted(found, key=lambda f: f.sort_key))
    if not include_public:
        return sorted(found, key=lambda f: f.sort_key)
    regions = sorted({galaxy.systems[s][1] for s in systems})
    listed_ids = set()
    candidates = []                 # (light-years, or None for a structure the app can't place; contract)
    client = public.client()
    try:
        for n, region in enumerate(regions, 1):
            if stopped():
                return sorted(found, key=lambda f: f.sort_key)
            progress(f"Public contracts: region {n} of {len(regions)}…")
            for c in public.region(region, client) or []:
                listed_ids.add(int(c["contract_id"]))
                # Public contracts are all outstanding (no status field); only the expiry tells.
                if c.get("type") != "item_exchange" or float(c.get("volume") or 0) < CAPITAL_VOLUME:
                    continue
                if (c.get("date_expired") or "") <= now:
                    continue
                system = galaxy.system_of(c.get("start_location_id"))
                if system is not None and system not in systems:
                    continue
                candidates.append((systems.get(system) if system is not None else None, system, c))
        candidates.sort(key=lambda e: (e[0] is None, e[0] or 0))
        for n, (_, system, c) in enumerate(candidates, 1):
            if stopped():
                break
            cached = str(c["contract_id"]) in public.items
            if not cached and (n == 1 or n % 10 == 0):
                progress(f"Reading public contracts: {n} of {len(candidates)}, nearest first…")
            items = public.contract_items(int(c["contract_id"]), client)
            if items:
                before = len(found)
                add(to_contract(c, items, system), "public")
                if len(found) > before:
                    on_found(sorted(found, key=lambda f: f.sort_key))
    finally:
        if client is not None:
            client.close()
        public.save(listed_ids)
    return sorted(found, key=lambda f: f.sort_key)
