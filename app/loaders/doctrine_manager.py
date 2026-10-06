import json
import logging
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple

from app.loaders.uid_high_water import FILE_NAME as HIGH_WATER_FILE, HighWaterMarks

logger = logging.getLogger(__name__)

# Doctrine UID ranges (decision D18), the same split fittings and roles use:
# shared doctrines (created in Doctrine Mode, or installed from a package) and
# the pilot's own local doctrines.
DOCTRINE_UID_MIN = 5000
DOCTRINE_UID_MAX = 5999
LOCAL_DOCTRINE_UID_MIN = 6000


def _merge_duplicate_list_keys(pairs):
    """json object_pairs_hook: merges list values that appear under a repeated key."""
    result = {}
    for key, value in pairs:
        existing = result.get(key)
        if isinstance(existing, list) and isinstance(value, list):
            result[key] = existing + [v for v in value if v not in existing]
        else:
            result[key] = value
    return result


def _normalize_assignments(doctrine: Dict[str, Any]) -> None:
    """
    Stores character_assignments keyed by str(role_uid), as JSON round-trips them.
    Values from int and str variants of the same key are merged.
    """
    assignments = doctrine.get('character_assignments')
    if not isinstance(assignments, dict):
        return
    normalized: Dict[str, List[str]] = {}
    for role_uid, char_ids in assignments.items():
        merged = normalized.setdefault(str(role_uid), [])
        for char_id in char_ids:
            if char_id not in merged:
                merged.append(char_id)
    doctrine['character_assignments'] = normalized


class DoctrineManager:
    """
    Manages doctrines that reference roles via role_uids.
    Doctrines do not store fitting data or character data, only role references.
    """

    def __init__(self, doctrines_path: str):
        self.doctrines_path = Path(doctrines_path)
        self.high_water = HighWaterMarks(self.doctrines_path.with_name(HIGH_WATER_FILE))
        self.doctrines: Dict[int, Dict[str, Any]] = {}
        self.load_doctrines()

    def load_doctrines(self) -> None:
        """Loads doctrines from the JSON file."""
        if not self.doctrines_path.exists():
            self.doctrines = {}
            return

        try:
            with open(self.doctrines_path, 'r') as f:
                # Older files can contain duplicate role keys in character_assignments
                # (int and str keys were both written); merge them instead of letting
                # the last one silently win.
                data = json.load(f, object_pairs_hook=_merge_duplicate_list_keys)
                # Convert list to dict for easier access: {doctrine_uid: doctrine_data}
                self.doctrines = {d['doctrine_uid']: d for d in data}
                for doctrine in self.doctrines.values():
                    _normalize_assignments(doctrine)
        except (json.JSONDecodeError, KeyError, FileNotFoundError) as e:
            logger.error(f"Error loading doctrines: {e}")
            self.doctrines = {}

    def save_doctrines(self) -> None:
        """Saves doctrines to the JSON file."""
        try:
            # Convert dict back to list for JSON storage
            data_to_save = list(self.doctrines.values())
            # Ensure directory exists
            self.doctrines_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.doctrines_path, 'w') as f:
                json.dump(data_to_save, f, indent=4)
        except Exception as e:
            logger.error(f"Error saving doctrines: {e}")

    def _generate_next_uid(self, is_doctrine: bool) -> int:
        """
        The next UID in the shared (5000-5999) or local (6000+) range. Shared UIDs always
        count upwards from the high-water mark, so a deleted one is never reused.
        """
        if is_doctrine:
            used = [uid for uid in self.doctrines if DOCTRINE_UID_MIN <= uid <= DOCTRINE_UID_MAX]
            uid = max([self.high_water.get("doctrines", DOCTRINE_UID_MIN - 1), *used]) + 1
            if uid > DOCTRINE_UID_MAX:
                raise ValueError("No doctrine UIDs left in the 5000-5999 range.")
            return uid
        used = [uid for uid in self.doctrines if uid >= LOCAL_DOCTRINE_UID_MIN]
        return max(used) + 1 if used else LOCAL_DOCTRINE_UID_MIN

    def _validate_name(self, name: str, exclude_uid: Optional[int] = None) -> str:
        """Trims, validates, and checks for name uniqueness."""
        cleaned_name = name.strip()
        if not cleaned_name:
            raise ValueError("Doctrine name cannot be empty or whitespace-only.")
        
        # Check uniqueness (case-insensitive)
        for uid, doctrine in self.doctrines.items():
            if uid == exclude_uid:
                continue
            if doctrine['doctrine_name'].lower() == cleaned_name.lower():
                raise ValueError(f"A doctrine with the name '{cleaned_name}' already exists.")
        
        return cleaned_name

    def create_doctrine(self, doctrine_name: str, is_doctrine: bool = True) -> int:
        """
        Creates a new doctrine and returns its doctrine_uid: 5000-5999 when
        is_doctrine (Doctrine Mode), 6000+ for a local doctrine (D18).
        """
        valid_name = self._validate_name(doctrine_name)
        doctrine_uid = self._generate_next_uid(is_doctrine)
        if is_doctrine:
            self.high_water.raise_to("doctrines", doctrine_uid)
        self.doctrines[doctrine_uid] = {
            "doctrine_uid": doctrine_uid,
            "doctrine_name": valid_name,
            "roles": []
        }
        self.save_doctrines()
        return doctrine_uid

    def inject_doctrine(self, doctrine_data: Dict[str, Any]) -> None:
        """
        Injects a doctrine with a specific UID. 
        Used for restoring doctrine packages.
        """
        uid = doctrine_data.get('doctrine_uid')
        if uid is None:
            raise ValueError("doctrine_uid must be provided for injection.")
        
        # Validate name uniqueness (ignoring self)
        valid_name = self._validate_name(doctrine_data['doctrine_name'], exclude_uid=uid)
        doctrine_data['doctrine_name'] = valid_name
        _normalize_assignments(doctrine_data)
        if DOCTRINE_UID_MIN <= uid <= DOCTRINE_UID_MAX:
            self.high_water.raise_to("doctrines", uid)

        self.doctrines[uid] = doctrine_data
        self.save_doctrines()

    def rename_doctrine(self, doctrine_uid: int, new_name: str) -> None:
        """Renames an existing doctrine."""
        if doctrine_uid not in self.doctrines:
            raise KeyError(f"Doctrine UID {doctrine_uid} not found.")
        
        valid_name = self._validate_name(new_name, exclude_uid=doctrine_uid)
        self.doctrines[doctrine_uid]['doctrine_name'] = valid_name
        self.save_doctrines()

    def get_doctrine_by_role_uid(self, role_uid: int) -> Optional[Dict[str, Any]]:
        """
        Finds the doctrine that contains the specified role_uid.
        """
        for doctrine in self.doctrines.values():
            if role_uid in doctrine.get('roles', []):
                return doctrine
        return None

    def delete_doctrine(self, doctrine_uid: int) -> bool:
        """Deletes a doctrine by its doctrine_uid."""
        if doctrine_uid in self.doctrines:
            del self.doctrines[doctrine_uid]
            self.save_doctrines()
            return True
        return False

    def get_doctrine(self, doctrine_uid: int) -> Optional[Dict[str, Any]]:
        """Retrieves a doctrine by its doctrine_uid."""
        return self.doctrines.get(doctrine_uid)

    def list_doctrines(self) -> List[Dict[str, Any]]:
        """Returns a list of all doctrines."""
        return list(self.doctrines.values())

    def add_role(self, doctrine_uid: int, role_uid: int) -> None:
        """
        Adds a role to a doctrine.
        Prevents duplicate role_uids from being added to the same doctrine.
        """
        doctrine = self.get_doctrine(doctrine_uid)
        if not doctrine:
            raise KeyError(f"Doctrine UID {doctrine_uid} not found.")
        
        if role_uid in doctrine['roles']:
            raise ValueError(f"Role UID {role_uid} is already assigned to this doctrine.")
        
        doctrine['roles'].append(role_uid)
        self.save_doctrines()

    def remove_role_from_doctrine(self, doctrine_uid: int, role_uid: int) -> List[str]:
        """
        Removes a role from one doctrine, with its character assignments there (the
        Library's Doctrine Overview, UI rework step 7.3). The role itself and its other
        doctrines are untouched. Returns the character IDs that were assigned to it.
        Raises KeyError for an unknown doctrine.
        """
        doctrine = self.get_doctrine(doctrine_uid)
        if not doctrine:
            raise KeyError(f"Doctrine UID {doctrine_uid} not found.")
        dropped = list((doctrine.get('character_assignments') or {}).pop(str(role_uid), []))
        had_role = role_uid in doctrine.get('roles', [])
        doctrine['roles'] = [r for r in doctrine.get('roles', []) if r != role_uid]
        if had_role or dropped:
            self.save_doctrines()
        return dropped

    def doctrines_using_role(self, role_uid: int) -> List[Dict[str, Any]]:
        """Every doctrine that includes this role."""
        return [d for d in self.doctrines.values() if role_uid in d.get('roles', [])]

    def remove_role_everywhere(self, role_uid: int) -> List[str]:
        """
        Removes a role from every doctrine, with its character assignments there
        (used when the role itself is deleted). Returns the names of the doctrines
        that changed.
        """
        changed = []
        for doctrine in self.doctrines.values():
            had_role = role_uid in doctrine.get('roles', [])
            had_assignments = str(role_uid) in (doctrine.get('character_assignments') or {})
            if not (had_role or had_assignments):
                continue
            doctrine['roles'] = [r for r in doctrine.get('roles', []) if r != role_uid]
            (doctrine.get('character_assignments') or {}).pop(str(role_uid), None)
            changed.append(doctrine['doctrine_name'])
        if changed:
            self.save_doctrines()
        return changed

    def assign_character_to_role(self, doctrine_uid: int, role_uid: int, character_id: str) -> None:
        """
        Assigns a character to a specific role within a doctrine.
        Stores assignments in the doctrine data.
        """
        doctrine = self.get_doctrine(doctrine_uid)
        if not doctrine:
            raise KeyError(f"Doctrine UID {doctrine_uid} not found.")

        # Initialize 'character_assignments' if not present
        if 'character_assignments' not in doctrine:
            doctrine['character_assignments'] = {}

        # structure: { str(role_uid): [char_id1, char_id2, ...] } (str keys, as JSON stores them)
        role_key = str(role_uid)
        if role_key not in doctrine['character_assignments']:
            doctrine['character_assignments'][role_key] = []

        assignments = doctrine['character_assignments'][role_key]
        if character_id not in assignments:
            assignments.append(character_id)
            self.save_doctrines()
        else:
            raise ValueError(f"Character {character_id} is already assigned to role {role_uid} in this doctrine.")

    def remove_character_from_role(self, doctrine_uid: int, role_uid: int, character_id: str) -> None:
        """
        Removes a character assignment from a role within a doctrine.
        """
        doctrine = self.get_doctrine(doctrine_uid)
        if not doctrine:
            raise KeyError(f"Doctrine UID {doctrine_uid} not found.")

        if 'character_assignments' not in doctrine:
            return

        assignments = doctrine['character_assignments'].get(str(role_uid), [])
        if character_id in assignments:
            assignments.remove(character_id)
            self.save_doctrines()
        else:
            raise ValueError(f"Character {character_id} is not assigned to role {role_uid} in this doctrine.")

    def remove_doctrines(self, doctrine_uids) -> int:
        """Deletes several doctrines with one save. Returns how many were removed."""
        removed = [uid for uid in set(doctrine_uids) if self.doctrines.pop(uid, None) is not None]
        if removed:
            self.save_doctrines()
        return len(removed)

    def clear(self) -> None:
        """Deletes every doctrine."""
        self.doctrines = {}
        self.save_doctrines()

    def assignments_for_character(self, character_id: str) -> List[Tuple[Dict[str, Any], int]]:
        """Every (doctrine, role_uid) the character is assigned to."""
        character_id = str(character_id)
        found = []
        for doctrine in self.doctrines.values():
            for role_key, char_ids in (doctrine.get('character_assignments') or {}).items():
                if character_id in (str(c) for c in char_ids):
                    found.append((doctrine, int(role_key)))
        return found

    def remove_character_everywhere(self, character_id: str) -> int:
        """
        Removes the character from every role in every doctrine (used when the
        character itself is removed). Roles stay in their doctrines even if they
        end up with no characters. Returns how many assignments were removed.
        """
        character_id = str(character_id)
        removed = 0
        for doctrine in self.doctrines.values():
            for role_key, char_ids in (doctrine.get('character_assignments') or {}).items():
                kept = [c for c in char_ids if str(c) != character_id]
                removed += len(char_ids) - len(kept)
                char_ids[:] = kept
        if removed:
            self.save_doctrines()
        return removed

    def get_character_assignments(self, doctrine_uid: int) -> Dict[str, List[str]]:
        """Returns the character assignments for a doctrine, keyed by str(role_uid)."""
        doctrine = self.get_doctrine(doctrine_uid)
        if not doctrine:
            return {}
        return doctrine.get('character_assignments', {})
