from enum import Enum
from typing import Optional

from app.models.bay_registry import bay_for_flag


class SlotType(Enum):
    HI_SLOT = "HiSlot"
    MED_SLOT = "MedSlot"
    LO_SLOT = "LoSlot"
    RIG_SLOT = "RigSlot"
    CARGO = "Cargo"
    DRONE_BAY = "DroneBay"
    SUBSYSTEM_SLOT = "SubSystemSlot"
    SUBSYSTEM_BAY = "SubSystemBay"      # a T3 cruiser's spare subsystems (1.7.4)
    FIGHTER_BAY = "FighterBay"
    FUEL_BAY = "SpecializedFuelBay"
    FLEET_HANGAR = "FleetHangar"
    SHIP_MAINTENANCE_BAY = "ShipHangar"
    ESCAPE_BAY = "FrigateEscapeBay"
    UNKNOWN = "Unknown"


class SlotCategory(Enum):
    HIGH = "high"
    MEDIUM = "med"
    LOW = "low"
    RIGS = "rigs"
    CARGO = "cargo"
    DRONES = "drones"
    SUBSYSTEMS = "subsystems"
    SUBSYSTEM_BAY = "subsystem_bay"
    # Bays from the bay registry; the values are the registry's bay keys.
    FIGHTERS = "fighters"
    FUEL_BAY = "fuel_bay"
    FLEET_HANGAR = "fleet_hangar"
    SHIP_MAINTENANCE_BAY = "ship_maintenance_bay"
    ESCAPE_BAY = "escape_bay"
    OTHER = "other"


# Registry bay key -> (SlotType, SlotCategory)
_BAY_SLOTS = {
    "fighters": (SlotType.FIGHTER_BAY, SlotCategory.FIGHTERS),
    "fuel_bay": (SlotType.FUEL_BAY, SlotCategory.FUEL_BAY),
    "fleet_hangar": (SlotType.FLEET_HANGAR, SlotCategory.FLEET_HANGAR),
    "ship_maintenance_bay": (SlotType.SHIP_MAINTENANCE_BAY, SlotCategory.SHIP_MAINTENANCE_BAY),
    "escape_bay": (SlotType.ESCAPE_BAY, SlotCategory.ESCAPE_BAY),
}


def classify_location_flag(flag: Optional[str]) -> tuple[SlotType, SlotCategory]:
    """
    Classifies an ESI location_flag into a SlotType and a SlotCategory.

    Bays come from the bay registry (app/models/bay_registry.py), so a flag
    belongs to a bay only once that bay is registered. Slot numbers
    (HiSlot0-7, ...) only decide the category, never the result.
    """
    if not flag:
        return SlotType.UNKNOWN, SlotCategory.OTHER

    bay = bay_for_flag(flag)
    if bay is not None and bay.key in _BAY_SLOTS:
        return _BAY_SLOTS[bay.key]

    if flag.startswith("HiSlot"):
        return SlotType.HI_SLOT, SlotCategory.HIGH
    if flag.startswith("MedSlot"):
        return SlotType.MED_SLOT, SlotCategory.MEDIUM
    if flag.startswith("LoSlot"):
        return SlotType.LO_SLOT, SlotCategory.LOW
    if flag.startswith("RigSlot"):
        return SlotType.RIG_SLOT, SlotCategory.RIGS
    if flag.startswith("SubSystemSlot"):
        return SlotType.SUBSYSTEM_SLOT, SlotCategory.SUBSYSTEMS
    if flag == "SubSystemBay":
        return SlotType.SUBSYSTEM_BAY, SlotCategory.SUBSYSTEM_BAY
    if flag == "Cargo":
        return SlotType.CARGO, SlotCategory.CARGO
    if flag == "DroneBay":
        return SlotType.DRONE_BAY, SlotCategory.DRONES

    return SlotType.UNKNOWN, SlotCategory.OTHER
