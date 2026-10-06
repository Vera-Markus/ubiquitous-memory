"""
The bay registry (design: docs/DOCTRINE_METADATA_V2.md §4).

One table of every ship bay V2 audits. The importer prompt, the metadata
editor, the hierarchy classification, the audit and the presentation all read
from here, so a bay is described in exactly one place.

Bay keys are a storage contract: they're saved in fittings and packages.
Never rename one; add new bays with new keys.

The fleet hangar is registered by its own flag only. Cargo stays cargo, because
it's an EFT fact compared with the fit; the audit pools the two when it counts
(decision D2), which `pooled_with_cargo` records.
"""
from dataclasses import dataclass
from typing import Dict, Literal, Optional

# SDE dogma attributes: a hull has the bay when its value is above zero.
FIGHTER_CAPACITY = 2055
SPECIAL_FUEL_BAY_CAPACITY = 1549
FLEET_HANGAR_CAPACITY = 912
SHIP_MAINTENANCE_BAY_CAPACITY = 908
FRIGATE_ESCAPE_BAY_CAPACITY = 3020

# SDE categories
CATEGORY_MATERIAL = 4
CATEGORY_MODULE = 7
CATEGORY_CHARGE = 8
CATEGORY_SHIP = 6
CATEGORY_FIGHTER = 87

# Hull groups the escape bay accepts: frigates, but not covert ops frigates or
# stealth bombers (decided 2026-10-04). Used to choose which saved fittings the
# escape-ship dropdown offers; the audit itself accepts whatever the game allowed in.
ESCAPE_BAY_ALLOWED_GROUPS = frozenset({
    25,    # Frigate (includes pirate frigates such as the Astero and Dramiel)
    324,   # Assault Frigate
    831,   # Interceptor
    893,   # Electronic Attack Ship
    1283,  # Expedition Frigate
    1527,  # Logistics Frigate
})


@dataclass(frozen=True)
class BayDefinition:
    key: str                                    # stable storage key
    label: str                                  # name shown to the user
    location_flags: frozenset[str]              # ESI location_flag values, matched exactly
    availability_attribute: Optional[int]       # dgmAttributeTypes.attributeID; None = every hull
    source: Literal["fit", "metadata"]          # where the requirement comes from
    expected_categories: frozenset[int]         # what normally goes in it; for warnings only, never to block
    flag_prefixes: frozenset[str] = frozenset() # ESI flags matched by prefix (FighterTube0-4)
    allowed_groups: frozenset[int] = frozenset()  # escape bay only: hull groups offered in its dropdown
    pooled_with_cargo: bool = False             # fleet hangar only: counted together with the cargo (D2)

    def holds_flag(self, flag: Optional[str]) -> bool:
        if not flag:
            return False
        return flag in self.location_flags or any(flag.startswith(p) for p in self.flag_prefixes)


FIGHTERS = BayDefinition(
    key="fighters",
    label="Fighters",
    location_flags=frozenset({"FighterBay"}),
    flag_prefixes=frozenset({"FighterTube"}),        # tubes and bay are pooled (D4)
    availability_attribute=FIGHTER_CAPACITY,
    source="fit",                                   # EFT exports list fighters
    expected_categories=frozenset({CATEGORY_FIGHTER}),
)

FUEL_BAY = BayDefinition(
    key="fuel_bay",
    label="Fuel Bay",
    location_flags=frozenset({"SpecializedFuelBay"}),
    availability_attribute=SPECIAL_FUEL_BAY_CAPACITY,
    source="metadata",
    expected_categories=frozenset({CATEGORY_MATERIAL}),   # isotopes and strontium (group Ice Product)
)

FLEET_HANGAR = BayDefinition(
    key="fleet_hangar",
    label="Fleet Hangar",
    location_flags=frozenset({"FleetHangar"}),
    availability_attribute=FLEET_HANGAR_CAPACITY,
    source="metadata",                              # extra stock on top of the EFT cargo
    expected_categories=frozenset({CATEGORY_MODULE, CATEGORY_CHARGE}),
    pooled_with_cargo=True,
)

SHIP_MAINTENANCE_BAY = BayDefinition(
    key="ship_maintenance_bay",
    label="Ship Maintenance Bay",
    location_flags=frozenset({"ShipHangar"}),       # confirmed by a live pull, 2026-10-04 (D1)
    availability_attribute=SHIP_MAINTENANCE_BAY_CAPACITY,
    source="metadata",
    expected_categories=frozenset({CATEGORY_SHIP}),
)

ESCAPE_BAY = BayDefinition(
    key="escape_bay",
    label="Escape Bay",
    location_flags=frozenset({"FrigateEscapeBay"}),
    availability_attribute=FRIGATE_ESCAPE_BAY_CAPACITY,
    source="metadata",
    expected_categories=frozenset({CATEGORY_SHIP}),
    allowed_groups=ESCAPE_BAY_ALLOWED_GROUPS,
)

BAYS: Dict[str, BayDefinition] = {bay.key: bay for bay in (FIGHTERS, FUEL_BAY, FLEET_HANGAR, SHIP_MAINTENANCE_BAY, ESCAPE_BAY)}


def bay_for_flag(flag: Optional[str]) -> Optional[BayDefinition]:
    """The registered bay an ESI location_flag belongs to, or None (slots, cargo, hangars...)."""
    for bay in BAYS.values():
        if bay.holds_flag(flag):
            return bay
    return None


def metadata_bays() -> list[BayDefinition]:
    """Bays whose requirements are entered as doctrine metadata, in registry order."""
    return [bay for bay in BAYS.values() if bay.source == "metadata"]
