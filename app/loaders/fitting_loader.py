import json
import logging
import sqlite3
import re
from pathlib import Path
from typing import Dict, List, Any, NamedTuple, Optional, Tuple, Union

from app import paths
from app.models.bay_registry import BAYS

logger = logging.getLogger("EVEdbLoader")

# Constants for Slot Resolution (from fit_parser_spec.md)
SLOT_EFFECTS = {
    11: "low",
    12: "high",
    13: "mid",
    2663: "rigs",
    3772: "subsystem",
    6306: "service"
}

DRONE_CATEGORY_ID = 18
FIGHTER_CATEGORY_ID = 87

# Dogma attributes for fuel suggestions (design §7.2)
JUMP_DRIVE_CONSUMPTION_TYPE = 866       # the isotope a jump drive burns
JUMP_DRIVE_CONSUMPTION_AMOUNT = 868     # base units per light year
MODULE_CONSUMPTION_TYPE = 713           # what a module burns per cycle (siege, triage, cyno...)
MODULE_CONSUMPTION_QUANTITY = 714       # units per cycle


class FuelUse(NamedTuple):
    type_id: int
    name: str
    quantity: float     # per light year for a jump drive, per cycle for a module


class EVEdbLoader:
    def __init__(self, db_path: Union[str, Path], location_cache_path: Optional[Union[str, Path]] = None):
        """
        location_cache_path: the resolved-location cache. Defaults to
        GENERATED_DIR/location_cache.json, looked up when first needed.
        """
        self.db_path = Path(db_path)
        if not self.db_path.exists():
            raise FileNotFoundError(f"Database not found at {self.db_path}")
        self._location_cache_path = Path(location_cache_path) if location_cache_path else None
        self._location_cache: Dict[str, Dict[str, Any]] = {}
        self._location_cache_mtime: Optional[float] = None
        # typeID -> (groupID, categoryID); the SDE doesn't change while the app runs.
        self._type_group_cache: Dict[int, Tuple[Optional[int], Optional[int]]] = {}

    def _execute_query(self, query: str, params: Tuple = ()) -> List[Dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

    def get_typeid_by_name(self, name: str) -> Optional[int]:
        results = self._execute_query("SELECT typeID FROM invTypes WHERE typeName = ?", (name,))
        return results[0]['typeID'] if results else None

    def get_type_name(self, typeid: int) -> str:
        results = self._execute_query("SELECT typeName FROM invTypes WHERE typeID = ?", (typeid,))
        return results[0]['typeName'] if results else "Unknown"

    def get_type_data(self, typeid: int) -> Optional[Dict[str, Any]]:
        results = self._execute_query("SELECT * FROM invTypes WHERE typeID = ?", (typeid,))
        return results[0] if results else None

    def get_dogma_data(self, typeid: int) -> Optional[Dict[str, Any]]:
        results = self._execute_query("SELECT effectID, isDefault FROM dgmTypeEffects WHERE typeID = ?", (typeid,))
        if results:
            return {"dogmaEffects": [{"effectID": r['effectID'], "isDefault": bool(r['isDefault'])} for r in results]}
        return None

    def _type_group(self, typeid: int) -> Tuple[Optional[int], Optional[int]]:
        if typeid not in self._type_group_cache:
            rows = self._execute_query(
                "SELECT t.groupID, g.categoryID FROM invTypes t LEFT JOIN invGroups g ON g.groupID = t.groupID WHERE t.typeID = ?",
                (typeid,))
            self._type_group_cache[typeid] = (rows[0]["groupID"], rows[0]["categoryID"]) if rows else (None, None)
        return self._type_group_cache[typeid]

    def get_type_group(self, typeid: int) -> Optional[int]:
        """The type's SDE group ID, or None if the type is unknown."""
        return self._type_group(typeid)[0]

    def get_type_category(self, typeid: int) -> Optional[int]:
        """The type's SDE category ID (6 = Ship), or None if the type is unknown."""
        return self._type_group(typeid)[1]

    def _attributes(self, type_id: int, attribute_ids: Tuple[int, ...]) -> Dict[int, float]:
        """{attributeID: value} for the given dogma attributes the type has."""
        marks = ",".join("?" * len(attribute_ids))
        rows = self._execute_query(
            f"SELECT attributeID, COALESCE(valueFloat, valueInt) AS value FROM dgmTypeAttributes "
            f"WHERE typeID = ? AND attributeID IN ({marks})", (type_id, *attribute_ids))
        return {row["attributeID"]: row["value"] for row in rows if row["value"] is not None}

    def get_hull_bays(self, hull_type_id: int) -> Dict[str, float]:
        """
        {bay_key: capacity} for every registry bay this hull has (its availability
        attribute is above zero), in registry order. Capacity is in m³, or the
        number of ships for the escape bay.
        """
        attribute_ids = tuple({bay.availability_attribute for bay in BAYS.values() if bay.availability_attribute})
        values = self._attributes(hull_type_id, attribute_ids)
        return {bay.key: values[bay.availability_attribute] for bay in BAYS.values()
                if bay.availability_attribute and values.get(bay.availability_attribute, 0) > 0}

    def _fuel(self, type_id: int, type_attribute: int, amount_attribute: int) -> Optional[FuelUse]:
        values = self._attributes(type_id, (type_attribute, amount_attribute))
        fuel_type = int(values.get(type_attribute) or 0)
        if not fuel_type:
            return None
        return FuelUse(fuel_type, self.get_type_name(fuel_type), values.get(amount_attribute, 0))

    def get_jump_fuel(self, hull_type_id: int) -> Optional[FuelUse]:
        """The isotope a hull's jump drive burns, with its base use per light year; None without a jump drive."""
        return self._fuel(hull_type_id, JUMP_DRIVE_CONSUMPTION_TYPE, JUMP_DRIVE_CONSUMPTION_AMOUNT)

    def get_module_fuel(self, module_type_id: int) -> Optional[FuelUse]:
        """What a module burns per cycle (Siege Module II → Strontium Clathrates); None if nothing."""
        return self._fuel(module_type_id, MODULE_CONSUMPTION_TYPE, MODULE_CONSUMPTION_QUANTITY)

    def get_group_data(self, group_id: int) -> Optional[Dict[str, Any]]:
        results = self._execute_query("SELECT * FROM invGroups WHERE groupID = ?", (group_id,))
        return results[0] if results else None

    def get_all_solar_systems(self) -> List[Dict[str, Any]]:
        """Returns all solar systems from the database."""
        return self._execute_query("SELECT solarSystemID, solarSystemName FROM mapSolarSystems ORDER BY solarSystemName")

    def get_stations_in_system(self, solar_system_id: int) -> List[Dict[str, Any]]:
        """Returns all stations in a given solar system."""
        return self._execute_query("SELECT stationID, stationName FROM staStations WHERE solarSystemID = ? ORDER BY stationName", (solar_system_id,))

    def get_system_name(self, system_id: int) -> str:
        """Returns the name of a solar system."""
        results = self._execute_query("SELECT solarSystemName FROM mapSolarSystems WHERE solarSystemID = ?", (system_id,))
        return results[0]['solarSystemName'] if results else "Unknown"

    def get_station_name(self, station_id: int) -> str:
        """Returns the name of a station."""
        results = self._execute_query("SELECT stationName FROM staStations WHERE stationID = ?", (station_id,))
        return results[0]['stationName'] if results else "Unknown"

    def get_resolved_location(self, location_id: int) -> Optional[Dict[str, Any]]:
        """
        Looks a location up in the resolved-location cache (location_cache.json):
        NPC stations, solar systems and player structures named through ESI.
        The file is read once and re-read only when it changes on disk.
        """
        cache = self._read_location_cache()
        return cache.get(str(location_id)) if cache is not None else None

    def get_structures_in_system(self, solar_system_id: int) -> List[Tuple[int, str]]:
        """
        (structure ID, name) for the player structures in a system that the location
        cache knows (structures the pulled characters have assets in), sorted by
        name. NPC stations are left out; get_stations_in_system lists those.
        """
        cache = self._read_location_cache() or {}
        found = {}
        for location_id, entry in cache.items():
            if entry.get("solar_system_id") != solar_system_id or not entry.get("name"):
                continue
            if not str(location_id).isdigit() or self.is_station(int(location_id)):
                continue
            found[int(location_id)] = entry["name"]
        return sorted(found.items(), key=lambda item: (item[1], item[0]))

    def location_label(self, location_id: Optional[int], fallback: Optional[str] = None) -> str:
        """
        The name to show for a location ID: an NPC station or solar system from the
        SDE, else a structure from the location cache, else fallback (a name saved
        with it), else "Location <id>". "" for no location.
        """
        if not location_id:
            return ""
        if self.is_station(location_id):
            return self.get_station_name(location_id)
        if self.is_solar_system(location_id):
            return self.get_system_name(location_id)
        resolved = self.get_resolved_location(location_id)
        if resolved and resolved.get("name") not in (None, "", "Unknown"):
            return resolved["name"]
        return fallback or f"Location {location_id}"

    def system_of(self, location_id: Optional[int]) -> Optional[int]:
        """
        The solar system a location is in: an NPC station's from the SDE, a solar
        system is its own, a structure's from the location cache. None when unknown.
        """
        if not location_id:
            return None
        system_id = self.get_system_id_of_station(location_id)
        if system_id:
            return system_id
        if self.is_solar_system(location_id):
            return location_id
        resolved = self.get_resolved_location(location_id) or {}
        return resolved.get("solar_system_id") or None

    def _read_location_cache(self) -> Optional[Dict[str, Dict[str, Any]]]:
        """The location cache, read once and again only when the file changes; None if it can't be read."""
        cache_path = self._location_cache_path or (paths.GENERATED_DIR / "location_cache.json")
        try:
            mtime = cache_path.stat().st_mtime
        except OSError:
            logger.debug(f"Location cache not found: {cache_path}")
            return None

        if mtime != self._location_cache_mtime:
            try:
                with open(cache_path, "r", encoding="utf-8") as f:
                    self._location_cache = json.load(f)
                self._location_cache_mtime = mtime
            except (OSError, ValueError) as e:
                logger.error(f"Could not read location cache {cache_path}: {e}")
                return None

        return self._location_cache

    def is_station(self, location_id: int) -> bool:
        """True for an NPC station in the SDE."""
        return bool(self._execute_query("SELECT 1 FROM staStations WHERE stationID = ?", (location_id,)))

    def is_solar_system(self, location_id: int) -> bool:
        """True for a solar system in the SDE (a ship logged off in space)."""
        return bool(self._execute_query("SELECT 1 FROM mapSolarSystems WHERE solarSystemID = ?", (location_id,)))

    def get_system_id_of_station(self, station_id: int) -> Optional[int]:
        """Returns the solar system ID of a given station."""
        results = self._execute_query("SELECT solarSystemID FROM staStations WHERE stationID = ?", (station_id,))
        return results[0]['solarSystemID'] if results else None

class FitParser:
    def __init__(self, sde_loader: EVEdbLoader):
        self.sde = sde_loader

    def parse_fit(self, fit_text: str) -> Dict[str, Any]:
        lines = [line.strip() for line in fit_text.strip().split('\n')]
        if not lines:
            return {}

        # Header: [Hull Name, Fit Name]
        header = lines[0].strip('[]').split(',')
        hull_name = header[0].strip() if len(header) > 0 else "Unknown"
        fit_name = header[1].strip() if len(header) > 1 else "Unknown"

        # Find hull_type_id
        hull_type_id = self.sde.get_typeid_by_name(hull_name)

        # Split into sections by blank lines
        sections = []
        current_section = []
        for line in lines[1:]:
            if not line:
                if current_section:
                    sections.append(current_section)
                    current_section = []
            else:
                current_section.append(line)
        if current_section:
            sections.append(current_section)

        result = {
            "hull": hull_name,
            "fit_name": fit_name,
            "hull_type_id": hull_type_id,
            "low": {},
            "mid": {},
            "high": {},
            "rigs": {},
            "subsystem": {},
            "service": {},
            "drones": {},
            "fighters": {},
            "cargo": {},
            "unresolved": []
        }

        for i, section in enumerate(sections):
            is_final_section = (i == len(sections) - 1)

            items = []  # (name, quantity, type_id, natural category)
            for item_line in section:
                name, qty = parse_item_line(item_line)
                type_id = self.sde.get_typeid_by_name(name)
                if not type_id:
                    result["unresolved"].append(name)
                    continue
                items.append((name, qty, type_id, self._natural_category(type_id, result)))

            # EFT's last section is the cargo hold, unless the fit has no cargo and
            # ends on rigs, subsystems, fighters or its only drone section.
            if is_final_section and not self._final_section_keeps_categories(items, result):
                items = [(name, qty, type_id, "cargo") for name, qty, type_id, _ in items]

            for name, qty, type_id, category in items:
                target_dict = result[category]
                tid_str = str(type_id)
                if tid_str not in target_dict:
                    target_dict[tid_str] = {"name": name, "quantity": 0}
                target_dict[tid_str]["quantity"] += qty

        # Clean up empty dicts
        for key in ["low", "mid", "high", "rigs", "subsystem", "service", "drones", "fighters", "cargo"]:
            if key in result and not result[key]:
                del result[key]

        return result

    def _natural_category(self, type_id: int, result: Dict[str, Any]) -> str:
        """Where an item goes outside the cargo section: its slot, the fighter or drone bay, or cargo."""
        slot = self._resolve_slot(type_id)
        if slot and slot in result:
            return slot
        if self._is_fighter(type_id):
            return "fighters"
        if self._is_drone(type_id):
            return "drones"
        return "cargo"

    @staticmethod
    def _final_section_keeps_categories(items: List[Tuple[str, int, int, str]], result: Dict[str, Any]) -> bool:
        """True when every item in the last section is a rig, a subsystem, a fighter, or a drone
        and no earlier section held drones (so it can't be spare drones in cargo)."""
        if not items:
            return False
        earlier_drones = bool(result["drones"])
        return all(
            category in ("rigs", "subsystem", "fighters") or (category == "drones" and not earlier_drones)
            for _, _, _, category in items
        )

    def _resolve_slot(self, type_id: int) -> Optional[str]:
        dogma = self.sde.get_dogma_data(type_id)
        if not dogma:
            return None
        
        effects = dogma.get("dogmaEffects", [])
        for effect in effects:
            effect_id = effect.get("effectID")
            if effect_id in SLOT_EFFECTS:
                return SLOT_EFFECTS[effect_id]
        return None

    def _is_drone(self, type_id: int) -> bool:
        type_data = self.sde.get_type_data(type_id)
        if not type_data:
            return False
        
        group_id = type_data.get("groupID")
        if group_id is None:
            return False
            
        group = self.sde.get_group_data(group_id)
        if not group:
            return False
        
        category_id = group.get("categoryID")
        return category_id == DRONE_CATEGORY_ID

    def _is_fighter(self, type_id: int) -> bool:
        return self.sde.get_type_category(type_id) == FIGHTER_CATEGORY_ID


def parse_item_line(line: str) -> Tuple[str, int]:
    """Splits an EFT item line into (name, quantity): "Hobgoblin II x5" -> ("Hobgoblin II", 5)."""
    match = re.match(r"(.+?)(?:\s+x(\d+))?$", line.strip())
    if match:
        name = match.group(1).strip()
        qty = int(match.group(2)) if match.group(2) else 1
        return name, qty
    return line.strip(), 1


def parse_fit(fit_text: str, db_path: str = "data/eve.db") -> Dict[str, Any]:
    loader = EVEdbLoader(db_path)
    parser = FitParser(loader)
    return parser.parse_fit(fit_text)


