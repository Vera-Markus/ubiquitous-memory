from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

@dataclass
class Asset:
    item_id: int
    type_id: int
    location_id: int
    location_flag: Optional[str]
    quantity: int
    is_singleton: bool
    character_id: int
    name: str = ""
    custom_name: Optional[str] = None
    fitting: Optional['Fitting'] = None
    mutated_base: Optional[int] = None      # a mutated item's base module, once known (plan 12.2)
    manual_at: Optional[str] = None         # a ship whose contents were pasted from the game: when (1.7.4)

@dataclass
class Fitting:
    high: List[Asset] = field(default_factory=list)
    med: List[Asset] = field(default_factory=list)
    low: List[Asset] = field(default_factory=list)
    rigs: List[Asset] = field(default_factory=list)
    cargo: List[Asset] = field(default_factory=list)
    drones: List[Asset] = field(default_factory=list)
    subsystems: List[Asset] = field(default_factory=list)
    subsystem_bay: List[Asset] = field(default_factory=list)        # spare subsystems (1.7.4)
    contents: List[Asset] = field(default_factory=list)
    # Bays from the bay registry (app/models/bay_registry.py). Filled from step 1.2.
    fighters: List[Asset] = field(default_factory=list)            # fighter bay + fighter tubes
    fuel_bay: List[Asset] = field(default_factory=list)
    fleet_hangar: List[Asset] = field(default_factory=list)
    ship_maintenance_bay: List[Asset] = field(default_factory=list)
    escape_bay: List[Asset] = field(default_factory=list)

@dataclass
class ShipAsset:
    asset: Asset
    fitting: Fitting = field(default_factory=Fitting)
    # The station, structure or solar system the ship is ultimately in. For a ship
    # inside another ship (or a container) this is where the outermost item sits.
    root_location_id: int = 0
    # Set when the ship is carried directly inside another ship (Ship Maintenance
    # Bay, escape bay, fleet hangar, cargo).
    carrier_item_id: Optional[int] = None

@dataclass
class CarriedShip:
    """A ship (assembled or packaged) inside another ship: the "suitcase" check, design §9.5."""
    item_id: int
    type_id: int
    name: str                          # hull type name, e.g. "Pilgrim"
    custom_name: Optional[str]
    carrier_item_id: int
    carrier_name: str                  # e.g. "Minokawa"
    carrier_custom_name: Optional[str]
    bay_key: str                       # registry key, e.g. "ship_maintenance_bay", or "cargo"
    location_id: int                   # the carrier's root location (station, structure or system)
    quantity: int = 1                  # a packaged stack can hold several hulls

@dataclass
class AuditSnapshot:
    """
    A point-in-time data collection for auditing a character.
    This model contains all the raw data required to perform compliance checks
    without containing the logic for the checks themselves.
    """
    character_id: int
    snapshot_time: str
    assets: List[Asset]
    ships: List[ShipAsset]
    systems: List[int]
    stations: List[int]
    assigned_role_uids: List[int]
    carried_ships: List[CarriedShip] = field(default_factory=list)   # filled from step 1.7
    clones: Optional[Dict[str, Any]] = None     # data/clones/<id>.json; None when not pulled (plan 15.2)
    skills: Optional[Dict[int, int]] = None     # {skill: level} from data/skills/<id>.json; None when not pulled (ESI plan 26.5)
