"""
The metadata editor's logic (design §6.2, §7.2), kept apart from Tk so it can
be tested. The dialogs in app/gui/dialogs/ only lay out widgets and call these.

Bay text uses EFT item lines: "Name xQty", or "Name" for x1. A line ending in
a bare "x" has no quantity yet: fuel suggestions are written that way so the
pilot has to fill them in, and Save refuses them.
"""
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from app.loaders.fitting_loader import parse_item_line
from app.models.bay_registry import BAYS, CATEGORY_SHIP, ESCAPE_BAY, BayDefinition
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


# --- the whole form -------------------------------------------------------------------------

def build_metadata(bay_texts: Dict[str, str], escape_choice: Optional[str], options: List[EscapeOption],
                   notes: str, sde: Any, existing_escape: Optional[BayRequirement] = None) -> FormResult:
    """
    The dialog's contents -> metadata, errors and warnings. `escape_choice` is
    the dropdown text: "" or None keeps no escape requirement, as does "No escape ship".
    A stored requirement for a deleted fitting is kept as it is until the pilot picks another.
    """
    result = FormResult()
    bays: Dict[str, List[BayRequirement]] = {}
    for key, text in bay_texts.items():
        requirements = parse_bay_text(BAYS[key], text, sde, result)
        if requirements:
            bays[key] = requirements
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
