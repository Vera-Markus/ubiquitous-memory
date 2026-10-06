"""
SDE rules the V2 audit relies on (design: docs/DOCTRINE_METADATA_V2.md §9.4).

- Identical-stat equivalents (decision D11): modules that differ only in name
  and type ID, e.g. Centum A-Type = Corpum A-Type Thermal Energized Membrane.
  The audit treats them as the same item. Ammo and other charges never do
  (user rule, 2026-10-06): a charge only matches its own type.
- No other stand-ins (user rule, 2026-10-06, replacing decision D3): a T1,
  meta, faction or T2 variant of a module is a different item.
- Bling (user rule, 2026-10-06): a better version of the doctrine's module,
  in its family with a strictly higher meta level, is a warning rather than
  missing. Modules only; never a Polarized weapon, and never a non-T2 weapon
  on a fitting that carries T2 ammo, which it can't load. Fighters follow the
  same rule (T2 for T1 is bling, T1 for T2 is missing). Drones too, but only
  faction ones: any faction version of the doctrine's drone is bling.

Every answer is cached for the session; the SDE only changes on restart.
"""
import sqlite3
from pathlib import Path
from typing import Dict, FrozenSet, List, Optional, Tuple, Union

CATEGORY_SHIP = 6
CATEGORY_MODULE = 7
CATEGORY_CHARGE = 8
CATEGORY_DRONE = 18
CATEGORY_SUBSYSTEM = 32
CATEGORY_FIGHTER = 87

META_LEVEL = 633
IMPLANT_SLOT = 331            # implantness: the slot an implant goes in (1-10)
META_GROUP_TECH_II = 2
META_GROUP_FACTION = 4
META_GROUP_ABYSSAL = 15       # mutated modules (TRACKED_ITEMS_DESIGN.md §9a)
BLING_CATEGORIES = (CATEGORY_MODULE, CATEGORY_FIGHTER, CATEGORY_DRONE)
EFFECT_LAUNCHER_FITTED, EFFECT_TURRET_FITTED = 40, 42

# Categories whose items can be fitted or carried as part of a fit. Blueprints
# (category 9) are never compared: without this rule faction blueprints match.
FITTABLE_CATEGORIES = frozenset({CATEGORY_MODULE, CATEGORY_CHARGE, CATEGORY_DRONE, CATEGORY_SUBSYSTEM, CATEGORY_FIGHTER})
# Categories that can have identical-stat twins: never charges, which match exactly.
EQUIVALENT_CATEGORIES = FITTABLE_CATEGORIES - {CATEGORY_CHARGE}

# Attributes that differ between otherwise identical variants: metaLevelOld, techLevel, metaGroupID.
IGNORED_ATTRIBUTES = frozenset({633, 422, 1692})
FIGHTER_SQUADRON_MAX_SIZE = 2215

Signature = Tuple[Tuple[Tuple[int, float], ...], FrozenSet[int]]


class SdeRules:
    def __init__(self, db_path: Union[str, Path]):
        self.db_path = Path(db_path)
        if not self.db_path.exists():
            raise FileNotFoundError(f"Database not found at {self.db_path}")
        self._conn = sqlite3.connect(f"file:{self.db_path.as_posix()}?mode=ro", uri=True, check_same_thread=False)
        self._category: Dict[int, Tuple[Optional[int], bool]] = {}
        self._parent: Dict[int, int] = {}
        self._equivalents: Dict[int, Tuple[int, ...]] = {}
        self._squadron: Dict[int, int] = {}
        self._meta: Dict[int, Tuple[float, Optional[int], bool, str]] = {}
        self._implant_slot: Dict[int, Optional[int]] = {}

    def close(self) -> None:
        self._conn.close()

    # --- basic lookups -----------------------------------------------------------

    def _rows(self, sql: str, params: tuple = ()) -> list:
        return self._conn.execute(sql, params).fetchall()

    def _category_published(self, type_id: int) -> Tuple[Optional[int], bool]:
        if type_id not in self._category:
            row = self._rows(
                "SELECT g.categoryID, t.published FROM invTypes t JOIN invGroups g ON g.groupID = t.groupID WHERE t.typeID = ?",
                (type_id,))
            self._category[type_id] = (row[0][0], bool(row[0][1])) if row else (None, False)
        return self._category[type_id]

    def category(self, type_id: int) -> Optional[int]:
        return self._category_published(type_id)[0]

    def family_parent(self, type_id: int) -> int:
        """The type's invMetaTypes parent, or the type itself when it has none."""
        if type_id not in self._parent:
            row = self._rows("SELECT parentTypeID FROM invMetaTypes WHERE typeID = ?", (type_id,))
            self._parent[type_id] = row[0][0] if row and row[0][0] else type_id
        return self._parent[type_id]

    def _family(self, parent: int) -> List[int]:
        children = [r[0] for r in self._rows("SELECT typeID FROM invMetaTypes WHERE parentTypeID = ?", (parent,))]
        return sorted({parent, *children})

    def _signature(self, type_id: int) -> Signature:
        attributes = tuple(sorted(
            (attr, value) for attr, value in self._rows(
                "SELECT attributeID, COALESCE(valueFloat, valueInt) FROM dgmTypeAttributes WHERE typeID = ?", (type_id,))
            if attr not in IGNORED_ATTRIBUTES))
        effects = frozenset(r[0] for r in self._rows("SELECT effectID FROM dgmTypeEffects WHERE typeID = ?", (type_id,)))
        return attributes, effects

    # --- D11: identical-stat equivalents -------------------------------------------

    def equivalent_types(self, type_id: int) -> Tuple[int, ...]:
        """
        Every type with identical stats to this one (including itself), sorted.
        A type with no twin, a charge, or a type that isn't fittable returns just itself.
        """
        if type_id in self._equivalents:
            return self._equivalents[type_id]

        category, published = self._category_published(type_id)
        if category not in EQUIVALENT_CATEGORIES or not published:
            self._equivalents[type_id] = (type_id,)
            return self._equivalents[type_id]

        groups: Dict[Signature, List[int]] = {}
        for member in self._family(self.family_parent(type_id)):
            member_category, member_published = self._category_published(member)
            if member_category not in EQUIVALENT_CATEGORIES or not member_published:
                continue
            groups.setdefault(self._signature(member), []).append(member)
        for members in groups.values():
            twins = tuple(sorted(members))
            for member in members:
                self._equivalents[member] = twins
        return self._equivalents.setdefault(type_id, (type_id,))

    def equivalence_key(self, type_id: int) -> int:
        """The lowest type ID among this type's identical-stat twins (its own ID if it has none)."""
        return self.equivalent_types(type_id)[0]

    # --- bling (user rule, 2026-10-06) ---------------------------------------------------------

    def _meta_info(self, type_id: int) -> Tuple[float, Optional[int], bool, str]:
        """(meta level, meta group, is a turret or launcher, name)."""
        if type_id not in self._meta:
            level = self._rows("SELECT COALESCE(valueFloat, valueInt) FROM dgmTypeAttributes "
                               "WHERE typeID = ? AND attributeID = ?", (type_id, META_LEVEL))
            group = self._rows("SELECT metaGroupID FROM invMetaTypes WHERE typeID = ?", (type_id,))
            weapon = self._rows("SELECT 1 FROM dgmTypeEffects WHERE typeID = ? AND effectID IN (?, ?)",
                                (type_id, EFFECT_LAUNCHER_FITTED, EFFECT_TURRET_FITTED))
            name = self._rows("SELECT typeName FROM invTypes WHERE typeID = ?", (type_id,))
            self._meta[type_id] = (float(level[0][0]) if level and level[0][0] is not None else 0.0,
                                   group[0][0] if group else None, bool(weapon), name[0][0] if name else "")
        return self._meta[type_id]

    def type_name(self, type_id: int) -> str:
        return self._meta_info(type_id)[3] or f"type {type_id}"

    def is_mutated(self, type_id: int) -> bool:
        """A mutated (Abyssal) module or drone type: one generic type per group, rolls per item."""
        return self._meta_info(type_id)[1] == META_GROUP_ABYSSAL

    def is_tech_ii_charge(self, type_id: int) -> bool:
        return self.category(type_id) == CATEGORY_CHARGE and self._meta_info(type_id)[1] == META_GROUP_TECH_II

    def is_bling(self, expected: int, fitted: int, carries_t2_ammo: bool = False) -> bool:
        """
        Whether a module fitted instead of the doctrine's one is a better version of it:
        the same family, a strictly higher meta level, and none of the traps (a Polarized
        weapon; a non-T2 weapon when the fitting carries T2 ammo it can't load). Fighters
        follow the same rule. For a drone, any faction version of it.
        """
        category = self.category(expected)
        if expected == fitted or category not in BLING_CATEGORIES or self.category(fitted) != category:
            return False
        if self.family_parent(expected) != self.family_parent(fitted):
            return False
        if category == CATEGORY_DRONE:
            return self._meta_info(fitted)[1] == META_GROUP_FACTION
        expected_level = self._meta_info(expected)[0]
        level, group, weapon, name = self._meta_info(fitted)
        if level <= expected_level:
            return False
        if weapon and name.startswith("Polarized "):
            return False
        if weapon and carries_t2_ammo and group != META_GROUP_TECH_II:
            return False
        return True

    # --- implants (tracked items plan, Phase 15) -------------------------------------------

    def implant_slot(self, type_id: int) -> Optional[int]:
        """The slot (1-10) an implant goes in; None for anything that isn't an implant."""
        if type_id not in self._implant_slot:
            row = self._rows("SELECT COALESCE(valueFloat, valueInt) FROM dgmTypeAttributes "
                             "WHERE typeID = ? AND attributeID = ?", (type_id, IMPLANT_SLOT))
            self._implant_slot[type_id] = int(row[0][0]) if row and row[0][0] else None
        return self._implant_slot[type_id]

    # --- fighters (decision D22) -----------------------------------------------------------

    def squadron_size(self, type_id: int) -> int:
        """Fighters per full squadron (fighterSquadronMaxSize); 1 for anything else."""
        if type_id not in self._squadron:
            row = self._rows(
                "SELECT COALESCE(valueFloat, valueInt) FROM dgmTypeAttributes WHERE typeID = ? AND attributeID = ?",
                (type_id, FIGHTER_SQUADRON_MAX_SIZE))
            self._squadron[type_id] = int(row[0][0]) if row and row[0][0] else 1
        return self._squadron[type_id]
