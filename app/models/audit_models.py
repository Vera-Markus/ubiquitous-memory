from dataclasses import dataclass, field
from typing import List, Dict, Any, Literal, Optional
from enum import Enum

from app.models.asset_models import CarriedShip

class RequirementStatus(Enum):
    PASS = "PASS"
    WARN = "WARN"   # ready, but needs attention: refit, substitute, unexpected item, still packed
    FAIL = "FAIL"


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
class PackedShipWarning:
    """A ship still packed inside a carrier that the carrier's metadata doesn't call for (design §9.5)."""
    ship: CarriedShip
    reason: Literal["not_in_metadata", "exceeds_requirement"]

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
        """True when every requirement is ready; a warning still counts as ready."""
        return all(is_ready(r.status) for r in self.requirement_results)
