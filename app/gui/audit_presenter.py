"""
What the Audit tree says (design §10.1), kept apart from Tk so it can be tested.

Every function returns Node trees; AuditTab only inserts them. Icons mean the
same thing at every level: ✅ ready, ⚠ ready but needs attention, ❌ not ready.
"""
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from app.models.asset_models import CarriedShip
from app.models.audit_models import (AuditResult, BayResult, PackedShipWarning, RefitMove, RequirementResult,
                                     RequirementStatus, ShipRequirementResult)
from app.models.bay_registry import BAYS

ICONS = {RequirementStatus.PASS: "✅", RequirementStatus.WARN: "⚠", RequirementStatus.FAIL: "❌"}
ESCAPE_BAY_KEY = "escape_bay"
NOT_AUDITED = "📋"
MISSING_ITEMS = "missing_items"     # tree values tag for the node "Add Missing Items to Shopping List" reads

SLOT_LABELS = {"subsystem": "subsystems", "high": "high slots", "mid": "mid slots", "low": "low slots", "rigs": "rigs"}
SPACE_LABELS = {"cargo": "cargo", "drones": "drone bay", "fighters": "fighter bay", "fuel_bay": "fuel bay",
                "fleet_hangar": "fleet hangar"}


@dataclass
class Node:
    text: str
    children: List["Node"] = field(default_factory=list)
    open: bool = False
    values: Tuple = ()
    data: Any = None        # not shown: the result behind a ship row (or a requirement with no ship), for the shopping list


# --- names -----------------------------------------------------------------------------

def named(name: str, custom_name: Optional[str]) -> str:
    """Tempest Fleet Issue "Old Faithful", or just the hull when the ship has no name of its own."""
    return f'{name} "{custom_name}"' if custom_name and custom_name != name else name


def bay_label(bay_key: str) -> str:
    if bay_key in BAYS:
        return BAYS[bay_key].label
    return {"cargo": "Cargo", "fleet_hangar": "Fleet Hangar"}.get(bay_key, bay_key)


# (location or system ID, a name saved with it) -> the name to show; EVEdbLoader.location_label
PlaceLabel = Callable[[int, Optional[str]], str]


def location_text(requirement: dict, place_label: PlaceLabel) -> str:
    """The "where" after the hull: " @ <station or structure>", " @ <system>", or nothing for any location."""
    if requirement.get("location_id"):
        return f" @ {place_label(requirement['location_id'], requirement.get('location_name'))}"
    if requirement.get("system_id"):
        return f" @ {place_label(requirement['system_id'], None)}"
    return ""


# --- one ship ------------------------------------------------------------------------------

def move_text(move: RefitMove) -> str:
    quantity = f"{move.quantity}× {move.name}"
    source = SPACE_LABELS.get(move.from_location)
    destination = SPACE_LABELS.get(move.to_location)
    if move.to_location in SLOT_LABELS and source:
        return f"Fit {quantity} (in {source})"
    if move.from_location in SLOT_LABELS and destination:
        return f"Unfit {quantity} (to {destination})"
    where = lambda loc: SLOT_LABELS.get(loc) or SPACE_LABELS.get(loc) or loc
    return f"Move {quantity} from {where(move.from_location)} to {where(move.to_location)}"


def refit_node(moves: Sequence[RefitMove], nothing_to_buy: bool) -> Node:
    """
    Subsystem changes reshape the whole slot layout, so they lead and the
    module moves are collapsed beneath them (design §9.4).
    """
    subsystem = [m for m in moves if "subsystem" in (m.from_location, m.to_location)]
    modules = [m for m in moves if m not in subsystem]
    if subsystem:
        children = [Node(move_text(m)) for m in subsystem]
        if modules:
            plural = "s" if len(modules) != 1 else ""
            children.append(Node(f"then {len(modules)} module move{plural}", [Node(move_text(m)) for m in modules]))
        return Node("⚠ Reconfigure subsystems", children)
    suffix = " (nothing to buy)" if nothing_to_buy else ""
    return Node(f"⚠ Refit to standard fitting{suffix}", [Node(move_text(m)) for m in moves])


def counted(names: Sequence[str]) -> List[str]:
    counts = Counter(names)
    return [f"{name} ×{n}" if n > 1 else name for name, n in sorted(counts.items())]


def bay_result(ship: ShipRequirementResult, bay_key: str) -> Optional[BayResult]:
    return next((b for b in ship.bay_results if b.bay_key == bay_key), None)


def count_text(name: str, actual: int, required: int) -> str:
    """"Helium Isotopes: 100,000 / 150,000 (missing 50,000)" """
    text = f"{name}: {actual:,} / {required:,}"
    return text + (f" (missing {required - actual:,})" if actual < required else "")


def bay_node(bay: BayResult) -> Node:
    """One bay row under a ship (design §10.1). Bays without requirements have no row."""
    icon = ICONS[bay.status]
    if bay.bay_key == ESCAPE_BAY_KEY:
        children = [Node(f"{s.name} (Missing {s.missing})") for s in bay.shortfalls]
        children += [Node(f"Unexpected: {text}") for text in counted(bay.unexpected)]
        return Node(f"{icon} {bay_label(bay.bay_key)}: {bay.message}", children)
    children = [Node(count_text(c.name, c.actual, c.required)) for c in bay.counts]
    if bay.message:
        children.append(Node(f"⚠ {bay.message}"))
    return Node(f"{icon} {bay_label(bay.bay_key)}", children)


def ship_node(ship: ShipRequirementResult, packed: Dict[int, CarriedShip], where: str = "", notes: str = "",
              fit_name: str = "") -> Node:
    children: List[Node] = []
    # Fuel has its own row (the Fuel Bay), so it isn't repeated under Missing Items or the refit.
    fuel = bay_result(ship, "fuel_bay")
    fuel_types = {c.type_id for c in fuel.counts} if fuel else set()
    shortfalls = [s for s in ship.shortfalls if s.type_id not in fuel_types]
    moves = [m for m in ship.refit_moves if m.to_location != "fuel_bay"]
    if shortfalls:
        children.append(Node(f"{ICONS[RequirementStatus.FAIL]} Missing Items",
                             [Node(f"{s.name} (Missing {s.missing})") for s in shortfalls],
                             values=(MISSING_ITEMS,)))
    if moves:
        nothing_to_buy = not ship.shortfalls and not any(b.status == RequirementStatus.FAIL for b in ship.bay_results)
        children.append(refit_node(moves, nothing_to_buy=nothing_to_buy))
    if ship.substitutions:
        children.append(Node("⚠ Bling", [
            Node(f"{s.fitted_name} fitted instead of {s.expected_name}" + (f" (×{s.quantity})" if s.quantity > 1 else ""))
            for s in ship.substitutions]))
    if ship.unexpected_items:
        children.append(Node("⚠ Unexpected Items", [Node(text) for text in counted(ship.unexpected_items)]))
    carried = packed.get(ship.ship_item_id) if ship.ship_item_id is not None else None
    if carried is not None:
        children.append(Node(f"📦 Packed in {named(carried.carrier_name, carried.carrier_custom_name)} · "
                             f"{bay_label(carried.bay_key)}, unpack before use"))
    children.extend(bay_node(b) for b in ship.bay_results)
    if notes.strip():
        children.append(Node(f"📝 {notes.strip()}"))
    # The ship's own name (or its hull when it has none), then the fitting it's audited against
    label = f"{ICONS[ship.status]} {ship.custom_name or ship.ship_name}" + (f" - {fit_name}" if fit_name else "")
    return Node(label + where, children, data=ship)


# --- one requirement -------------------------------------------------------------------------

def requirement_node(requirement: dict, hull: str, result: Optional[RequirementResult],
                     packed: Dict[int, CarriedShip], ship_location: Callable[[ShipRequirementResult], str],
                     notes: str = "", fit_name: str = "", *, place_label: PlaceLabel, replacing: str = "") -> Node:
    """
    The requirement row names the hull and where; each ship row names the ship and the fitting.
    hull is the fitting in use; replacing names the original's hull when the pilot replaced it.
    """
    where = location_text(requirement, place_label)
    text = f"{hull}{where}" + (f" (replacing {replacing})" if replacing else "")
    if result is None:
        return Node(f"{NOT_AUDITED} {text}")
    if not result.ship_results:
        reason = result.message or "No ship found"
        # The requirement row carries its result, so the shopping list can offer the missing hull.
        return Node(f"{ICONS[result.status]} {text}", [Node(f"{ICONS[RequirementStatus.FAIL]} {reason}")], data=result)
    # Under "Any" location each ship shows where it is (D16).
    any_location = not where
    ships = [ship_node(s, packed, f" · {ship_location(s)}" if any_location else "", notes, fit_name)
             for s in result.ship_results]
    return Node(f"{ICONS[result.status]} {text} · {result.ships_ready} of {result.ships_listed} ready", ships)


# --- characters ------------------------------------------------------------------------------

def character_status(result: Optional[AuditResult], packed: Sequence[PackedShipWarning]) -> Optional[RequirementStatus]:
    """FAIL if a requirement isn't ready, WARN if anything needs attention (packed ships included), else PASS."""
    if result is None:
        return None
    if not result.overall_pass:
        return RequirementStatus.FAIL
    if packed or any(r.status == RequirementStatus.WARN for r in result.requirement_results):
        return RequirementStatus.WARN
    return RequirementStatus.PASS


def character_icon(result: Optional[AuditResult], packed: Sequence[PackedShipWarning]) -> str:
    status = character_status(result, packed)
    return ICONS[status] if status else "⚪"


def packed_line(warning: PackedShipWarning, location_name: str) -> str:
    ship = warning.ship
    quantity = f" ×{ship.quantity}" if ship.quantity > 1 else ""
    line = (f"{named(ship.name, ship.custom_name)}{quantity} in {named(ship.carrier_name, ship.carrier_custom_name)}"
            f" · {bay_label(ship.bay_key)}")
    if location_name:
        line += f" · {location_name}"
    if warning.reason == "exceeds_requirement":
        line += " · more than required"
    return line


def packed_node(character_name: str, warnings: Sequence[PackedShipWarning],
                location_name: Callable[[int], str]) -> Node:
    total = sum(w.ship.quantity for w in warnings)
    return Node(f"📦 Ships still packed: {character_name} ({total})",
                [Node(packed_line(w, location_name(w.ship.location_id))) for w in warnings])


def carried_by_item(carried: Sequence[CarriedShip]) -> Dict[int, CarriedShip]:
    """item ID of each carried ship -> where it's packed, for the ship rows' "unpack before use" note (D7)."""
    return {ship.item_id: ship for ship in carried}


# --- the doctrine's fuel summary (D6, D20) ---------------------------------------------------------

def fuel_summary_node(results: Iterable[RequirementResult]) -> Optional[Node]:
    """
    "⛽ Fuel: 3 of 5 ships ready", totalled per fuel type over every listed ship
    with a fuel requirement, spares included (D20). Each ship counts once.
    Shortfalls are summed per ship, so one ship's surplus never hides another's
    shortfall; fuel aboard but outside the fuel bay is a move, not a shortfall.
    """
    ships: Dict[Any, ShipRequirementResult] = {}
    for result in results:
        for ship in result.ship_results:
            if bay_result(ship, "fuel_bay") is not None:
                ships.setdefault(ship.ship_item_id if ship.ship_item_id is not None else id(ship), ship)
    if not ships:
        return None
    totals: Dict[str, List[int]] = {}           # name -> [required, aboard, short]
    moved: Dict[str, Counter] = {}               # name -> {where: quantity}
    ready = 0
    for ship in ships.values():
        fuel = bay_result(ship, "fuel_bay")
        ready += fuel.status != RequirementStatus.FAIL
        for count in fuel.counts:
            row = totals.setdefault(count.name, [0, 0, 0])
            row[0] += count.required
            row[1] += count.actual
            row[2] += count.missing
        for move in ship.refit_moves:
            if move.to_location == "fuel_bay":
                moved.setdefault(move.name, Counter())[move.from_location] += move.quantity
    lines = []
    for name in sorted(totals):
        required, aboard, short = totals[name]
        text = f"{name}: {required:,} required · {aboard:,} aboard"
        if short:
            text += f" · short {short:,}"
        if name in moved:
            where = ", ".join(f"{q:,} in {SPACE_LABELS.get(loc, loc)}" for loc, q in sorted(moved[name].items()))
            text += f" ({where}, move to the fuel bay)"
        lines.append(Node(text))
    plural = "" if len(ships) == 1 else "s"
    return Node(f"⛽ Fuel: {ready} of {len(ships)} ship{plural} ready", lines)
