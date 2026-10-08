"""
What a fitting or a ship needs a pilot to have trained (ESI features plan 26.4), and the
skill plan for what's missing (26.7).

Every type lists up to six required skills, as dogma attribute pairs in eve.db: the skill
(182, 183, 184, 1285, 1289, 1290) and its level (277, 278, 279, 1286, 1287, 1288). A skill
is a type too, so its own prerequisites are followed the same way.

Hard and soft (D1.1): the hull and anything fitted in a slot (high, mid, low, rig,
subsystem) are hard, since without them the ship can't be flown as fitted. Everything else
(drones, fighters, charges, cargo) is soft: something to train, not a reason the pilot
isn't ready.
"""
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Union

PAIRS = ((182, 277), (183, 278), (184, 279), (1285, 1286), (1289, 1287), (1290, 1288))
HARD_SECTIONS = ("high", "mid", "low", "rigs", "subsystem")         # a fitting's slots
HARD_SHIP_PARTS = ("high", "med", "low", "rigs", "subsystems")      # a ship's (asset_models.Fitting)
SOFT_SHIP_PARTS = ("drones", "fighters", "cargo", "contents", "fleet_hangar")
ROMAN = {1: "I", 2: "II", 3: "III", 4: "IV", 5: "V"}


@dataclass
class MissingSkill:
    skill_id: int
    name: str
    needed: int
    have: int

    @property
    def text(self) -> str:
        """'Carriers V (has IV)', 'Jump Drive Calibration I (not trained)'."""
        has = f"has {ROMAN.get(self.have, self.have)}" if self.have else "not trained"
        return f"{self.name} {ROMAN.get(self.needed, self.needed)} ({has})"


@dataclass
class SkillCheck:
    """Missing skills for the hull and fitted modules (hard) and for the rest (soft)."""
    hard: List[MissingSkill] = field(default_factory=list)
    soft: List[MissingSkill] = field(default_factory=list)
    levels: Dict[int, int] = field(default_factory=dict)    # the pilot's levels, for the skill plan

    @property
    def ok(self) -> bool:
        return not self.hard and not self.soft

    @property
    def worst(self) -> Optional[str]:
        return "hard" if self.hard else "soft" if self.soft else None

    def summary(self, limit: int = 3, parts=("hard", "soft")) -> str:
        """'Carriers V (has IV), Drones V (has IV) and 2 more', hard ones first."""
        missing = [m for part in parts for m in getattr(self, part)]
        shown = ", ".join(m.text for m in missing[:limit])
        return shown + (f" and {len(missing) - limit} more" if len(missing) > limit else "")


class SkillRequirements:
    def __init__(self, db_path: Union[str, Path]):
        self._direct: Dict[int, Dict[int, int]] = {}
        self._names: Dict[int, str] = {}
        self._needs: Dict[int, Dict[int, int]] = {}
        with sqlite3.connect(Path(db_path)) as conn:
            ids = {a for pair in PAIRS for a in pair}
            raw: Dict[int, Dict[int, int]] = {}
            marks = ",".join("?" * len(ids))
            for type_id, attribute, value in conn.execute(
                    f"SELECT typeID, attributeID, COALESCE(valueFloat, valueInt) FROM dgmTypeAttributes "
                    f"WHERE attributeID IN ({marks})", tuple(ids)):
                if value is not None:
                    raw.setdefault(type_id, {})[attribute] = int(value)
            for type_id, attributes in raw.items():
                for skill_attr, level_attr in PAIRS:
                    skill = attributes.get(skill_attr)
                    if skill:
                        direct = self._direct.setdefault(type_id, {})
                        direct[skill] = max(direct.get(skill, 0), attributes.get(level_attr, 1) or 1)
            skills = {s for d in self._direct.values() for s in d}
            if skills:
                marks = ",".join("?" * len(skills))
                self._names = dict(conn.execute(f"SELECT typeID, typeName FROM invTypes WHERE typeID IN ({marks})",
                                                tuple(skills)))

    def name(self, skill_id: int) -> str:
        return self._names.get(skill_id, f"Skill {skill_id}")

    def direct(self, type_id: int) -> Dict[int, int]:
        """The type's own required skills: {skill: level}."""
        return dict(self._direct.get(type_id, {}))

    def needs(self, type_id: int) -> Dict[int, int]:
        """Every skill the type needs, prerequisites included: {skill: highest level}."""
        if type_id not in self._needs:
            self._needs[type_id] = {}           # guards against a cycle in bad data
            result: Dict[int, int] = {}
            for skill, level in self._direct.get(type_id, {}).items():
                result[skill] = max(result.get(skill, 0), level)
                for sub, sub_level in self.needs(skill).items():
                    result[sub] = max(result.get(sub, 0), sub_level)
            self._needs[type_id] = result
        return self._needs[type_id]

    def _all_needs(self, type_ids: Iterable[int]) -> Dict[int, int]:
        result: Dict[int, int] = {}
        for type_id in type_ids:
            for skill, level in self.needs(type_id).items():
                result[skill] = max(result.get(skill, 0), level)
        return result

    def check(self, hard_types: Iterable[int], soft_types: Iterable[int], levels: Dict[int, int]) -> SkillCheck:
        hard_need = self._all_needs(hard_types)
        soft_need = self._all_needs(soft_types)
        check = SkillCheck(levels=dict(levels))
        for skill, level in sorted(hard_need.items(), key=lambda kv: self.name(kv[0])):
            if levels.get(skill, 0) < level:
                check.hard.append(MissingSkill(skill, self.name(skill), level, levels.get(skill, 0)))
        for skill, level in sorted(soft_need.items(), key=lambda kv: self.name(kv[0])):
            if levels.get(skill, 0) < level and level > hard_need.get(skill, 0):
                check.soft.append(MissingSkill(skill, self.name(skill), level, levels.get(skill, 0)))
        return check

    def check_fitting(self, fitting: Dict, levels: Dict[int, int]) -> SkillCheck:
        """A library fitting: the hull and its slot sections hard, the rest soft."""
        fit = fitting.get("fit", fitting)
        hard, soft = [fitting.get("hull_type_id") or fit.get("hull_type_id")], []
        for section, items in fit.items():
            if not isinstance(items, dict):
                continue
            type_ids = [int(t) for t in items if str(t).isdigit()]
            (hard if section in HARD_SECTIONS else soft).extend(type_ids)
        return self.check([t for t in hard if t], soft, levels)

    def check_ship(self, ship, levels: Dict[int, int]) -> SkillCheck:
        """A ship as it is (asset_models.ShipAsset): the hull and what's fitted hard, the rest soft (D1.2)."""
        fitting = ship.fitting
        hard = [ship.asset.type_id] + [a.type_id for part in HARD_SHIP_PARTS for a in getattr(fitting, part, [])]
        soft = [a.type_id for part in SOFT_SHIP_PARTS for a in getattr(fitting, part, [])]
        return self.check(hard, soft, levels)

    def plan(self, check: SkillCheck, parts: Iterable[str] = ("hard", "soft")) -> List[str]:
        """
        Copy Skill Plan (26.7): one 'Skill Name N' per line, only the missing levels from the
        next trainable one, each skill's prerequisites before it. The game's import refuses a
        plan out of order or with prerequisites missing (D1.4).
        """
        target: Dict[int, int] = {}
        for part in parts:
            for m in getattr(check, part):
                target[m.skill_id] = max(target.get(m.skill_id, 0), m.needed)
        lines: List[str] = []
        done: Dict[int, int] = {}

        def visit(skill: int) -> None:
            if skill in done:
                return
            done[skill] = 0
            for prerequisite in sorted(self.direct(skill), key=self.name):
                if prerequisite in target:
                    visit(prerequisite)
            have = check.levels.get(skill, 0)
            for level in range(have + 1, target[skill] + 1):
                lines.append(f"{self.name(skill)} {level}")

        for skill in sorted(target, key=self.name):
            visit(skill)
        return lines
