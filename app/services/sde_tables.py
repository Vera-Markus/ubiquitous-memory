"""
What sde_builder writes into data/eve.db, and the checks the result must pass
(docs/SDE_MIGRATION_PLAN.md, step 9.1).

Table and column names are Fuzzwork's, so the code reading the database doesn't
change. Only the columns the app, the tests or tools/make_sde_fixture.py read are
kept. To add data for a new feature, add a Table here (and a test), then bump
BUILDER_SCHEMA so existing databases are rebuilt.
"""
import sqlite3
from datetime import datetime, timezone
from typing import Dict, Iterator, List, Optional, Tuple

from app.services.sde_builder import (
    INTEGER, REAL, TEXT, DerivedTable, Reader, SdeFormatError, SdeRecord, Table, const, en, english, field, flag, key, value_at,
)

BUILDER_SCHEMA = 1

TABLES = (
    Table("invCategories", "categories.jsonl", primary_key=("categoryID",), columns={
        "categoryID": key(),
        "categoryName": en("name"),
        "published": flag("published"),
    }),
    Table("invGroups", "groups.jsonl", primary_key=("groupID",), columns={
        "groupID": key(),
        "categoryID": field("categoryID"),
        "groupName": en("name"),
        "published": flag("published"),
    }),
    Table("invMetaGroups", "metaGroups.jsonl", primary_key=("metaGroupID",), columns={
        "metaGroupID": key(),
        "metaGroupName": en("name"),
    }),
    Table("invTypes", "types.jsonl", primary_key=("typeID",), indexes=(("typeName",),), columns={
        "typeID": key(),
        "groupID": field("groupID"),
        "typeName": en("name"),
        "published": flag("published"),
    }),
    # CCP has no metaTypes file: each type names its variation parent (T1 module) itself.
    Table("invMetaTypes", "types.jsonl", primary_key=("typeID",), indexes=(("parentTypeID",),),
          where=lambda record: "variationParentTypeID" in record or "metaGroupID" in record, columns={
        "typeID": key(),
        "parentTypeID": field("variationParentTypeID", optional=True),
        "metaGroupID": field("metaGroupID", optional=True),
    }),
    Table("dgmAttributeTypes", "dogmaAttributes.jsonl", primary_key=("attributeID",), columns={
        "attributeID": key(),
        "attributeName": field("name", TEXT),
    }),
    # One value per attribute; the app reads COALESCE(valueFloat, valueInt), as with Fuzzwork's.
    Table("dgmTypeAttributes", "typeDogma.jsonl", primary_key=("typeID", "attributeID"),
          explode="dogmaAttributes", columns={
        "typeID": key(),
        "attributeID": field("attributeID"),
        "valueInt": const(None, INTEGER),
        "valueFloat": field("value", REAL),
    }),
    Table("dgmTypeEffects", "typeDogma.jsonl", primary_key=("typeID", "effectID"), explode="dogmaEffects", columns={
        "typeID": key(),
        "effectID": field("effectID"),
        "isDefault": flag("isDefault"),
    }),
    Table("mapSolarSystems", "mapSolarSystems.jsonl", primary_key=("solarSystemID",), columns={
        "solarSystemID": key(),
        "solarSystemName": en("name"),
        "regionID": field("regionID"),
        "constellationID": field("constellationID"),
        "x": field("position.x", REAL),
        "y": field("position.y", REAL),
        "z": field("position.z", REAL),
        "security": field("securityStatus", REAL),
    }),
)

# --- stations ----------------------------------------------------------------------------

STATION_SOURCES = ("npcStations.jsonl", "mapPlanets.jsonl", "mapMoons.jsonl", "npcCorporations.jsonl",
                   "stationOperations.jsonl")

STATION_COLUMNS = {
    "stationID": INTEGER, "stationName": TEXT, "solarSystemID": INTEGER, "corporationID": INTEGER,
    "operationID": INTEGER, "stationTypeID": INTEGER, "x": REAL, "y": REAL, "z": REAL,
}

_NUMERALS = ((1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
             (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"))


def roman(number: int) -> str:
    text = ""
    for value, numeral in _NUMERALS:
        while number >= value:
            text += numeral
            number -= value
    return text


def station_location(system: str, planet: Optional[Tuple[int, Optional[str]]],
                     moon: Optional[Tuple[int, Optional[str]]]) -> str:
    """
    The part of a station's name before its owner, as the game shows it.
    planet is (celestialIndex, uniqueName) of the planet it orbits, or of its moon's
    planet; moon is (orbitIndex, uniqueName), or None when it orbits a planet. A
    station orbiting neither (the Fulcrum orbits Zarzakh's star) is named after its system.
      Jita IV - Moon 4                          a moon
      Jita IV                                   a planet
      Amarr VIII (Oris) - Moon 4                the planet has a unique name
      Kor-Azor Prime IV (Eclipticum) - Moon Griklaeum    the moon has one (it's the whole prefix)
    """
    if planet is None:
        return system
    planet_name = planet[1] or f"{system} {roman(planet[0])}"
    if moon is None:
        return planet_name
    return moon[1] or f"{planet_name} - Moon {moon[0]}"


def _lookup(station: SdeRecord, table: dict, item_id: int, what: str):
    if item_id not in table:
        raise SdeFormatError(station.file, station.line, f"{what} {item_id} not found")
    return table[item_id]


def _stations(read: Reader, db: sqlite3.Connection) -> Iterator[tuple]:
    systems = dict(db.execute("SELECT solarSystemID, solarSystemName FROM mapSolarSystems"))
    corporations = {r["_key"]: english(r, "name") for r in read("npcCorporations.jsonl")}
    operations = {r["_key"]: english(r, "operationName") for r in read("stationOperations.jsonl")}
    stations = list(read("npcStations.jsonl"))
    orbits = {station["orbitID"] for station in stations}

    planets: Dict[int, Tuple[int, Optional[str]]] = {}
    for r in read("mapPlanets.jsonl"):
        planets[r["_key"]] = (r["celestialIndex"], english(r, "uniqueName", optional=True))
    moons: Dict[int, Tuple[int, int, Optional[str]]] = {}         # only the moons stations orbit
    for r in read("mapMoons.jsonl"):
        if r["_key"] in orbits:
            moons[r["_key"]] = (r["orbitID"], r["orbitIndex"], english(r, "uniqueName", optional=True))

    for station in stations:
        system = _lookup(station, systems, station["solarSystemID"], "solar system")
        orbit = station["orbitID"]
        if orbit in moons:
            planet_id, orbit_index, moon_name = moons[orbit]
            where = station_location(system, _lookup(station, planets, planet_id, "planet"), (orbit_index, moon_name))
        else:
            where = station_location(system, planets.get(orbit), None)
        name = f"{where} - {_lookup(station, corporations, station['ownerID'], 'corporation')}"
        if station.get("useOperationName"):
            name += f" {_lookup(station, operations, station['operationID'], 'station operation')}"
        yield (station["_key"], name, station["solarSystemID"], station["ownerID"], station["operationID"],
               station["typeID"], *(value_at(station, f"position.{axis}") for axis in "xyz"))


def _sde_info(read: Reader, db: sqlite3.Connection) -> Iterator[tuple]:
    built_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for r in read("_sde.jsonl"):
        if r.get("_key") == "sde":
            yield ("sde", r["buildNumber"], r["releaseDate"], BUILDER_SCHEMA, built_at)


TABLES += (
    DerivedTable("staStations", STATION_SOURCES, STATION_COLUMNS, primary_key=("stationID",),
                 indexes=(("solarSystemID",),), build=_stations),
    # Which release the database was built from, and by which version of these definitions.
    DerivedTable("sdeInfo", ("_sde.jsonl",), {"key": TEXT, "buildNumber": INTEGER, "releaseDate": TEXT,
                                              "schemaVersion": INTEGER, "builtAt": TEXT},
                 primary_key=("key",), build=_sde_info),
)

# --- sanity checks --------------------------------------------------------------------------

# About half of each table's size in build 3569502 (2026-10-02): far more than a broken
# build would have, far less than any real release.
MIN_ROWS = {
    "invCategories": 20, "invGroups": 800, "invMetaGroups": 5, "invTypes": 25_000, "invMetaTypes": 6_000,
    "dgmAttributeTypes": 1_400, "dgmTypeAttributes": 300_000, "dgmTypeEffects": 25_000,
    "mapSolarSystems": 4_000, "staStations": 2_500, "sdeInfo": 1,
}

KNOWN_ROWS = (
    ("invTypes", "typeID", 587, "typeName", "Rifter"),
    ("mapSolarSystems", "solarSystemID", 30000142, "solarSystemName", "Jita"),
    ("staStations", "stationID", 60003760, "stationName", "Jita IV - Moon 4 - Caldari Navy Assembly Plant"),
)


def check_row_counts(db: sqlite3.Connection) -> List[str]:
    problems = []
    for table, minimum in MIN_ROWS.items():
        count = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        if count < minimum:
            problems.append(f"{table} has {count} rows (expected at least {minimum})")
    return problems


def check_known_rows(db: sqlite3.Connection) -> List[str]:
    problems = []
    for table, id_column, item_id, column, expected in KNOWN_ROWS:
        row = db.execute(f"SELECT {column} FROM {table} WHERE {id_column} = ?", (item_id,)).fetchone()
        if row is None or row[0] != expected:
            problems.append(f"{table} {item_id} is {row[0] if row else 'missing'!r} (expected {expected!r})")
    return problems


CHECKS = (check_row_counts, check_known_rows)
