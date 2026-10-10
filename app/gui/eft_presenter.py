"""
The ship view's EFT tab (UI thoughts plan 19.2, U6): one audited ship in EFT layout.

Built only from the audit's own result (what it found aboard, what the fitting expects,
the refit moves, shortfalls, substitutions and unexpected items), so the EFT and Audit
tabs always agree. Two views:

- Current, what's on the ship: in place (ok), in the wrong place (moved, "→ high slots"),
  to take off (remove, drawn struck through), anything else (extra: more than the fit needs).
- Expected, what the fitting says: present (ok), present but elsewhere (moved, "from
  cargo"), missing (missing).

Charges loaded in modules count as cargo, as they do in the audit, but Current lists them
under their modules, marked "loaded" (1.7.4). Each line is (text, tag); the tab colours the
tags from the theme.
"""
from collections import Counter, defaultdict
from typing import Dict, List, Tuple

from app.gui.audit_presenter import SLOT_LABELS, SPACE_LABELS
from app.models.audit_models import EftItem, ShipRequirementResult
from app.services.audit.configuration import REMOVE
from app.services.audit.expectations import SLOT_LOCATIONS

OK, MOVED, MISSING, REMOVE_TAG, EXTRA, HULL = "ok", "moved", "missing", "remove", "extra", "hull"
ORDER = ("low", "mid", "high", "rigs", "subsystem", "drones", "fighter_tubes", "fighters", "cargo", "fuel_bay")

Line = Tuple[str, str]


def where(location: str) -> str:
    return SLOT_LABELS.get(location) or SPACE_LABELS.get(location) or location


def _lines(location: str, name: str, quantity: int, tag: str, note: str = "", loaded: bool = False) -> List[Line]:
    """A module per line in the slots, "Name x5" elsewhere (as EFT writes them). loaded: a charge in
    modules, "Name x100 (loaded)"."""
    if quantity <= 0:
        return []
    note = " · ".join(n for n in ("loaded" if loaded else "", note) if n)
    suffix = f"  ({note})" if note else ""
    if location in SLOT_LOCATIONS:
        return [(name + suffix, tag)] * quantity
    return [(f"{name} x{quantity}{suffix}", tag)]


def shown_in(item: EftItem) -> str:
    """The section an item is listed in: a loaded charge's modules', else its own location."""
    return item.loaded or item.location


def _merged(items: List[EftItem]) -> List[EftItem]:
    """
    One entry per type and location (stacks, and charges loaded in several modules), in EFT order.
    Loaded charges come after the modules of their section, and before the cargo, so they count first.
    """
    merged: Dict[tuple, EftItem] = {}
    for i in items:
        key = (i.location, i.type_id, i.loaded)
        previous = merged.get(key)
        merged[key] = EftItem(i.location, i.type_id, i.key, i.name, i.quantity + (previous.quantity if previous else 0),
                              i.loaded)
    return sorted(merged.values(), key=lambda i: (ORDER.index(shown_in(i)) if shown_in(i) in ORDER else 99,
                                                  bool(i.loaded), i.name.casefold()))


def _layout(header: str, sections: Dict[str, List[Line]]) -> List[Line]:
    out: List[Line] = [(header, HULL)]
    for location in ORDER:
        if sections.get(location):
            out.append(("", ""))
            out.extend(sections[location])
    return out


def current_lines(result: ShipRequirementResult, hull: str, fit_name: str) -> List[Line]:
    """What's on the ship, marked against the fitting."""
    expected_left = Counter()
    for item in result.expected:
        expected_left[(item.location, item.key)] += item.quantity
    moves_out = defaultdict(list)          # (from, key) -> [[move, quantity left]]
    for move in result.refit_moves:
        moves_out[(move.from_location, move.type_id)].append([move, move.quantity])
    keys = {i.type_id: i.key for i in result.contents}
    substitute_for = defaultdict(list)     # fitted key -> [[expected name, quantity left]]
    for s in result.substitutions:
        substitute_for[keys.get(s.fitted_type_id, s.fitted_type_id)].append([s.expected_name, s.quantity])
    unexpected_left = Counter()
    for item in result.unexpected_aboard:
        unexpected_left[(item.location, item.type_id)] += item.quantity

    sections: Dict[str, List[Line]] = defaultdict(list)
    for item in _merged(result.contents):
        out = sections[shown_in(item)]

        def lines(quantity, tag, note=""):
            return _lines(item.location, item.name, quantity, tag, note, loaded=bool(item.loaded))
        here = min(item.quantity, expected_left[(item.location, item.key)])
        expected_left[(item.location, item.key)] -= here
        out += lines(here, OK)
        rest = item.quantity - here
        for entry in moves_out[(item.location, item.key)]:
            take = min(rest, entry[1])
            if take <= 0:
                continue
            entry[1] -= take
            rest -= take
            if entry[0].to_location == REMOVE:
                out += lines(take, REMOVE_TAG, "remove")
            else:
                out += lines(take, MOVED, f"→ {where(entry[0].to_location)}")
        for entry in substitute_for[item.key]:
            take = min(rest, entry[1])
            if take > 0:
                entry[1] -= take
                rest -= take
                out += lines(take, OK, f"for {entry[0]}")
        take = min(rest, unexpected_left[(item.location, item.type_id)])
        unexpected_left[(item.location, item.type_id)] -= take
        rest -= take
        out += lines(take, REMOVE_TAG, "not in the fit")
        out += lines(rest, EXTRA, "more than the fit needs")
    custom = result.custom_name if result.custom_name and result.custom_name != hull else fit_name
    return _layout(f"[{hull}, {custom}]", sections)


def fitted_lines(items, hull: str, custom_name: str = "") -> List[Line]:
    """
    A ship with no fitting assigned (1.7.2 plan, 32.4): what's aboard as it stands, every line
    green. items are the audit's AboardItems (location, type_id, name, quantity).
    """
    sections: Dict[str, List[Line]] = defaultdict(list)
    for item in _merged([EftItem(i.location, i.type_id, i.type_id, i.name, i.quantity, getattr(i, "loaded", ""))
                         for i in items]):
        sections[shown_in(item)] += _lines(item.location, item.name, item.quantity, OK, loaded=bool(item.loaded))
    return _layout(f"[{hull}, {custom_name or hull}]", sections)


def expected_lines(result: ShipRequirementResult, hull: str, fit_name: str) -> List[Line]:
    """What the fitting says, marked against what's aboard."""
    actual_left = Counter()
    for item in result.contents:
        actual_left[(item.location, item.key)] += item.quantity
    moves_in = defaultdict(list)           # (to, key) -> [[move, quantity left]]
    for move in result.refit_moves:
        if move.to_location != REMOVE:
            moves_in[(move.to_location, move.type_id)].append([move, move.quantity])
    keys = {i.type_id: i.key for i in result.expected}
    substituted = defaultdict(list)        # expected key -> [[fitted name, quantity left]]
    for s in result.substitutions:
        substituted[keys.get(s.expected_type_id, s.expected_type_id)].append([s.fitted_name, s.quantity])

    sections: Dict[str, List[Line]] = defaultdict(list)
    for item in _merged(result.expected):
        out = sections[item.location]
        here = min(item.quantity, actual_left[(item.location, item.key)])
        actual_left[(item.location, item.key)] -= here
        out += _lines(item.location, item.name, here, OK)
        rest = item.quantity - here
        for entry in moves_in[(item.location, item.key)]:
            take = min(rest, entry[1])
            if take > 0:
                entry[1] -= take
                rest -= take
                out += _lines(item.location, item.name, take, MOVED, f"in {where(entry[0].from_location)}")
        for entry in substituted[item.key]:
            take = min(rest, entry[1])
            if take > 0:
                entry[1] -= take
                rest -= take
                out += _lines(item.location, item.name, take, OK, f"as {entry[0]}")
        out += _lines(item.location, item.name, rest, MISSING, "missing")
    return _layout(f"[{hull}, {fit_name}]", sections)
