"""
What the Audit tree says (design §10.1), kept apart from Tk so it can be tested.

Every function returns Node trees; AuditTab only inserts them. Icons mean the
same thing at every level: ✅ ready, ⚠ ready but needs attention, ❌ not ready.
"""
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from app.loaders.role_manager import requirement_priority
from app.models.asset_models import CarriedShip
from app.models.audit_models import (AuditResult, BayResult, ImplantSlotResult, PackedShipWarning, RefitMove,
                                     RequirementResult, RequirementStatus, ShipRequirementResult)
from app.models.bay_registry import BAYS
from app.services.audit.configuration import REMOVE

ICONS = {RequirementStatus.PASS: "✅", RequirementStatus.WARN: "⚠", RequirementStatus.FAIL: "❌",
         RequirementStatus.NOT_CHECKED: "⚪"}         # a hull with no fitting assigned (A4)
ESCAPE_BAY_KEY = "escape_bay"
NOT_AUDITED = "📋"
MISSING_ITEMS = "missing_items"     # tree values tag for the node "Add Missing Items to Shopping List" reads

SLOT_LABELS = {"subsystem": "subsystems", "high": "high slots", "mid": "mid slots", "low": "low slots", "rigs": "rigs"}
SPACE_LABELS = {"cargo": "cargo", "drones": "drone bay", "fighters": "fighter bay", "fighter_tubes": "fighter tubes",
                "fuel_bay": "fuel bay",
                "fleet_hangar": "fleet hangar"}


@dataclass
class Node:
    text: str
    children: List["Node"] = field(default_factory=list)
    open: bool = False
    values: Tuple = ()
    data: Any = None        # not shown: the result behind a ship row (or a requirement with no ship), for the shopping list
    tone: Optional[str] = None      # "hard" or "soft" on a failing row: the tree tints it (plan D6)
    priority: Optional[str] = None  # on a ship row: its requirement's ("hard" or "soft"), for Add Every (P7)


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


def area_text(requirement: dict, place_label: PlaceLabel) -> str:
    """Where a requirement applies, as a name: its station, its system, or "any system"."""
    return location_text(requirement, place_label)[len(" @ "):] or "any system"


def priority_text(requirement: dict, result: Optional[RequirementResult], place_label: PlaceLabel) -> str:
    """" (soft)", " (soft · covered by Jita)" when a wider hard requirement locks it (P4), or nothing."""
    covered = result.covered_by if result is not None else None
    if covered is not None:
        return f" (soft · covered by {area_text(covered, place_label)})"
    return " (soft)" if requirement_priority(requirement) == "soft" else ""


# --- one ship ------------------------------------------------------------------------------

def move_text(move: RefitMove) -> str:
    quantity = f"{move.quantity}× {move.name}"
    source = SPACE_LABELS.get(move.from_location)
    destination = SPACE_LABELS.get(move.to_location)
    if move.to_location in SLOT_LABELS and source:
        return f"Fit {quantity} (in {source})"
    if move.to_location == REMOVE:
        return f"Remove {quantity} ({SLOT_LABELS.get(move.from_location) or SPACE_LABELS.get(move.from_location, move.from_location)})"
    if move.from_location in SLOT_LABELS and destination:
        return f"Stow {quantity} (to {destination})"
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
        children = [Node(f"{s.name} (Missing {s.missing})", data=s) for s in bay.shortfalls]
        children += [Node(f"Unexpected: {text}") for text in counted(bay.unexpected)]
        return Node(f"{icon} {bay_label(bay.bay_key)}: {bay.message}", children)
    children = [Node(count_text(c.name, c.actual, c.required)) for c in bay.counts]
    if bay.message:
        children.append(Node(f"⚠ {bay.message}"))
    return Node(f"{icon} {bay_label(bay.bay_key)}", children)


def placement_text(ship: ShipRequirementResult) -> str:
    """What a bound ship's row adds after its name (tracked ships, plan 14.4); nothing for spares."""
    if not ship.bound:
        return ""
    holder = (ship.holder or {}).get("name", "")
    if ship.placement == "HOME":
        corporation = (ship.holder or {}).get("kind") == "corporation"
        return " · 📌 Home" + (f" ({holder})" if corporation and holder else "")
    if ship.placement == "AWAY":
        return " · ↗ Away: " + ", ".join(p for p in (holder, ship.where) if p) + " (no Home set)"
    if ship.placement == "IN_SYSTEM":
        return f" · ↔ In the system: {ship.placement_note}"
    if ship.placement == "DEPLOYED":
        return " · ↗ Deployed: " + ", ".join(p for p in (holder, ship.where) if p) + f", {ship.placement_note}"
    if ship.placement == "MISSING":
        return loss_text(ship.loss) if ship.loss else f" · ❓ Missing since {(ship.missing_since or '')[:10]}"
    return ""


def loss_text(loss: dict) -> str:
    """A missing ship a killmail matched (ESI features plan 30.3)."""
    insured = f" · insurance pays ≈ {loss['insurance']}" if loss.get("insurance") else ""
    if loss.get("state") == "lost":
        return f" · 💥 Lost {loss['date']}: replace{insured}"
    return f" · ❓ Possibly lost {loss['date']} (right-click ▸ This One Was Lost){insured}"


def ship_node(ship: ShipRequirementResult, packed: Dict[int, CarriedShip], where: str = "", notes: str = "",
              fit_name: str = "", priority: str = "hard") -> Node:
    """priority: the requirement's ("hard" or "soft"); a failing ship row is tinted with it."""
    children: List[Node] = []
    tone = priority if ship.status == RequirementStatus.FAIL else None
    if ship.placement == "MISSING":
        last = ship.last_seen or {}
        seen = f"Last seen {last.get('where')}" if last.get("where") else "Not seen since it was bound"
        if (last.get("holder") or {}).get("name"):
            seen += f", held by {last['holder']['name']}"
        children.append(Node(f"❓ {seen}"))
        children.append(Node("❌ Replacement (add by hand)",
                             [Node(f"{s.name} (Missing {s.missing})", data=s) for s in ship.shortfalls],
                             values=(MISSING_ITEMS,), tone=priority))
        label = f"{ICONS[ship.status]} {ship.custom_name or ship.ship_name}" + (f" - {fit_name}" if fit_name else "")
        return Node(label + placement_text(ship), children, data=ship, tone=tone)
    # Fuel has its own row (the Fuel Bay), so it isn't repeated under Missing Items or the refit.
    fuel = bay_result(ship, "fuel_bay")
    fuel_types = {c.type_id for c in fuel.counts} if fuel else set()
    shortfalls = [s for s in ship.shortfalls if s.type_id not in fuel_types]
    moves = [m for m in ship.refit_moves if m.to_location != "fuel_bay"]
    # Fitted modules the fitting doesn't want go in one Remove list with the unexpected items
    # (plan 17.4); a subsystem stays with the subsystem reconfiguration it belongs to.
    removals = [m for m in moves if m.to_location == REMOVE and m.from_location != "subsystem"]
    moves = [m for m in moves if m not in removals]
    if shortfalls:
        children.append(Node(f"{ICONS[RequirementStatus.FAIL]} Missing Items",
                             [Node(f"{s.name} (Missing {s.missing})", data=s) for s in shortfalls],
                             values=(MISSING_ITEMS,), tone=priority))
    if moves:
        nothing_to_buy = not ship.shortfalls and not any(b.status == RequirementStatus.FAIL for b in ship.bay_results)
        children.append(refit_node(moves, nothing_to_buy=nothing_to_buy))
    if ship.substitutions:
        children.append(Node("⚠ Bling", [
            Node(f"{s.fitted_name} fitted instead of {s.expected_name}" + (f" (×{s.quantity})" if s.quantity > 1 else ""))
            for s in ship.substitutions]))
    if removals or ship.unexpected_items:
        children.append(Node("⚠ Remove from ship", [Node(move_text(m)) for m in removals]
                             + [Node(text) for text in counted(ship.unexpected_items)]))
    carried = packed.get(ship.ship_item_id) if ship.ship_item_id is not None else None
    if carried is not None:
        children.append(Node(f"📦 Packed in {named(carried.carrier_name, carried.carrier_custom_name)} · "
                             f"{bay_label(carried.bay_key)}, unpack before use"))
    children.extend(bay_node(b) for b in ship.bay_results)
    if notes.strip():
        children.append(Node(f"📝 {notes.strip()}"))
    # The ship's own name (or its hull when it has none), then the fitting it's audited against
    label = f"{ICONS[ship.status]} {ship.custom_name or ship.ship_name}" + (f" - {fit_name}" if fit_name else "")
    if ship.placement in ("AWAY", "DEPLOYED"):
        where = ""                          # the placement says where it is
    return Node(label + where + placement_text(ship), children, data=ship, tone=tone)


# --- one requirement -------------------------------------------------------------------------

def requirement_node(requirement: dict, hull: str, result: Optional[RequirementResult],
                     packed: Dict[int, CarriedShip], ship_location: Callable[[ShipRequirementResult], str],
                     notes: str = "", fit_name: str = "", *, place_label: PlaceLabel, replacing: str = "") -> Node:
    """
    The requirement row names the hull and where; each ship row names the ship and the fitting.
    hull is the fitting in use; replacing names the original's hull when the pilot replaced it.
    """
    where = location_text(requirement, place_label)
    text = f"{hull}{where}" + (f" (replacing {replacing})" if replacing else "") + priority_text(requirement, result, place_label)
    if result is None:
        return Node(f"{NOT_AUDITED} {text}")
    if result.implant_set is not None:
        node = implant_set_node(text, result)
        node.tone = result.failure
        return node
    skills = skill_nodes(result)
    if not result.ship_results:
        reason = result.message or "No ship found"
        # The requirement row carries its result, so the shopping list can offer the missing hull
        # (and, when NOT CHECKED, the hulls to assign: right-click).
        not_checked = result.status == RequirementStatus.NOT_CHECKED
        icon = ICONS[RequirementStatus.NOT_CHECKED if not_checked else RequirementStatus.FAIL]
        return Node(f"{ICONS[shown_status(result)]} {text}",
                    skills + [Node(f"{icon} {reason}", tone=None if not_checked else result.priority)],
                    data=result, tone=shown_tone(result))
    # Under "Any" location each ship shows where it is (D16).
    any_location = not where
    ships = [ship_node(s, packed, f" · {ship_location(s)}" if any_location else "", notes, fit_name, result.priority)
             for s in result.ship_results]
    for ship in ships:
        ship.priority = result.priority
    if result.unassigned_hulls:
        # A missing ship, and hulls with no fitting that may be its replacement (23.4): right-click to assign.
        ships.insert(0, Node(f"{ICONS[RequirementStatus.NOT_CHECKED]} {result.message}"))
    return Node(f"{ICONS[shown_status(result)]} {text} · {result.ships_ready} of {result.ships_listed} ready",
                skills + ships, tone=shown_tone(result), data=result if result.unassigned_hulls else None)


# --- the skill check (ESI features plan 26.5) -------------------------------------------------------

def shown_status(result: RequirementResult) -> RequirementStatus:
    """The requirement row's icon: its ships' status, made worse by skills the pilot is missing."""
    failure = result.skill_failure
    if failure == "hard":
        return RequirementStatus.FAIL
    if failure == "soft" and result.status == RequirementStatus.PASS:
        return RequirementStatus.WARN
    return result.status


def shown_tone(result: RequirementResult) -> Optional[str]:
    tones = (result.failure, result.skill_failure)
    return "hard" if "hard" in tones else ("soft" if "soft" in tones else None)


def skill_nodes(result: RequirementResult) -> List[Node]:
    """
    One line when the pilot is missing skills: "❌ Can't fly: Carriers V (has IV)" for the hull or
    fitted modules (D1.1), "⚠ Skills to train: …" for the rest. Its data is the check, for Copy Skill Plan.
    """
    check = result.skills
    if result.skill_state != "checked" or check is None or check.ok:
        return []
    failure = result.skill_failure
    icon = ICONS[RequirementStatus.FAIL if failure == "hard" else RequirementStatus.WARN]
    if check.hard and check.soft:
        text = f"Can't fly: {check.summary(parts=('hard',))}; to train: {check.summary(2, ('soft',))}"
    elif check.hard:
        text = f"Can't fly: {check.summary()}"
    else:
        text = f"Skills to train: {check.summary()}"
    return [Node(f"{icon} {text}", data=check, tone=failure)]


def skills_note(results: Iterable[RequirementResult]) -> Optional[Node]:
    """A character whose skills haven't been pulled: one line, not one per requirement."""
    if any(r is not None and r.skill_state == "unchecked" for r in results):
        return Node(f"{ICONS[RequirementStatus.NOT_CHECKED]} Skills not checked: pull with a login that includes "
                    "skills (Characters ▸ Add Character again if it's older)")
    return None


def implant_set_node(text: str, result: RequirementResult) -> Node:
    """
    An implant set requirement (plan 15.3): where its clone is, then a line for each slot
    that isn't right. The row carries its result: right-click adds a missing set's implants.
    """
    implant_set = result.implant_set
    lines = [Node(f"{ICONS[result.status]} {result.message}")]
    if implant_set.placement == "MISSING" and implant_set.clone:
        closest = implant_set.clone + (f" in {implant_set.where}" if implant_set.where else "")
        lines.append(Node(f"Closest: {closest}"))
    lines += [Node(slot_text(s)) for s in implant_set.slots if s.status != RequirementStatus.PASS]
    return Node(f"{ICONS[result.status]} {text}", lines, data=result)


def slot_text(slot: ImplantSlotResult) -> str:
    """'❌ Slot 5: empty (needs High-grade Amulet Epsilon)', '⚠ Slot 10: ... HG-1006 (1008 preferred)'."""
    worn = slot.worn_name or "empty"
    reason = f"needs {slot.listed_name}" if slot.reason == "empty" else slot.reason
    return f"{ICONS[slot.status]} Slot {slot.slot}: {worn} ({reason})"


# --- characters ------------------------------------------------------------------------------

def character_status(result: Optional[AuditResult], packed: Sequence[PackedShipWarning]) -> Optional[RequirementStatus]:
    """
    FAIL if a hard requirement isn't ready, WARN if anything needs attention (a soft requirement
    that isn't ready, packed ships), else PASS. A soft requirement never makes it worse than WARN (P2).
    """
    if result is None:
        return None
    if not result.overall_pass:
        failed = any(r.failure == "hard" or r.skill_failure == "hard" for r in result.requirement_results)
        return RequirementStatus.FAIL if failed else RequirementStatus.NOT_CHECKED
    attention = any(r.status == RequirementStatus.WARN or r.failure == "soft" or r.skill_failure == "soft"
                    or (not r.counts_for_readiness and r.status != RequirementStatus.PASS)
                    for r in result.requirement_results)
    if packed or attention:
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
    if warning.reason == "wrong_fit":           # plan 20.2: the bay names a saved fitting
        line += f" · should be {warning.fit_name}: {warning.detail}"
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
