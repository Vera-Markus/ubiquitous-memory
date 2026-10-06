import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.models.bay_registry import ESCAPE_BAY
from app.models.doctrine_metadata import DoctrineMetadata, MetadataError, is_doctrine_fit_uid

logger = logging.getLogger(__name__)


DOCTRINE_UID_MIN = 1000
DOCTRINE_UID_MAX = 1999
LOCAL_UID_MIN = 2000

# The highest doctrine UID ever handed out is stored in fittings.json, so a deleted
# doctrine fitting's UID is never given to a new one. Packages identify fittings by
# UID: a reused UID would look like an update of the deleted fit to recipients, and
# their local requirements on the old fit would silently point at the new one.
HIGH_WATER_KEY = "doctrine_uid_high_water"

# Version of the fitting record layout (design §5.1). 2: fighters have their own section.
CURRENT_SCHEMA = 2
FIGHTER_CATEGORY = 87


class FittingManager:
    def __init__(self, fittings_path: str):
        self.fittings_path = Path(fittings_path)
        self._data: Dict[str, Any] = {"generated_at": "", "fittings": []}
        self.load_fittings()

    def load_fittings(self) -> None:
        """Load and normalize fitting records from the JSON file."""
        if not self.fittings_path.exists():
            self._data = {"generated_at": "", "fittings": []}
            return

        try:
            with self.fittings_path.open("r", encoding="utf-8") as file:
                loaded = json.load(file)
            self._data = {"generated_at": loaded.get("generated_at", ""), "fittings": []}
            if isinstance(loaded.get(HIGH_WATER_KEY), int):
                self._data[HIGH_WATER_KEY] = loaded[HIGH_WATER_KEY]
            for record in loaded.get("fittings", []):
                normalized = self._normalize_record(record)
                if normalized["fit_uid"] is None:
                    normalized["fit_uid"] = self._allocate_uid(normalized["source"])
                else:
                    self._uid_source(normalized["fit_uid"])
                self._data["fittings"].append(normalized)
        except (json.JSONDecodeError, IOError, TypeError, ValueError) as error:
            logger.error(f"Error loading fittings: {error}")
            self._data = {"generated_at": "", "fittings": []}

    def save_fittings(self) -> None:
        """Save the current fitting records to the JSON file."""
        try:
            self.fittings_path.parent.mkdir(parents=True, exist_ok=True)
            with self.fittings_path.open("w", encoding="utf-8") as file:
                json.dump(self._data, file, indent=2)
        except IOError as error:
            logger.error(f"Error saving fittings: {error}")

    def list_fittings(self) -> List[Dict[str, Any]]:
        return list(self._data.get("fittings", []))

    def create_fitting(self, fitting_data: Dict[str, Any], source: str = "local") -> Dict[str, Any]:
        """Create, persist, and return a fitting with a newly allocated UID."""
        normalized_source = self._validate_source(source)
        record = self._normalize_record(fitting_data, source=normalized_source)
        record["fit_uid"] = self._allocate_uid(normalized_source)
        record["schema_version"] = CURRENT_SCHEMA   # the importer already writes the current layout
        self._data.setdefault("fittings", []).append(record)
        self._touch_and_save()
        return record

    def replace_fitting(
        self,
        fit_uid: int,
        fitting_data: Dict[str, Any],
        source: Optional[str] = None,
    ) -> bool:
        """
        Replace a record while retaining its UID and namespace. The existing
        doctrine metadata is kept unless the new data brings its own: replacing
        the EFT fit must never erase requirements (design §8).
        """
        fittings = self._data.setdefault("fittings", [])
        for index, existing in enumerate(fittings):
            if existing.get("fit_uid") != fit_uid:
                continue
            existing_source = self._validate_source(source or existing.get("source", "local"))
            if self._uid_source(fit_uid) != existing_source:
                raise ValueError("A fitting can only be replaced within its UID namespace")
            replacement = self._normalize_record(fitting_data, source=existing_source)
            replacement["fit_uid"] = fit_uid
            replacement["schema_version"] = CURRENT_SCHEMA
            if "doctrine_metadata" not in replacement and "doctrine_metadata" in existing:
                replacement["doctrine_metadata"] = existing["doctrine_metadata"]
            fittings[index] = replacement
            self._touch_and_save()
            return True
        return False

    def get_fitting(self, fit_uid: int) -> Optional[Dict[str, Any]]:
        return next((record for record in self.list_fittings() if record.get("fit_uid") == fit_uid), None)

    def find_by_hull(self, hull: str) -> List[Dict[str, Any]]:
        value = hull.casefold()
        return [record for record in self.list_fittings() if str(record.get("hull", "")).casefold() == value]

    def find_by_fit_name(self, fit_name: str) -> List[Dict[str, Any]]:
        value = fit_name.casefold()
        return [record for record in self.list_fittings() if str(record.get("fit_name", "")).casefold() == value]

    def search_fittings(self, query: str) -> List[Dict[str, Any]]:
        value = query.casefold()
        return [
            record for record in self.list_fittings()
            if value in str(record.get("hull", "")).casefold()
            or value in str(record.get("fit_name", "")).casefold()
        ]

    def import_fit(
        self,
        fit_text: str,
        shared_doctrine: bool = False,
        operation: str = "new",
        fit_uid: Optional[int] = None,
        db_path: str = "data/eve.db",
    ) -> Dict[str, Any] | bool:
        """Parse fit text through the loader, then create or replace its library record."""
        from app.loaders.fitting_loader import parse_fit

        parsed_fit = parse_fit(fit_text, db_path)
        if not parsed_fit:
            raise ValueError("Fit text did not produce a fitting record")
        source = "doctrine" if shared_doctrine else "local"
        if operation == "replace":
            if fit_uid is None:
                raise ValueError("fit_uid is required when replacing a fitting")
            return self.replace_fitting(fit_uid, parsed_fit, source=source)
        if operation != "new":
            raise ValueError("operation must be 'new' or 'replace'")
        return self.create_fitting(parsed_fit, source=source)

    def inject_fitting(self, fitting_data: Dict[str, Any]) -> None:
        """
        Injects a fitting with a specific UID.
        Used for restoring doctrine packages.
        """
        uid = fitting_data.get("fit_uid")
        if uid is None:
            raise ValueError("fit_uid must be provided for injection.")
        
        # Ensure the source is set correctly based on UID
        fitting_data["source"] = self._uid_source(uid)
        if fitting_data["source"] == "doctrine":
            self._data[HIGH_WATER_KEY] = max(self._doctrine_high_water(), uid)

        # Replace an existing record with the same UID, so re-importing a
        # package updates fittings instead of duplicating them.
        fittings = self._data.setdefault("fittings", [])
        for index, existing in enumerate(fittings):
            if existing.get("fit_uid") == uid:
                fittings[index] = fitting_data
                break
        else:
            fittings.append(fitting_data)
        self._touch_and_save()

    def delete_fitting(self, fit_uid: int) -> bool:
        """Delete a fitting by its authoritative UID."""
        fittings = self._data.setdefault("fittings", [])
        remaining = [record for record in fittings if record.get("fit_uid") != fit_uid]
        if len(remaining) == len(fittings):
            return False
        self._data["fittings"] = remaining
        self._touch_and_save()
        return True

    def rename_fitting(self, fit_uid: int, new_name: str) -> Dict[str, Any]:
        """
        Renames a fitting everywhere its name is stored: fit_name, fitting and the
        parsed fit's fit_name (the "[Hull, Name]" header is built from these). Roles
        and escape ships point at fittings by UID, so they follow. A package fitting
        (1000-1999) is marked renamed, so a package update keeps the new name.
        Raises KeyError for an unknown fitting and ValueError for an unusable name.
        """
        record = self.get_fitting(fit_uid)
        if record is None:
            raise KeyError(f"Fitting UID {fit_uid} not found.")
        name = (new_name or "").strip()
        if not name:
            raise ValueError("The name can't be empty.")
        if any(c in name for c in "[]\r\n"):
            raise ValueError("The name can't contain [ ] or line breaks (they'd break the EFT header).")
        if name == record.get("fit_name"):
            return record           # unchanged (a same-named copy in the other range is fine)
        clash = [f for f in self.find_by_fit_name(name) if f.get("fit_uid") != fit_uid]
        if clash:
            raise ValueError(f"Another fitting is already called \"{clash[0]['fit_name']}\".")
        record["fit_name"] = name
        record["fitting"] = name
        if isinstance(record.get("fit"), dict):
            record["fit"]["fit_name"] = name
        if self._uid_source(fit_uid) == "doctrine":
            record["renamed"] = True
        self._touch_and_save()
        return record

    def remove_fittings(self, fit_uids) -> int:
        """Deletes several fittings with one save. Returns how many were removed."""
        fit_uids = set(fit_uids)
        fittings = self._data.setdefault("fittings", [])
        remaining = [record for record in fittings if record.get("fit_uid") not in fit_uids]
        removed = len(fittings) - len(remaining)
        if removed:
            self._data["fittings"] = remaining
            self._touch_and_save()
        return removed

    def clear(self) -> None:
        """Deletes every fitting. Doctrine fitting UIDs still aren't reused (the high-water mark stays)."""
        high_water = self._doctrine_high_water()
        self._data["fittings"] = []
        if high_water >= DOCTRINE_UID_MIN:
            self._data[HIGH_WATER_KEY] = high_water
        self._touch_and_save()

    def get_fitting_names(self) -> List[str]:
        return [record["fit_name"] for record in self.list_fittings() if record.get("fit_name")]

    def _normalize_record(self, fitting_data: Dict[str, Any], source: Optional[str] = None) -> Dict[str, Any]:
        if not isinstance(fitting_data, dict):
            raise TypeError("fitting_data must be a dictionary")
        nested_fit = fitting_data.get("fit") if isinstance(fitting_data.get("fit"), dict) else fitting_data
        fit_name = fitting_data.get("fit_name") or fitting_data.get("fitting") or fitting_data.get("source_fit_name")
        fit_name = fit_name or nested_fit.get("fit_name")
        hull = fitting_data.get("hull") or nested_fit.get("hull")
        if not fit_name or not hull:
            raise ValueError("A fitting requires fit_name and hull")
        record = {
            "fit_uid": fitting_data.get("fit_uid"),
            "source": self._validate_source(source or fitting_data.get("source", "local")),
            "hull": hull,
            "fit_name": fit_name,
            "hull_type_id": fitting_data.get("hull_type_id", nested_fit.get("hull_type_id")),
            "fit": nested_fit,
        }
        record["fitting"] = fit_name
        if "schema_version" in fitting_data:
            record["schema_version"] = fitting_data["schema_version"]
        if fitting_data.get("doctrine_metadata"):
            record["doctrine_metadata"] = fitting_data["doctrine_metadata"]
        if fitting_data.get("renamed"):
            record["renamed"] = True        # a package fitting renamed here (UI rework 6.4); kept on updates (6.5)
        return record

    # --- doctrine metadata (design §5.1, §8) ----------------------------------------------

    def get_metadata(self, fit_uid: int) -> DoctrineMetadata:
        """The fitting's doctrine metadata; empty when it has none. Raises KeyError for an unknown fit."""
        record = self.get_fitting(fit_uid)
        if record is None:
            raise KeyError(f"Fitting UID {fit_uid} not found.")
        return DoctrineMetadata.from_dict(record.get("doctrine_metadata"))

    def set_metadata(self, fit_uid: int, metadata: DoctrineMetadata) -> DoctrineMetadata:
        """
        Validate and save a fitting's doctrine metadata, and return what was saved.
        Duplicate types are merged, empty bays dropped and updated_at stamped. An
        escape fitting must exist in the library; its hull and name are cached on
        the requirement so the audit can still name it if it's deleted later.
        Empty metadata removes the key. Raises KeyError for an unknown fit and
        MetadataError (listing every problem) for anything invalid.
        """
        record = self.get_fitting(fit_uid)
        if record is None:
            raise KeyError(f"Fitting UID {fit_uid} not found.")
        cleaned = metadata.normalized()
        problems = cleaned.problems(owner_fit_uid=fit_uid)
        for requirement in cleaned.requirements(ESCAPE_BAY.key):
            if requirement.match != "fit" or requirement.fit_uid is None:
                continue
            escape = self.get_fitting(requirement.fit_uid)
            if escape is None:
                problems.append(f"{ESCAPE_BAY.label}: fitting UID {requirement.fit_uid} doesn't exist")
            elif requirement.fit_uid == fit_uid:
                problems.append(f"{ESCAPE_BAY.label}: a fitting can't be its own escape ship")
            else:
                requirement.type_id = escape.get("hull_type_id")
                requirement.name = escape.get("fit_name", requirement.name)
        if problems:
            raise MetadataError(problems)

        if cleaned.is_empty():
            record.pop("doctrine_metadata", None)
        else:
            cleaned.updated_at = datetime.now().isoformat(timespec="seconds")
            record["doctrine_metadata"] = cleaned.to_dict()
        self._touch_and_save()
        return cleaned

    def find_escape_references(self, fit_uid: int) -> List[int]:
        """UIDs of the fittings that recommend this one as their escape ship (for the delete confirmation)."""
        found = []
        for record in self.list_fittings():
            bays = (record.get("doctrine_metadata") or {}).get("bays") or {}
            if any(isinstance(r, dict) and r.get("match") == "fit" and r.get("fit_uid") == fit_uid
                   for r in bays.get(ESCAPE_BAY.key) or []):
                found.append(record["fit_uid"])
        return sorted(found)

    def list_escape_candidates(self, for_fit_uid: int, sde_loader: Any) -> List[Dict[str, Any]]:
        """
        Saved fittings that can be recommended as for_fit_uid's escape ship: hulls
        in the escape bay's allowed groups (frigates, never covert ops or bombers),
        and only doctrine fittings when for_fit_uid is a doctrine fitting. Sorted by name.
        """
        doctrine_only = is_doctrine_fit_uid(for_fit_uid)
        candidates = []
        for record in self.list_fittings():
            uid = record.get("fit_uid")
            if uid == for_fit_uid or (doctrine_only and not is_doctrine_fit_uid(uid)):
                continue
            hull_type_id = record.get("hull_type_id") or (
                sde_loader.get_typeid_by_name(record["hull"]) if record.get("hull") else None)
            if hull_type_id and sde_loader.get_type_group(hull_type_id) in ESCAPE_BAY.allowed_groups:
                candidates.append(record)
        return sorted(candidates, key=lambda r: (str(r.get("fit_name", "")).casefold(), r.get("fit_uid")))

    def migrate(self, sde_loader: Any) -> Dict[str, int]:
        """
        Brings records older than CURRENT_SCHEMA up to date (design §12). Safe to
        run any number of times; records already current are left alone.

        Schema 2: fighters move from the fit's cargo into its "fighters" section
        (the importer filed them as cargo before step 1.4), and a missing
        top-level hull_type_id is filled from the fit or an SDE lookup.
        """
        summary = {"migrated": 0, "fighter_types_moved": 0, "hull_ids_filled": 0}
        for record in self._data.get("fittings", []):
            if record.get("schema_version", 1) >= CURRENT_SCHEMA:
                continue
            fit = record.get("fit") if isinstance(record.get("fit"), dict) else {}

            cargo = fit.get("cargo") or {}
            fighters = {t: item for t, item in cargo.items()
                        if str(t).isdigit() and sde_loader.get_type_category(int(t)) == FIGHTER_CATEGORY}
            if fighters:
                section = fit.setdefault("fighters", {})
                for type_id, item in fighters.items():
                    if type_id in section:
                        section[type_id]["quantity"] += item.get("quantity", 0)
                    else:
                        section[type_id] = item
                    del cargo[type_id]
                if not cargo:
                    fit.pop("cargo", None)
                summary["fighter_types_moved"] += len(fighters)

            if not record.get("hull_type_id"):
                hull_type_id = fit.get("hull_type_id") or (
                    sde_loader.get_typeid_by_name(record["hull"]) if record.get("hull") else None)
                if hull_type_id:
                    record["hull_type_id"] = hull_type_id
                    summary["hull_ids_filled"] += 1

            record["schema_version"] = CURRENT_SCHEMA
            summary["migrated"] += 1

        if summary["migrated"]:
            self._touch_and_save()
            logger.info(
                f"Migrated {summary['migrated']} fitting(s) to schema {CURRENT_SCHEMA}: moved "
                f"{summary['fighter_types_moved']} fighter type(s) out of cargo, filled "
                f"{summary['hull_ids_filled']} hull ID(s).")
        return summary

    def _doctrine_high_water(self) -> int:
        """The highest doctrine UID ever handed out (DOCTRINE_UID_MIN - 1 before the first)."""
        existing = [uid for uid in (r.get("fit_uid") for r in self.list_fittings())
                    if isinstance(uid, int) and DOCTRINE_UID_MIN <= uid <= DOCTRINE_UID_MAX]
        return max([self._data.get(HIGH_WATER_KEY, DOCTRINE_UID_MIN - 1), *existing])

    def _allocate_uid(self, source: str) -> int:
        if source == "doctrine":
            # Always count upwards: never reuse a deleted doctrine fitting's UID.
            candidate = self._doctrine_high_water() + 1
            if candidate > DOCTRINE_UID_MAX:
                raise ValueError("No doctrine fitting UIDs left in the 1000-1999 range")
            self._data[HIGH_WATER_KEY] = candidate
            return candidate
        # Local fittings are never shared, so reusing a free UID is harmless.
        used = {record.get("fit_uid") for record in self.list_fittings()}
        for candidate in range(LOCAL_UID_MIN, LOCAL_UID_MIN + len(used) + 1):
            if candidate not in used:
                return candidate
        raise ValueError(f"No available UID in the {source} namespace")

    @staticmethod
    def _validate_source(source: str) -> str:
        if source not in {"doctrine", "local"}:
            raise ValueError("source must be 'doctrine' or 'local'")
        return source

    @staticmethod
    def _uid_source(fit_uid: int) -> str:
        if DOCTRINE_UID_MIN <= fit_uid <= DOCTRINE_UID_MAX:
            return "doctrine"
        if fit_uid >= LOCAL_UID_MIN:
            return "local"
        raise ValueError("fit_uid is outside the supported namespaces")

    def _touch_and_save(self) -> None:
        self._data["generated_at"] = datetime.now().isoformat()
        self.save_fittings()


fittingManager = FittingManager
