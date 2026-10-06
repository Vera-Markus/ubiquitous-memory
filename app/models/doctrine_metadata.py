"""
Doctrine metadata: what a fitting needs beyond its EFT text (design §5.1-5.2).

Stored on a fitting record as "doctrine_metadata": per-bay requirements for
the metadata bays in the bay registry (fuel, fleet hangar stock, ships in the
maintenance bay, the escape ship), plus free-text notes.

    DoctrineMetadata.from_dict(record.get("doctrine_metadata"))  -> parse
    metadata.normalized()                                        -> merge duplicates, drop empty bays
    metadata.validate(owner_fit_uid)                             -> raise MetadataError listing every problem
    metadata.to_dict()                                           -> what's saved
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

from app.models.bay_registry import BAYS, ESCAPE_BAY, SHIP_MAINTENANCE_BAY

Match = Literal["type", "fit", "any_ship"]
MATCHES = ("type", "fit", "any_ship")
ESCAPE_ONLY_MATCHES = ("fit", "any_ship")
# Bays whose entries can name a saved fitting: the escape ship, and ships carried in the
# maintenance bay (UI thoughts plan 20.2; type_id is then the fitting's hull, cached).
FIT_BAYS = (ESCAPE_BAY.key, SHIP_MAINTENANCE_BAY.key)

# The doctrine fitting range, the same as FittingManager's DOCTRINE_UID_MIN/MAX
# (tests/test_doctrine_metadata.py checks they agree).
DOCTRINE_FIT_UID_MIN = 1000
DOCTRINE_FIT_UID_MAX = 1999


def is_doctrine_fit_uid(fit_uid: Optional[int]) -> bool:
    return fit_uid is not None and DOCTRINE_FIT_UID_MIN <= fit_uid <= DOCTRINE_FIT_UID_MAX


class MetadataError(ValueError):
    """Metadata that can't be read or saved. `problems` lists every reason."""

    def __init__(self, problems: List[str]):
        self.problems = list(problems)
        super().__init__("; ".join(self.problems))


@dataclass
class BayRequirement:
    type_id: Optional[int]          # the item or hull; None only when match == "any_ship"
    name: str                       # display cache: the type name, the fitting name, or "Any"
    min_quantity: int               # a minimum: more is never a failure
    match: Match = "type"
    fit_uid: Optional[int] = None   # set only when match == "fit"

    @classmethod
    def any_ship(cls) -> "BayRequirement":
        return cls(type_id=None, name="Any", min_quantity=1, match="any_ship")

    @classmethod
    def from_dict(cls, data: Any) -> "BayRequirement":
        if not isinstance(data, dict):
            raise MetadataError([f"a requirement must be an object, not {type(data).__name__}"])
        match = data.get("match") or "type"
        try:
            type_id = None if data.get("type_id") is None else int(data["type_id"])
            fit_uid = None if data.get("fit_uid") is None else int(data["fit_uid"])
            min_quantity = int(data.get("min_quantity", 1))
        except (TypeError, ValueError):
            raise MetadataError([f"requirement {data.get('name', '?')!r} has a non-numeric ID or quantity"])
        return cls(type_id=type_id, name=str(data.get("name") or ""), min_quantity=min_quantity,
                   match=match, fit_uid=fit_uid)

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        if self.match != "type":
            out["match"] = self.match
        if self.match == "fit":
            out["fit_uid"] = self.fit_uid
        out.update({"type_id": self.type_id, "name": self.name, "min_quantity": self.min_quantity})
        return out


@dataclass
class DoctrineMetadata:
    bays: Dict[str, List[BayRequirement]] = field(default_factory=dict)
    notes: str = ""
    updated_at: str = ""
    # Full squadrons pre-loaded in the fighter tubes, per fighter type (UI thoughts plan 20.3, U11).
    # Empty: tubes and bay are pooled, as before.
    tubes: Dict[int, int] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "DoctrineMetadata":
        """Parse a stored record. None or {} is "no requirements"."""
        if not data:
            return cls()
        if not isinstance(data, dict):
            raise MetadataError([f"doctrine_metadata must be an object, not {type(data).__name__}"])
        bays_data = data.get("bays")
        if bays_data is None:
            bays_data = {}
        if not isinstance(bays_data, dict):
            raise MetadataError(["bays must be an object keyed by bay"])
        bays: Dict[str, List[BayRequirement]] = {}
        for bay_key, entries in bays_data.items():
            if not isinstance(entries, list):
                raise MetadataError([f"{bay_key}: requirements must be a list"])
            bays[str(bay_key)] = [BayRequirement.from_dict(entry) for entry in entries]
        tubes_data = data.get("tubes") or {}
        if not isinstance(tubes_data, dict):
            raise MetadataError(["tubes must be an object keyed by fighter type ID"])
        try:
            tubes = {int(type_id): int(count) for type_id, count in tubes_data.items()}
        except (TypeError, ValueError):
            raise MetadataError(["tubes: fighter type IDs and squadron counts must be numbers"])
        return cls(bays=bays, notes=str(data.get("notes") or ""), updated_at=str(data.get("updated_at") or ""),
                   tubes=tubes)

    def to_dict(self) -> Dict[str, Any]:
        """What's saved. Empty bays are left out (an empty list is the same as a missing bay)."""
        out: Dict[str, Any] = {"bays": {key: [r.to_dict() for r in reqs] for key, reqs in self.bays.items() if reqs}}
        if self.notes:
            out["notes"] = self.notes
        if any(self.tubes.values()):
            out["tubes"] = {str(type_id): count for type_id, count in sorted(self.tubes.items()) if count}
        if self.updated_at:
            out["updated_at"] = self.updated_at
        return out

    def is_empty(self) -> bool:
        return not any(self.bays.values()) and not self.notes.strip() and not any(self.tubes.values())

    def requirements(self, bay_key: str) -> List[BayRequirement]:
        return list(self.bays.get(bay_key, []))

    def normalized(self) -> "DoctrineMetadata":
        """
        Duplicate type_ids within a bay merged by summing min_quantity, in
        first-seen order; empty bays dropped. Escape bay entries are kept as they are.
        """
        bays: Dict[str, List[BayRequirement]] = {}
        for key, reqs in self.bays.items():
            if key == ESCAPE_BAY.key:
                merged = [BayRequirement(**vars(r)) for r in reqs]
            else:
                by_type: Dict[Any, BayRequirement] = {}
                merged = []
                for r in reqs:
                    same = (r.match, r.fit_uid if r.match == "fit" else r.type_id)
                    if r.match in ("type", "fit") and same in by_type:
                        by_type[same].min_quantity += r.min_quantity
                        continue
                    copy = BayRequirement(**vars(r))
                    if r.match in ("type", "fit"):
                        by_type[same] = copy
                    merged.append(copy)
            if merged:
                bays[key] = merged
        return DoctrineMetadata(bays=bays, notes=self.notes, updated_at=self.updated_at,
                                tubes={t: n for t, n in self.tubes.items() if n})

    def problems(self, owner_fit_uid: Optional[int] = None) -> List[str]:
        """
        Every rule from design §5.1 this metadata breaks. `owner_fit_uid` is
        the fitting it belongs to, for the namespace rule: a doctrine fitting
        (1000-1999) may only recommend a doctrine escape fitting.
        """
        found: List[str] = []
        for type_id, count in self.tubes.items():
            if count < 0:
                found.append(f"Fighter tubes: type {type_id} can't have fewer than 0 squadrons")
        for key, reqs in self.bays.items():
            bay = BAYS.get(key)
            if bay is None:
                found.append(f"{key}: not a known bay")
                continue
            if bay.source != "metadata":
                found.append(f"{bay.label}: comes from the EFT fit, not from metadata")
                continue
            if key == ESCAPE_BAY.key:
                found.extend(self._escape_bay_problems(reqs, owner_fit_uid))
                continue
            for r in reqs:
                label = f"{bay.label}: {r.name or r.type_id}"
                if r.match not in MATCHES:
                    found.append(f"{label}: unknown match {r.match!r}")
                elif r.match == "any_ship" or (r.match == "fit" and key not in FIT_BAYS):
                    found.append(f"{label}: a fitting or 'Any' can only be required in the {ESCAPE_BAY.label}"
                                 + (" or the Ship Maintenance Bay" if r.match == "fit" else ""))
                if r.match == "type" and (r.type_id is None or r.type_id <= 0):
                    found.append(f"{label}: needs a type ID")
                if r.match == "fit" and key in FIT_BAYS:
                    if r.fit_uid is None:
                        found.append(f"{label}: no fitting chosen")
                    elif is_doctrine_fit_uid(owner_fit_uid) and not is_doctrine_fit_uid(r.fit_uid):
                        found.append(f"{label}: a doctrine fitting can only name doctrine fittings "
                                     f"(UID {r.fit_uid} is a local fitting)")
                if r.min_quantity < 1:
                    found.append(f"{label}: quantity must be at least 1")
        return found

    @staticmethod
    def _escape_bay_problems(reqs: List[BayRequirement], owner_fit_uid: Optional[int]) -> List[str]:
        label = ESCAPE_BAY.label
        if len(reqs) != 1:
            return [f"{label}: holds exactly one ship, found {len(reqs)} requirements"]
        r = reqs[0]
        if r.match not in ESCAPE_ONLY_MATCHES:
            return [f"{label}: must be a saved fitting or 'Any', not a ship type"]
        found = []
        if r.min_quantity != 1:
            found.append(f"{label}: holds exactly one ship (quantity {r.min_quantity})")
        if r.match == "any_ship" and r.type_id is not None:
            found.append(f"{label}: 'Any' can't name a ship type")
        if r.match == "fit":
            if r.fit_uid is None:
                found.append(f"{label}: no fitting chosen")
            elif is_doctrine_fit_uid(owner_fit_uid) and not is_doctrine_fit_uid(r.fit_uid):
                found.append(f"{label}: a doctrine fitting can only recommend a doctrine escape fitting "
                             f"(UID {r.fit_uid} is a local fitting)")
        return found

    def validate(self, owner_fit_uid: Optional[int] = None) -> None:
        found = self.problems(owner_fit_uid)
        if found:
            raise MetadataError(found)
