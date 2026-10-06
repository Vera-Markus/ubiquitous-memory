"""
Pull from stock before buying (UI thoughts plan Phase 21, U8, A5).

Stock is what a holder has in a solar system that isn't inside a ship: hangars, corporation
divisions, deliveries, and containers sitting in them; modules count as well as consumables,
and so does a packaged hull. Assembled ships and asset safety don't. A character's ships
draw only on that character's stock (a corporation's ships, on the corporation's).

Each unit is given out once: the shopping list asks the index for each ship's needs in the
order the ships were added, and what one ship takes isn't there for the next. Identical-stat
twins count, as they do in the audit. What stock doesn't cover is bought.
"""
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

Needs = Dict[int, Tuple[str, int]]          # type ID -> (name, quantity), as in shopping_list_service

NOT_STOCK = ("asset safety", "impounded")   # where items can't simply be picked up


@dataclass
class PullLine:
    """Units to fetch from one stack: "Jita IV - Moon 4 - Caldari Navy Assembly Plant · hangar · 'Ammo Can 1'"."""
    place: str
    where: str
    type_id: int
    item_name: str
    quantity: int
    for_type_id: int                # the type the ship needs (a twin may be pulled for it)

    def line(self) -> str:
        return f"{self.place} · {self.where}: {self.item_name} x{self.quantity}"


def where_text(aboard: str) -> str:
    """"hangar / in 'Ammo Can 1'" -> "hangar · 'Ammo Can 1'"."""
    return " · ".join(part[3:] if part.startswith("in '") else part for part in (aboard or "hangar").split(" / "))


class StockIndex:
    def __init__(self, universe: Any, place_label: Callable[[int], str], type_name: Callable[[int], str],
                 key: Optional[Callable[[int], int]] = None):
        self._key = key or (lambda type_id: type_id)
        self._type_name = type_name
        self._stock: Dict[tuple, List[list]] = defaultdict(list)    # (kind, id, system, key) -> [[sighting, left, place]]
        for s in universe.items():
            if s.carrier_item_id is not None or universe.is_ship(s) or s.system_id is None:
                continue
            if (s.aboard or "").split(" / ")[0] in NOT_STOCK:
                continue
            self._stock[(s.holder_kind, s.holder_id, s.system_id, self._key(s.type_id))].append(
                [s, s.quantity, place_label(s.root_location_id)])
        for stacks in self._stock.values():
            stacks.sort(key=lambda e: (e[2].casefold(), where_text(e[0].aboard), e[0].item_id))

    def allocate(self, holder: Optional[Dict[str, Any]], system_id: Optional[int],
                 needs: Needs) -> Tuple[List[PullLine], Needs]:
        """(what to pull, what's left to buy) for one ship's needs; pulled units are used up."""
        pulls: List[PullLine] = []
        buy: Needs = {}
        for type_id, (name, quantity) in needs.items():
            left = quantity
            if holder is not None and system_id is not None:
                for entry in self._stock.get((holder["kind"], int(holder["id"]), system_id, self._key(type_id)), []):
                    if left <= 0:
                        break
                    take = min(left, entry[1])
                    if take <= 0:
                        continue
                    entry[1] -= take
                    left -= take
                    s = entry[0]
                    pulls.append(PullLine(entry[2], where_text(s.aboard), s.type_id,
                                          name if s.type_id == type_id else self._type_name(s.type_id), take, type_id))
            if left > 0:
                buy[type_id] = (name, left)
        return pulls, buy


def merge_pulls(pulls: List[PullLine]) -> List[PullLine]:
    """One line per place, hangar or container and type, sorted by place, where and name."""
    merged: Dict[tuple, PullLine] = {}
    for p in pulls:
        k = (p.place, p.where, p.type_id)
        if k in merged:
            merged[k].quantity += p.quantity
        else:
            merged[k] = PullLine(p.place, p.where, p.type_id, p.item_name, p.quantity, p.for_type_id)
    return sorted(merged.values(), key=lambda p: (p.place.casefold(), p.where.casefold(), p.item_name.casefold()))


def list_text(pulls: List[PullLine], buy_lines: List[str]) -> str:
    """Copy and Export: each line under the section it's in."""
    parts = []
    if pulls:
        parts += ["Pull from stock:"] + [p.line() for p in pulls]
    if buy_lines:
        parts += ([""] if parts else []) + ["Buy:"] + buy_lines
    return "\n".join(parts)
