"""
Hub prices for the shopping list (ESI features plan 28.1, D3).

    GET /markets/{region_id}/orders?order_type=sell&type_id=…     public, no login

Only sell orders at the chosen hub's station count (D3.1); the region's other stations
are left out. A price walks the orders, cheapest first, for the quantity needed (D3.2),
so 14 of an item costs what 14 actually cost, not 14 × the lowest order. Each type's
orders are kept until ESI's Expires (about 5 minutes), so a list redrawn after every
change doesn't ask again.
"""
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Callable, Dict, List, Optional, Tuple

import httpx

from app.esi_service.esi_settings import ESI_BASE_URL, esi_headers

DEFAULT_TTL = 300           # seconds, when ESI sends no usable Expires
Orders = List[Tuple[float, int]]        # (price, volume remaining), cheapest first


@dataclass
class Quote:
    """What `quantity` of a type costs at the hub: `filled` units for `cost` ISK."""
    type_id: int
    quantity: int
    cost: float = 0.0
    filled: int = 0

    @property
    def listed(self) -> bool:
        """Anything on sale at the hub at all."""
        return self.filled > 0

    @property
    def enough(self) -> bool:
        return self.filled >= self.quantity


def walk(orders: Orders, type_id: int, quantity: int) -> Quote:
    """Buys up the cheapest orders until `quantity` is covered, or the orders run out."""
    quote = Quote(type_id, quantity)
    for price, volume in sorted(orders):
        if quote.filled >= quantity:
            break
        take = min(volume, quantity - quote.filled)
        quote.cost += take * price
        quote.filled += take
    return quote


def isk(amount: float) -> str:
    """79.4 M, 1.23 B, 512 K, 950: short, for lines and totals."""
    for size, unit in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(amount) >= size:
            value = amount / size
            return f"{value:.2f} {unit}" if value < 10 else f"{value:.1f} {unit}"
    return f"{amount:,.0f}"


def _expires(headers, now: float) -> float:
    try:
        return max(parsedate_to_datetime(headers.get("expires")).timestamp(), now + 10)
    except (TypeError, ValueError):
        return now + DEFAULT_TTL


def fetch_orders(region_id: int, station_id: int, type_id: int, client: Optional[httpx.Client] = None,
                 now: Optional[float] = None) -> Tuple[Optional[Orders], float]:
    """(the hub's sell orders, cheapest first, or None when ESI didn't answer; when they expire)."""
    now = time.time() if now is None else now
    own = client is None
    client = client or httpx.Client(base_url=ESI_BASE_URL, headers=esi_headers(), timeout=15.0)
    try:
        orders: Orders = []
        page, pages, expires = 1, 1, now + DEFAULT_TTL
        while page <= pages:
            r = client.get(f"/markets/{region_id}/orders",
                           params={"order_type": "sell", "type_id": type_id, "page": page})
            if r.status_code != 200:
                return None, now + 30
            if page == 1:
                expires = _expires(r.headers, now)
                try:
                    pages = int(r.headers.get("x-pages") or 1)
                except ValueError:
                    pages = 1
            orders += [(float(o["price"]), int(o["volume_remain"])) for o in r.json()
                       if int(o.get("location_id", 0)) == int(station_id) and not o.get("is_buy_order")]
            page += 1
        return sorted(orders), expires
    except (httpx.HTTPError, ValueError, KeyError):
        return None, now + 30
    finally:
        if own:
            client.close()


class PriceBook:
    """Hub orders by (station, type), kept until they expire. Safe to share between threads one at a time."""

    def __init__(self, fetch: Optional[Callable] = None, clock: Callable[[], float] = time.time):
        self._fetch = fetch or (lambda *args: fetch_orders(*args))     # looked up at the call (the GUI env fakes it)
        self._clock = clock
        self._orders: Dict[Tuple[int, int], Tuple[float, Optional[Orders]]] = {}

    def orders(self, region_id: int, station_id: int, type_id: int, client=None) -> Optional[Orders]:
        now = self._clock()
        cached = self._orders.get((station_id, type_id))
        if cached is not None and cached[0] > now:
            return cached[1]
        orders, expires = self._fetch(region_id, station_id, type_id, client, now)
        self._orders[(station_id, type_id)] = (expires, orders)
        return orders

    def quote(self, region_id: int, station_id: int, type_id: int, quantity: int, client=None) -> Optional[Quote]:
        """None when the hub's orders couldn't be read (not the same as nothing on sale)."""
        orders = self.orders(region_id, station_id, type_id, client)
        return None if orders is None else walk(orders, type_id, quantity)

    def quotes(self, region_id: int, station_id: int, needs: Dict[int, int],
               progress: Optional[Callable[[int, int], None]] = None) -> Dict[int, Optional[Quote]]:
        """Quotes for {type ID: quantity}, over one connection."""
        out: Dict[int, Optional[Quote]] = {}
        with httpx.Client(base_url=ESI_BASE_URL, headers=esi_headers(), timeout=15.0) as client:
            for n, (type_id, quantity) in enumerate(needs.items(), 1):
                out[type_id] = self.quote(region_id, station_id, type_id, quantity, client)
                if progress:
                    progress(n, len(needs))
        return out


@dataclass
class Totals:
    """The Buy lines' estimate: what's priced, what has no sell orders, what's short at the hub."""
    cost: float = 0.0
    unlisted: int = 0           # types with nothing on sale at the hub: left out of the total
    short: int = 0              # types with some on sale, but not enough: priced for what there is
    unread: int = 0             # types whose orders couldn't be read

    def text(self, hub: str) -> str:
        parts = [f"≈ {isk(self.cost)} ISK to buy at {hub}"]
        if self.unlisted:
            parts.append(f"{self.unlisted} item type{'s' if self.unlisted != 1 else ''} with no sell orders there "
                         "left out")
        if self.short:
            parts.append(f"{self.short} short of the quantity there")
        if self.unread:
            parts.append(f"{self.unread} not priced (ESI didn't answer)")
        return parts[0] + (f" ({'; '.join(parts[1:])})" if len(parts) > 1 else "")


def totals(quotes: Dict[int, Optional[Quote]], type_ids) -> Totals:
    t = Totals()
    for type_id in type_ids:
        q = quotes.get(type_id)
        if q is None:
            t.unread += 1
        elif not q.listed:
            t.unlisted += 1
        else:
            t.cost += q.cost
            t.short += 0 if q.enough else 1
    return t


def line_text(quote: Optional[Quote]) -> str:
    """The Buy line's price column: '≈ 2.10 M', 'none at hub', 'only 3: ≈ 1.2 M', '?'."""
    if quote is None:
        return "?"
    if not quote.listed:
        return "none at hub"
    text = f"≈ {isk(quote.cost)}"
    return text if quote.enough else f"only {quote.filled:,}: {text}"
