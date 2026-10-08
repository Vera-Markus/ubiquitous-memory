"""
Contracts, pulled with the assets when Options ▸ ESI Features ▸ Contracts is on (ESI features
plan 28.2, 28.3).

    GET /characters/{id}/                                  corporation, alliance (public)
    GET /characters/{id}/contracts?page=n                  esi-contracts.read_character_contracts.v1
    GET /characters/{id}/contracts/{contract_id}/items     (the same)
    GET /corporations/{id}/contracts?page=n                esi-contracts.read_corporation_contracts.v1
    GET /corporations/{id}/contracts/{contract_id}/items   (the same)

Each character's own contracts (issued, assigned or accepted: ESI keeps them about 30 days)
go to data/contracts/char_<id>.json. Each corporation's go to data/contracts/corp_<id>.json,
fetched once per corporation per pull, by any linked member whose login has the scope: the
corporation route also lists contracts assigned to the alliance, and needs no corporation
role (lab, 2026-10-07).

A contract's items never change, so they're fetched once, only for contracts the app can
use (outstanding item exchanges big enough to hold a ship, or your own), and kept in
data/contracts/items.json until the contract is gone. Item calls are paced: in a burst the
corporation route answered HTTP 520 to half of them; at about 3 a second, none.
"""
import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app import paths
from app.asset_handling.clone_pull import token_scopes
from app.esi_service.base_interfaces import ESIRequest

logger = logging.getLogger("MultiCharAssetPull")       # the pull's logger, so lines reach the GUI log

CHARACTER_SCOPE = "esi-contracts.read_character_contracts.v1"
CORPORATION_SCOPE = "esi-contracts.read_corporation_contracts.v1"
ITEMS_FILE = "items.json"
SHIP_VOLUME = 2500.0        # m³: a packaged frigate; anything smaller can't hold a ship
ITEM_PACE = 0.35            # seconds between item calls


def contracts_dir(folder: Optional[Path] = None) -> Path:
    return Path(folder or paths.CONTRACTS_DIR)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def is_live(contract: dict, now: Optional[str] = None) -> bool:
    """Outstanding and not expired (ESI leaves expired ones "outstanding" until someone looks)."""
    return contract.get("status") == "outstanding" and (contract.get("date_expired") or "") > (now or _now())


def wants_items(contract: dict, own: bool = False) -> bool:
    """A live item exchange that could hold a ship (or, for your own, anything: it's stock)."""
    return contract.get("type") == "item_exchange" and is_live(contract) and (
        own or float(contract.get("volume") or 0) >= SHIP_VOLUME)


def _write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    tmp.replace(path)


def load_items(folder: Optional[Path] = None) -> Dict[str, list]:
    try:
        data = json.loads((contracts_dir(folder) / ITEMS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


async def _pages(esi_client, url: str) -> Optional[list]:
    out, page, pages = [], 1, 1
    while page <= pages:
        r = await esi_client.request(ESIRequest(url=url, params={"page": page}))
        if r.status_code != 200:
            return None
        out.extend(r.data or [])
        try:
            pages = int(r.headers.get("x-pages") or 1)
        except (TypeError, ValueError):
            pages = 1
        page += 1
    return out


async def fetch_items(esi_client, base: str, contracts: Iterable[dict], cache: Dict[str, list],
                      sleep=asyncio.sleep) -> Tuple[int, int]:
    """Items for contracts not cached yet, paced. (fetched, failed); a failure is tried again next pull."""
    fetched = failed = 0
    for contract in contracts:
        key = str(contract["contract_id"])
        if key in cache:
            continue
        if fetched or failed:
            await sleep(ITEM_PACE)
        r = await esi_client.request(ESIRequest(url=f"{base}/{key}/items"))
        if r.status_code == 200 and isinstance(r.data, list):
            cache[key] = [{"type_id": i["type_id"], "quantity": i.get("quantity", 1),
                           "is_included": i.get("is_included", True), "is_singleton": i.get("is_singleton", False)}
                          for i in r.data]
            fetched += 1
        else:
            failed += 1
    return fetched, failed


async def pull_contracts(esi_client, auth: Any, folder: Optional[Path] = None, sleep=asyncio.sleep) -> Dict[str, Any]:
    """
    Every linked character's contracts, then each of their corporations' (once each), with the
    items the app can use. The client's active character is restored. A failure never fails the pull.
    """
    folder = contracts_dir(folder)
    cache = load_items(folder)
    seen_ids: set = set()
    corps: Dict[int, Tuple[str, Optional[int]]] = {}      # corporation -> (a member who may read it, alliance)
    summary = {"characters": 0, "corporations": 0, "items_fetched": 0, "items_failed": 0}
    original = auth.active_character_id
    try:
        for char_id in sorted(auth.profiles, key=int):
            auth.active_character_id = char_id
            name = auth.index.get(char_id, char_id)
            scopes = token_scopes(auth.get_access_token())
            if CHARACTER_SCOPE not in scopes:
                logger.info(f"{name}'s login doesn't include contracts; log in again "
                            "(Characters ▸ Add Character) for contracts.")
                continue
            info = await esi_client.request(ESIRequest(url=f"/characters/{char_id}/"))
            if info.status_code == 200 and CORPORATION_SCOPE in scopes:
                corp = int(info.data["corporation_id"])
                corps.setdefault(corp, (char_id, info.data.get("alliance_id")))
            contracts = await _pages(esi_client, f"/characters/{char_id}/contracts")
            if contracts is None:
                logger.warning(f"Couldn't read {name}'s contracts; the last ones read are kept.")
                old = _read(folder / f"char_{char_id}.json")
                seen_ids |= {str(c["contract_id"]) for c in old.get("contracts", [])}
                continue
            seen_ids |= {str(c["contract_id"]) for c in contracts}
            # Your own live item exchanges are stock (28.2): their items, whatever their size.
            own = [c for c in contracts if int(c.get("issuer_id", 0)) == int(char_id) and wants_items(c, own=True)]
            got, bad = await fetch_items(esi_client, f"/characters/{char_id}/contracts", own, cache, sleep)
            summary["items_fetched"] += got
            summary["items_failed"] += bad
            _write(folder / f"char_{char_id}.json", {"character_id": int(char_id), "pulled_at": _now(),
                                                     "contracts": contracts})
            summary["characters"] += 1
        for corp, (char_id, alliance) in sorted(corps.items()):
            auth.active_character_id = char_id
            contracts = await _pages(esi_client, f"/corporations/{corp}/contracts")
            if contracts is None:
                logger.warning(f"Couldn't read corporation {corp}'s contracts; the last ones read are kept.")
                old = _read(folder / f"corp_{corp}.json")
                seen_ids |= {str(c["contract_id"]) for c in old.get("contracts", [])}
                continue
            seen_ids |= {str(c["contract_id"]) for c in contracts}
            usable = [c for c in contracts if wants_items(c)]
            got, bad = await fetch_items(esi_client, f"/corporations/{corp}/contracts", usable, cache, sleep)
            summary["items_fetched"] += got
            summary["items_failed"] += bad
            _write(folder / f"corp_{corp}.json", {"corporation_id": corp, "alliance_id": alliance,
                                                  "pulled_at": _now(), "contracts": contracts})
            summary["corporations"] += 1
    finally:
        auth.active_character_id = original
    # Items of contracts no longer listed anywhere: gone for good.
    cache = {k: v for k, v in cache.items() if k in seen_ids}
    _write(folder / ITEMS_FILE, cache)
    if summary["characters"] or summary["corporations"]:
        logger.info(f"Contracts: {summary['characters']} character(s), {summary['corporations']} corporation(s); "
                    f"items read for {summary['items_fetched']} new contract(s)"
                    + (f", {summary['items_failed']} to try again next pull" if summary["items_failed"] else "") + ".")
    return summary


def _read(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def load_contracts(folder: Optional[Path] = None) -> Dict[str, List[dict]]:
    """{"characters": [file, …], "corporations": [file, …]} as saved."""
    folder = contracts_dir(folder)
    out: Dict[str, List[dict]] = {"characters": [], "corporations": []}
    if not folder.exists():
        return out
    for path in sorted(folder.glob("char_*.json")):
        out["characters"].append(_read(path))
    for path in sorted(folder.glob("corp_*.json")):
        out["corporations"].append(_read(path))
    return out
