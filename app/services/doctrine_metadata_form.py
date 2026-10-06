"""
The metadata editor's logic (design §6.2, §7.2), kept apart from Tk so it can
be tested. The dialogs in app/gui/dialogs/ only lay out widgets and call these.

Bay text uses EFT item lines: "Name xQty", or "Name" for x1. A line ending in
a bare "x" has no quantity yet: fuel suggestions are written that way so the
pilot has to fill them in, and Save refuses them.
"""
import math
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from app.loaders.fitting_loader import parse_item_line
from app.models.bay_registry import BAYS, CATEGORY_SHIP, ESCAPE_BAY, SHIP_MAINTENANCE_BAY, BayDefinition
from app.models.doctrine_metadata import BayRequirement, DoctrineMetadata

NO_ESCAPE_SHIP = "No escape ship"
ANY_ESCAPE_SHIP = "Any ship"
FIT_SLOTS = ("high", "mid", "low", "rigs", "subsystem")
MISSING_QUANTITY = re.compile(r"\s+x\s*$|^\s*x\s*$")
NEGATIVE_QUANTITY = re.compile(r"\s+x-\d+\s*$")


# --- which bays, which prompt ----------------------------------------------------------------

def hull_type_id_of(fitting: Dict[str, Any], sde: Any) -> Optional[int]:
    fit = fitting.get("fit") if isinstance(fitting.get("fit"), dict) else {}
    return fitting.get("hull_type_id") or fit.get("hull_type_id") or (
        sde.get_typeid_by_name(fitting["hull"]) if fitting.get("hull") else None)


def metadata_bays(fitting: Dict[str, Any], sde: Any) -> List[Tuple[BayDefinition, float]]:
    """(bay, capacity) for each metadata bay the fitting's hull has, in registry order."""
    hull_type_id = hull_type_id_of(fitting, sde)
    if not hull_type_id:
        return []
    return [(BAYS[key], capacity) for key, capacity in sde.get_hull_bays(hull_type_id).items()
            if BAYS[key].source == "metadata"]


def prompt_kind(fitting: Dict[str, Any], sde: Any) -> Optional[str]:
    """
    What to offer after an import (§6.2): None (no metadata bays, e.g. a Devoter),
    "escape" when the escape bay is the only one (battleships, Marauders), else "editor".
    """
    keys = [bay.key for bay, _ in metadata_bays(fitting, sde)]
    if not keys:
        return None
    return "escape" if keys == [ESCAPE_BAY.key] else "editor"


def bay_list_text(bays: List[Tuple[BayDefinition, float]]) -> str:
    """"a Fuel Bay, Fleet Hangar and Ship Maintenance Bay" """
    labels = [bay.label for bay, _ in bays]
    joined = labels[0] if len(labels) == 1 else ", ".join(labels[:-1]) + " and " + labels[-1]
    return f"{'an' if joined[0] in 'AEIOU' else 'a'} {joined}"


def capacity_text(bay: BayDefinition, capacity: float) -> str:
    if bay.key == ESCAPE_BAY.key:
        return f"{capacity:,.0f} ship" + ("" if capacity == 1 else "s")
    return f"{capacity:,.0f} m³"


# --- text <-> requirements -------------------------------------------------------------------

def requirements_text(requirements: List[BayRequirement]) -> str:
    return "\n".join(r.name if r.min_quantity == 1 else f"{r.name} x{r.min_quantity}" for r in requirements)


def fuel_suggestions(fitting: Dict[str, Any], sde: Any) -> Tuple[str, str]:
    """
    (text for an empty fuel bay box, hint shown beside it): the hull's jump fuel
    and the fuel of each fitted module that burns one, quantities left blank (D6).
    """
    lines: List[str] = []
    hints: List[str] = []
    hull_type_id = hull_type_id_of(fitting, sde)
    jump = sde.get_jump_fuel(hull_type_id) if hull_type_id else None
    if jump:
        lines.append(f"{jump.name} x")
        hints.append(f"Jump fuel: {jump.name}, {jump.quantity:,.0f} per light year (base)")
    fit = fitting.get("fit") if isinstance(fitting.get("fit"), dict) else {}
    for slot in FIT_SLOTS:
        for type_id, item in (fit.get(slot) or {}).items():
            if not str(type_id).isdigit():
                continue
            use = sde.get_module_fuel(int(type_id))
            if use is None:
                continue
            hints.append(f"{item.get('name', type_id)}: {use.name}, {use.quantity:,.0f} per cycle (base)")
            if f"{use.name} x" not in lines:
                lines.append(f"{use.name} x")
    return "\n".join(lines), "\n".join(hints)


# --- fuel bay sliders (UI thoughts plan 20.1, U9) ---------------------------------------------

@dataclass
class FuelRow:
    """One fuel in the editor: a plain amount, set with a slider or typed."""
    type_id: int
    name: str
    volume: Optional[float]         # m³ per unit; None when the database doesn't know (no cap)
    quantity: int = 0


def suggested_fuels(fitting: Dict[str, Any], sde: Any) -> List[Tuple[int, str]]:
    """(type_id, name): the hull's jump fuel, then each fitted module's fuel, once each."""
    found: List[Tuple[int, str]] = []
    hull_type_id = hull_type_id_of(fitting, sde)
    jump = sde.get_jump_fuel(hull_type_id) if hull_type_id else None
    if jump:
        found.append((jump.type_id, jump.name))
    fit = fitting.get("fit") if isinstance(fitting.get("fit"), dict) else {}
    for slot in FIT_SLOTS:
        for type_id in (fit.get(slot) or {}):
            use = sde.get_module_fuel(int(type_id)) if str(type_id).isdigit() else None
            if use is not None and all(use.type_id != t for t, _ in found):
                found.append((use.type_id, use.name))
    return found


def fuel_rows(fitting: Dict[str, Any], saved: List[BayRequirement], sde: Any) -> List[FuelRow]:
    """The saved fuels with their amounts, then the suggested ones not saved yet, at 0."""
    rows = [FuelRow(r.type_id, r.name, sde.get_type_volume(r.type_id), r.min_quantity) for r in saved if r.type_id]
    for type_id, name in suggested_fuels(fitting, sde):
        if all(r.type_id != type_id for r in rows):
            rows.append(FuelRow(type_id, name, sde.get_type_volume(type_id)))
    return rows


def fuel_used(rows: List[FuelRow], skip: Optional[int] = None) -> float:
    """m³ the fuels take (all but row `skip`)."""
    return sum(r.volume * r.quantity for i, r in enumerate(rows) if i != skip and r.volume)


def fuel_max(rows: List[FuelRow], index: int, capacity: float) -> Optional[int]:
    """The most of fuel `index` that fits beside the others; None when its volume isn't known."""
    volume = rows[index].volume
    if not volume:
        return None
    return max(0, int(math.floor((capacity - fuel_used(rows, skip=index)) / volume + 1e-6)))


def fuel_text(rows: List[FuelRow]) -> str:
    """The rows as bay text for build_metadata: fuels at 0 aren't required."""
    return "\n".join(f"{r.name} x{r.quantity}" for r in rows if r.quantity > 0)


def fuel_warnings(rows: List[FuelRow], capacity: float) -> List[str]:
    found = []
    if rows and not any(r.quantity > 0 for r in rows):
        found.append("No fuel amount set: the Fuel Bay won't be checked.")
    if fuel_used(rows) > capacity + 1e-6:
        found.append(f"The fuels take {fuel_used(rows):,.0f} m³, more than the Fuel Bay's {capacity:,.0f} m³.")
    return found


@dataclass
class FormResult:
    metadata: DoctrineMetadata = field(default_factory=DoctrineMetadata)
    errors: List[str] = field(default_factory=list)       # block Save
    warnings: List[str] = field(default_factory=list)     # shown, don't block

    @property
    def ok(self) -> bool:
        return not self.errors


def parse_bay_text(bay: BayDefinition, text: str, sde: Any, result: FormResult) -> List[BayRequirement]:
    """One bay's text box -> requirements; problems are added to `result`."""
    requirements = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        where = f"Line {number} ({bay.label})"
        if MISSING_QUANTITY.search(line):
            result.errors.append(f'{where}: "{MISSING_QUANTITY.sub("", line)}" needs a quantity')
            continue
        negative = NEGATIVE_QUANTITY.search(line)
        name, quantity = (line[:negative.start()].strip(), -1) if negative else parse_item_line(line)
        type_id = sde.get_typeid_by_name(name)
        if not type_id:
            result.errors.append(f'{where}: "{name}" isn\'t in the EVE database')
            continue
        if quantity <= 0:
            result.errors.append(f'{where}: "{name}" has a quantity of 0 or less')
            continue
        warning = category_warning(bay, name, sde.get_type_category(type_id))
        if warning:
            result.warnings.append(f"{where}: {warning}")
        requirements.append(BayRequirement(type_id, name, quantity))
    return requirements


def category_warning(bay: BayDefinition, name: str, category: Optional[int]) -> Optional[str]:
    """Something unusual for the bay (§7.2). The fleet hangar takes anything except ships."""
    if bay.pooled_with_cargo:
        if category == CATEGORY_SHIP:
            return f'"{name}" is a ship; ships usually go in the Ship Maintenance Bay.'
        return None
    if category in bay.expected_categories:
        return None
    if CATEGORY_SHIP in bay.expected_categories:
        return f'"{name}" isn\'t a ship; the {bay.label} holds ships.'
    if category == CATEGORY_SHIP:
        return f'"{name}" is a ship; ships usually go in the Ship Maintenance Bay.'
    return f'"{name}" isn\'t what a {bay.label} usually holds.'


# --- escape bay ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EscapeOption:
    label: str
    requirement: Optional[BayRequirement]      # None for "No escape ship"


def escape_options(fitting: Dict[str, Any], fitting_manager: Any, sde: Any) -> List[EscapeOption]:
    """The escape bay dropdown (§7.2): no ship, any ship, then every candidate fitting as "Hull - Fit name"."""
    options = [EscapeOption(NO_ESCAPE_SHIP, None), EscapeOption(ANY_ESCAPE_SHIP, BayRequirement.any_ship())]
    for record in fitting_manager.list_escape_candidates(fitting["fit_uid"], sde):
        options.append(EscapeOption(f"{record['hull']} - {record['fit_name']}",
                                    BayRequirement(record.get("hull_type_id"), record["fit_name"], 1,
                                                   match="fit", fit_uid=record["fit_uid"])))
    return options


def escape_label(requirement: Optional[BayRequirement], options: List[EscapeOption]) -> str:
    """The dropdown text for a stored escape requirement ("" when none is stored)."""
    if requirement is None:
        return ""
    if requirement.match == "any_ship":
        return ANY_ESCAPE_SHIP
    for option in options:
        if option.requirement is not None and option.requirement.fit_uid == requirement.fit_uid:
            return option.label
    return f"(deleted fitting) {requirement.name}"


def filter_options(options: List[EscapeOption], typed: str) -> List[str]:
    """Labels matching what's typed, like the Library's System box. The two fixed choices always stay."""
    value = typed.strip().casefold()
    return [o.label for o in options
            if o.label in (NO_ESCAPE_SHIP, ANY_ESCAPE_SHIP) or not value or value in o.label.casefold()]


# --- ship maintenance bay entries (UI thoughts plan 20.2, U10) --------------------------------------

ANY_FITTING = "(any fitting)"


@dataclass(frozen=True)
class CarriedOption:
    """One choice in the + chooser: a hull with any fitting, or a saved fitting of it."""
    label: str                  # "Pilgrim (any fitting)", "Pilgrim - SNUFF Pilgrim"
    type_id: int
    hull: str
    fit_uid: Optional[int] = None
    fit_name: str = ""


def carried_options(fitting: Dict[str, Any], fitting_manager: Any, capacity: float, sde: Any) -> List[CarriedOption]:
    """Each hull that fits in the bay and has a saved fitting: "any fitting" first, then its fittings."""
    by_hull: Dict[Tuple[str, int], List[dict]] = {}
    for record in fitting_manager.list_carried_candidates(fitting["fit_uid"], capacity, sde):
        hull_type_id = record.get("hull_type_id") or sde.get_typeid_by_name(record.get("hull", ""))
        by_hull.setdefault((record.get("hull", ""), hull_type_id), []).append(record)
    options: List[CarriedOption] = []
    for (hull, type_id), records in sorted(by_hull.items(), key=lambda h: h[0][0].casefold()):
        options.append(CarriedOption(f"{hull} {ANY_FITTING}", type_id, hull))
        options += [CarriedOption(f"{hull} - {r['fit_name']}", type_id, hull, r["fit_uid"], r["fit_name"])
                    for r in records]
    return options


def carried_requirement(option: CarriedOption, quantity: int) -> BayRequirement:
    if option.fit_uid is None:
        return BayRequirement(option.type_id, option.hull, quantity)
    return BayRequirement(option.type_id, option.fit_name, quantity, match="fit", fit_uid=option.fit_uid)


def carried_label(requirement: BayRequirement, fitting_manager: Any, sde: Any) -> str:
    """"2× Pilgrim - SNUFF Pilgrim", "1× Devoter (any fitting)", a deleted fitting marked."""
    count = f"{requirement.min_quantity}×"
    hull = sde.get_type_name(requirement.type_id) if requirement.type_id else "?"
    if requirement.match != "fit":
        return f"{count} {requirement.name or hull} {ANY_FITTING}"
    named = fitting_manager.get_fitting(requirement.fit_uid) if requirement.fit_uid is not None else None
    if named is None:
        return f"{count} {hull} - (deleted fitting) {requirement.name}: any fitting is accepted"
    return f"{count} {hull} - {named.get('fit_name', requirement.name)}"


def carried_warnings(requirements: List[BayRequirement], capacity: float, sde: Any) -> List[str]:
    volume = sum((sde.get_type_volume(r.type_id) or 0) * r.min_quantity for r in requirements if r.type_id)
    if volume > capacity + 1e-6:
        return [f"The carried ships take {volume:,.0f} m³, more than the Ship Maintenance Bay's {capacity:,.0f} m³."]
    return []


# --- fighter tubes (UI thoughts plan 20.3, U11) -------------------------------------------------

@dataclass
class TubeRow:
    """A fighter type in the fit: how many full squadrons go in the tubes."""
    type_id: int
    name: str
    listed: int                 # fighters of the type the fit lists
    squadron_size: int
    squadrons: int = 0


def tube_rows(fitting: Dict[str, Any], saved: Dict[int, int], sde: Any) -> List[TubeRow]:
    """Each fighter type the fit lists, with its saved pre-load (0 when none)."""
    fit = fitting.get("fit") if isinstance(fitting.get("fit"), dict) else {}
    rows = []
    for type_id, item in (fit.get("fighters") or {}).items():
        if str(type_id).isdigit():
            rows.append(TubeRow(int(type_id), item.get("name") or sde.get_type_name(int(type_id)),
                                int(item.get("quantity", 0) or 0), sde.get_squadron_size(int(type_id)),
                                int(saved.get(int(type_id), 0))))
    return sorted(rows, key=lambda r: r.name.casefold())


def tube_problems(rows: List[TubeRow], tube_count: int, hull: str) -> List[str]:
    """Errors: more squadrons than tubes, or more than the fit lists of a type."""
    found = []
    total = sum(r.squadrons for r in rows)
    if total > tube_count:
        found.append(f"Fighter tubes: {total} squadrons pre-loaded, but the {hull} has {tube_count} tubes")
    for r in rows:
        if r.squadrons * r.squadron_size > r.listed:
            found.append(f"Fighter tubes: {r.squadrons} full squadrons of {r.name} need {r.squadrons * r.squadron_size} "
                         f"fighters; the fit lists {r.listed}")
    return found


# --- the whole form -------------------------------------------------------------------------

def build_metadata(bay_texts: Dict[str, str], escape_choice: Optional[str], options: List[EscapeOption],
                   notes: str, sde: Any, existing_escape: Optional[BayRequirement] = None,
                   carried: Optional[List[BayRequirement]] = None) -> FormResult:
    """
    The dialog's contents -> metadata, errors and warnings. `escape_choice` is
    the dropdown text: "" or None keeps no escape requirement, as does "No escape ship".
    A stored requirement for a deleted fitting is kept as it is until the pilot picks another.
    `carried`: the Ship Maintenance Bay's entries from its list (plan 20.2), in place of text.
    """
    result = FormResult()
    bays: Dict[str, List[BayRequirement]] = {}
    for key, text in bay_texts.items():
        requirements = parse_bay_text(BAYS[key], text, sde, result)
        if requirements:
            bays[key] = requirements
    if carried:
        bays[SHIP_MAINTENANCE_BAY.key] = [BayRequirement(**vars(r)) for r in carried]
    choice = (escape_choice or "").strip()
    if choice and choice != NO_ESCAPE_SHIP:
        option = next((o for o in options if o.label == choice), None)
        if option is not None:
            bays[ESCAPE_BAY.key] = [option.requirement]
        elif existing_escape is not None and choice == escape_label(existing_escape, options):
            bays[ESCAPE_BAY.key] = [existing_escape]
        else:
            result.errors.append(f'{ESCAPE_BAY.label}: "{choice}" isn\'t one of the choices')
    result.metadata = DoctrineMetadata(bays=bays, notes=notes.strip())
    return result
