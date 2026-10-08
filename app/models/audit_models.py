from dataclasses import dataclass, field
from typing import List, Dict, Any, Literal, Optional
from enum import Enum

from app.models.asset_models import CarriedShip

class RequirementStatus(Enum):
    PASS = "PASS"
    WARN = "WARN"   # ready, but needs attention: refit, substitute, unexpected item, still packed
    FAIL = "FAIL"
    NOT_CHECKED = "NOT_CHECKED"     # a hull is there but has no fitting assigned: not ready, not a failure (A4)


def is_ready(status: "RequirementStatus") -> bool:
    """PASS and WARN both count as ready (design §9.4)."""
    return status in (RequirementStatus.PASS, RequirementStatus.WARN)


@dataclass
class ItemShortfall:
    """An item the fitting (or its metadata) needs that isn't aboard in full."""
    type_id: int
    name: str
    required: int
    actual: int

    @property
    def missing(self) -> int:
        return max(0, self.required - self.actual)


@dataclass
class RefitMove:
    """
    One move that brings a ship back to its standard fitting (design §9.2-9.3).
    Locations are slot categories ("high", "mid", "low", "rigs", "subsystem"),
    "drones", "fighters", "cargo" or a bay key.
    """
    type_id: int
    name: str
    quantity: int
    from_location: str
    to_location: str


@dataclass
class Substitution:
    """Bling: a better version aboard in place of the fitting's module (a warning, never bought)."""
    expected_type_id: int
    expected_name: str
    fitted_type_id: int
    fitted_name: str
    quantity: int


@dataclass
class BayResult:
    """The result for one registry bay on one ship."""
    bay_key: str
    status: RequirementStatus
    shortfalls: List[ItemShortfall] = field(default_factory=list)
    unexpected: List[str] = field(default_factory=list)
    message: str = ""                    # e.g. "expected B B C / Astero (Escape), found Heron"
    # Every requirement's count, short or not: required vs aboard (the fuel summary needs both).
    counts: List[ItemShortfall] = field(default_factory=list)


@dataclass
class EftItem:
    """An item aboard, or one the fitting expects, at one location (the EFT view, plan 19.2)."""
    location: str           # "high", "mid", "low", "rigs", "subsystem", "drones", "fighters", "cargo", "fuel_bay"
    type_id: int
    key: int                # the equivalence key the audit counted it by
    name: str
    quantity: int


@dataclass
class PackedShipWarning:
    """A ship still packed inside a carrier that the carrier's metadata doesn't call for (design §9.5),
    or one it calls for with a saved fitting that the ship doesn't match (plan 20.2, U10)."""
    ship: CarriedShip
    reason: Literal["not_in_metadata", "exceeds_requirement", "wrong_fit"]
    fit_name: str = ""          # wrong_fit: the fitting the carrier's maintenance bay names
    detail: str = ""            # wrong_fit: "missing 2× Small Remote Armor Repairer II", "packaged"

@dataclass
class ShipRequirementResult:
    """
    Represents the result of a single ship's compliance with a requirement.
    """
    ship_name: str
    status: RequirementStatus
    custom_name: Optional[str] = None
    unexpected_items: List[str] = field(default_factory=list)
    # V2 detail (filled from step 1.6)
    ship_item_id: Optional[int] = None
    location_id: Optional[int] = None
    carrier_item_id: Optional[int] = None       # set when the ship is packed inside another ship
    shortfalls: List[ItemShortfall] = field(default_factory=list)
    refit_moves: List[RefitMove] = field(default_factory=list)
    substitutions: List[Substitution] = field(default_factory=list)
    bay_results: List[BayResult] = field(default_factory=list)
    # Assigned ships (UI thoughts plan 18.3). Without a tracking context every ship is UNBOUND.
    # HOME, AWAY (no Home: before Homes), IN_SYSTEM and DEPLOYED (Home S, elsewhere: move it, plan 23.3),
    # MISSING, or UNBOUND (no assignments known)
    placement: str = "UNBOUND"
    placement_note: str = ""                    # IN_SYSTEM / DEPLOYED: "move it to Jita IV-4", "bring it back to Jita"
    # A ship with no Home (assigned before Homes, H7): the system it's in, offered as its Home (Adopt, 23.4).
    adopt_system_id: Optional[int] = None
    bound: bool = False                         # assigned this requirement's fitting and owned by the character
    holder: Optional[Dict[str, Any]] = None     # {"kind": "character"|"corporation", "id", "name"}
    where: str = ""                             # "Jita IV - Moon 4 - Caldari Navy Assembly Plant (deliveries)"
    last_seen: Optional[Dict[str, Any]] = None  # a missing ship's last sighting
    missing_since: Optional[str] = None
    # Losses (ESI features plan 30.3): a missing ship a killmail matched: {"state": "lost" or "possibly",
    # "date", "killmail_id", "insurance"?}. Set by the Doctrines tab after the audit.
    loss: Optional[Dict[str, Any]] = None
    # What the audit compared, for the EFT view (plan 19.2): aboard, expected, and what aboard
    # the fitting doesn't call for at all.
    contents: List[EftItem] = field(default_factory=list)
    expected: List[EftItem] = field(default_factory=list)
    unexpected_aboard: List[EftItem] = field(default_factory=list)

@dataclass
class ImplantSlotResult:
    """One implant slot of a set checked against a clone (TRACKED_ITEMS_DESIGN.md §5.3)."""
    slot: int
    status: RequirementStatus
    listed_type_id: Optional[int] = None
    listed_name: str = ""
    worn_type_id: Optional[int] = None
    worn_name: str = ""
    reason: str = ""                    # "HG-1008 preferred", "check by hand", "empty", "Mid-grade Amulet Alpha"


@dataclass
class ImplantSetResult:
    """An implant set requirement matched to a clone (TRACKED_ITEMS_DESIGN.md §9, plan 15.2)."""
    set_name: str                       # "High-Grade Amulets"
    placement: str                      # HOME, IN USE, AWAY, MISSING, or UNKNOWN (no clone data)
    status: RequirementStatus
    clone: str = ""                     # "jump clone", "active clone"; for MISSING, the closest one checked
    where: str = ""                     # the jump clone's station or structure
    slots: List[ImplantSlotResult] = field(default_factory=list)


@dataclass
class RequirementResult:
    """
    Represents the result of a single requirement check.
    """
    req_uid: int
    status: RequirementStatus
    requirement_details: Dict[str, Any]  # The original requirement dict
    ship_results: List[ShipRequirementResult] = field(default_factory=list)
    # Plain-language summary when no ship could be audited, e.g. "No Devoter in Jita".
    message: Optional[str] = None
    # When no ship could be audited: everything a replacement needs (the hull, its fitting and
    # metadata), offered to the shopping list from this requirement's row only.
    shortfalls: List[ItemShortfall] = field(default_factory=list)
    # NOT CHECKED: the character's hulls at the place with no fitting assigned, offered for
    # assigning from the requirement's row ({item_id, type_id, custom_name, fit_uid, fit_name}).
    unassigned_hulls: List[Dict[str, Any]] = field(default_factory=list)
    # An implant set requirement (a Capsule fitting) has no ships: its clone match instead.
    implant_set: Optional[ImplantSetResult] = None
    # "hard" or "soft" (homes and priorities plan, P1): only hard requirements decide readiness.
    priority: str = "hard"
    # When a wider hard requirement for the same fitting locks this one soft (P4): that requirement.
    covered_by: Optional[Dict[str, Any]] = None
    # Its ships exist but are in the wrong place (in the system, or deployed): a failure to move, not to buy (H9).
    misplaced: bool = False
    # The skill check (ESI features plan 26.5): "" when it's off, "unchecked" when the pilot's skills
    # haven't been pulled, "checked" with the result in skills (a services.skill_requirements.SkillCheck).
    skill_state: str = ""
    skills: Optional[Any] = None

    @property
    def skill_failure(self) -> Optional[str]:
        """
        "hard" when the pilot can't fly the hull or a fitted module and the requirement decides
        readiness (D1.1), "soft" for anything else missing, None when nothing is.
        """
        if self.skills is None or self.skills.ok:
            return None
        return "hard" if self.skills.hard and self.counts_for_readiness else "soft"

    @property
    def counts_for_readiness(self) -> bool:
        return self.priority != "soft"

    @property
    def failure(self) -> Optional[str]:
        """
        "hard" when the requirement fails and decides readiness, "soft" when it fails but
        only warns (P2), None when it doesn't fail. A soft failure points at something to
        move or fix; a hard one at something to buy.
        """
        if self.status != RequirementStatus.FAIL:
            return None
        return "hard" if self.counts_for_readiness and not self.misplaced else "soft"

    @property
    def ships_listed(self) -> int:
        return len(self.ship_results)

    @property
    def ships_ready(self) -> int:
        """How many listed ships are ready (PASS or WARN), for "1 of 10 ready" (decision D13)."""
        return sum(1 for s in self.ship_results if is_ready(s.status))

@dataclass
class AuditResult:
    """
    The final output of an audit for a specific role and character.
    """
    character_id: int
    role_uid: int
    role_name: str
    doctrine_uid: Optional[int] = None
    doctrine_name: Optional[str] = None
    requirement_results: List[RequirementResult] = field(default_factory=list)

    @property
    def overall_pass(self) -> bool:
        """
        True when every hard requirement is ready. A warning, a soft failure (a ship to move, H9) and
        any soft requirement still count as ready.
        """
        return all((is_ready(r.status) or r.failure == "soft") and r.skill_failure != "hard"
                   for r in self.requirement_results if r.counts_for_readiness)
