import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional

from app.loaders.uid_high_water import FILE_NAME as HIGH_WATER_FILE, HighWaterMarks
from app.models.doctrine_metadata import is_doctrine_fit_uid

logger = logging.getLogger(__name__)


def is_shared_role_uid(role_uid: Optional[int]) -> bool:
    """Shared (doctrine) roles are 1000-1999; local roles are 2000+."""
    return role_uid is not None and 1000 <= role_uid <= 1999


def fit_matches_role(role_uid: int, fit_uid: int) -> bool:
    """
    Shared roles use shared fittings and local roles use local fittings, never a mix: a
    shared role travels in packages, and a local fitting can't go with it. To use a fit
    in both, import it once in each mode. Older data can still hold mixed pairings; the
    package importer keeps handling those (e.g. "Your own roles that will lose a fitting").
    """
    return is_shared_role_uid(role_uid) == is_doctrine_fit_uid(fit_uid)


def is_current_requirement(requirement: Dict[str, Any]) -> bool:
    """
    A requirement saves its location by ID: system_id and location_id (either may be
    None for "any"). Older ones saved names (system, station) and aren't used.
    """
    return "system_id" in requirement and "location_id" in requirement


def fitting_in_use(requirement: Dict[str, Any]) -> Optional[int]:
    """
    The fitting a requirement is audited and shown with: the pilot's replacement
    (doctrine tweaks plan, step 11.1) when there is one, else the requirement's own.
    """
    replacement = requirement.get('replacement')
    if isinstance(replacement, dict) and replacement.get('fit_uid') is not None:
        return replacement['fit_uid']
    return requirement.get('fit_uid')


PRIORITIES = ("hard", "soft")


def requirement_priority(requirement: Dict[str, Any]) -> str:
    """
    "hard" or "soft" (homes and priorities plan, P1). A hard requirement decides whether
    the pilot is ready; a soft one is audited in full but only ever warns. Saved only
    when soft, so older requirements and packages are hard.
    """
    return "soft" if requirement.get("priority") == "soft" else "hard"


class RoleManager:
    """
    Manages roles that reference fittings via fit_uids.
    Roles do not store fitting data, only references.
    """

    def __init__(self, roles_path: str):
        self.roles_path = Path(roles_path)
        self.high_water = HighWaterMarks(self.roles_path.with_name(HIGH_WATER_FILE))
        self.roles: Dict[int, Dict[str, Any]] = {}
        self.load_roles()

    def load_roles(self) -> None:
        """Loads roles from the JSON file."""
        if not self.roles_path.exists():
            self.roles = {}
            return

        try:
            with open(self.roles_path, 'r') as f:
                data = json.load(f)
                # Convert list to dict for easier access: {role_uid: role_data}
                self.roles = {role['role_uid']: role for role in data}
        except (json.JSONDecodeError, KeyError, FileNotFoundError) as e:
            logger.error(f"Error loading roles: {e}")
            self.roles = {}
            return

        # Requirements from before locations were saved by ID (SDE plan step 10.2) named
        # their system and station. Saved data from then is disposable: drop them.
        dropped = 0
        for role in self.roles.values():
            kept = [req for req in role.get('requirements', []) if is_current_requirement(req)]
            dropped += len(role.get('requirements', [])) - len(kept)
            role['requirements'] = kept
        if dropped:
            logger.warning(f"Dropped {dropped} requirement(s) saved by location name; add them again.")
            self.save_roles()

    def save_roles(self) -> None:
        """Saves roles to the JSON file."""
        try:
            # Convert dict back to list for JSON storage
            data_to_save = list(self.roles.values())
            with open(self.roles_path, 'w') as f:
                json.dump(data_to_save, f, indent=4)
        except Exception as e:
            logger.error(f"Error saving roles: {e}")

    def _generate_next_uid(self, start_val: int) -> int:
        """
        The next UID for start_val's range: 1000 = shared roles (1000-1999), 2000 = local
        roles (2000+), 4000 = requirements (4000+, across all roles). Shared role UIDs always
        count upwards from the high-water mark, so a deleted one is never reused.
        """
        if start_val == 1000:
            used = [uid for uid in self.roles if 1000 <= uid <= 1999]
            new_uid = max([self.high_water.get("roles", 999), *used]) + 1
            if new_uid > 1999:
                raise ValueError("Doctrine role UID range (1000-1999) exceeded.")
            return new_uid
        if start_val == 2000:
            used = [uid for uid in self.roles if uid >= 2000]
        else:
            used = [req['req_uid'] for role in self.roles.values()
                    for req in role.get('requirements', []) if 'req_uid' in req]
        return max(used) + 1 if used else start_val

    def _validate_name(self, name: str, exclude_uid: Optional[int] = None) -> str:
        """Trims, validates, and checks for name uniqueness."""
        cleaned_name = name.strip()
        if not cleaned_name:
            raise ValueError("Role name cannot be empty or whitespace-only.")
        
        # Check uniqueness (case-insensitive)
        for uid, role in self.roles.items():
            if uid == exclude_uid:
                continue
            if role['role_name'].lower() == cleaned_name.lower():
                raise ValueError(f"A role with the name '{cleaned_name}' already exists.")
        
        return cleaned_name

    def create_role(self, role_name: str, is_doctrine: bool = False) -> int:
        """Creates a new role and returns its role_uid."""
        valid_name = self._validate_name(role_name)
        start_val = 1000 if is_doctrine else 2000
        role_uid = self._generate_next_uid(start_val)
        if is_doctrine:
            self.high_water.raise_to("roles", role_uid)
        self.roles[role_uid] = {
            "role_uid": role_uid,
            "role_name": valid_name,
            "requirements": []
        }
        self.save_roles()
        return role_uid

    def inject_role(self, role_data: Dict[str, Any]) -> None:
        """
        Injects a role with a specific UID.
        Used for restoring doctrine packages.
        """
        uid = role_data.get('role_uid')
        if uid is None:
            raise ValueError("role_uid must be provided for injection.")
            
        # Validate name uniqueness (ignoring self)
        valid_name = self._validate_name(role_data['role_name'], exclude_uid=uid)
        role_data['role_name'] = valid_name
        if 1000 <= uid <= 1999:
            self.high_water.raise_to("roles", uid)

        self.roles[uid] = role_data
        self.save_roles()

    def rename_role(self, role_uid: int, new_name: str) -> None:
        """Renames an existing role."""
        if role_uid not in self.roles:
            raise KeyError(f"Role UID {role_uid} not found.")
        
        valid_name = self._validate_name(new_name, exclude_uid=role_uid)
        self.roles[role_uid]['role_name'] = valid_name
        self.save_roles()

    def delete_role(self, role_uid: int) -> bool:
        """Deletes a role by its role_uid."""
        if role_uid in self.roles:
            del self.roles[role_uid]
            self.save_roles()
            return True
        return False

    def get_role(self, role_uid: int) -> Optional[Dict[str, Any]]:
        """Retrieves a role by its role_uid."""
        return self.roles.get(role_uid)

    def list_roles(self) -> List[Dict[str, Any]]:
        """Returns a list of all roles."""
        return list(self.roles.values())

    def add_requirement(self, role_uid: int, fit_uid: int, system_id: Optional[int], location_id: Optional[int],
                        location_name: Optional[str] = None, added: bool = False, priority: str = "hard") -> int:
        """
        Adds a requirement to a role: the fitting, wanted in a solar system (None: any)
        and at a station or structure (None: any). location_name is only shown, when
        nothing else knows the location (a structure in someone else's package).
        added marks a pilot's own requirement on a role a package installed: updates
        ask before removing it, and exports leave it out. priority: "hard" or "soft".
        Returns the new req_uid.
        """
        role = self.get_role(role_uid)
        if not role:
            raise KeyError(f"Role UID {role_uid} not found.")
        if priority not in PRIORITIES:
            raise ValueError(f"A requirement is hard or soft, not {priority!r}.")
        if not fit_matches_role(role_uid, fit_uid):
            kind, other = ("shared", "local") if is_shared_role_uid(role_uid) else ("local", "shared")
            raise ValueError(f"A {kind} role can't use a {other} fitting. To use this fit here, import it again "
                             f"{'with' if kind == 'shared' else 'without'} \"Shared Doctrine Fitting\" ticked.")

        for req in role.get('requirements', []):
            if (fitting_in_use(req), req.get('system_id'), req.get('location_id')) == (fit_uid, system_id, location_id):
                raise ValueError("This role already has a requirement for that fitting at that location.")

        req_uid = self._generate_next_uid(4000)
        requirement = {
            "req_uid": req_uid,
            "fit_uid": fit_uid,
            "system_id": system_id,
            "location_id": location_id,
            "location_name": location_name,
        }
        if added:
            requirement["added"] = True
        if priority == "soft":
            requirement["priority"] = "soft"
        role['requirements'].append(requirement)
        self.save_roles()
        return req_uid

    def set_requirement_priority(self, role_uid: int, req_uid: int, priority: str) -> bool:
        """Makes a requirement hard or soft. False when it already was."""
        if priority not in PRIORITIES:
            raise ValueError(f"A requirement is hard or soft, not {priority!r}.")
        requirement = self._requirement(role_uid, req_uid)
        if requirement_priority(requirement) == priority:
            return False
        if priority == "soft":
            requirement["priority"] = "soft"
        else:
            requirement.pop("priority", None)
        self.save_roles()
        return True

    def _requirement(self, role_uid: int, req_uid: int) -> Dict[str, Any]:
        role = self.get_role(role_uid)
        if not role:
            raise KeyError(f"Role UID {role_uid} not found.")
        found = next((r for r in role.get('requirements', []) if r.get('req_uid') == req_uid), None)
        if found is None:
            raise KeyError(f"Requirement UID {req_uid} not found in role {role_uid}.")
        return found

    def replace_requirement(self, role_uid: int, req_uid: int, fit_uid: int) -> None:
        """
        The pilot's correction: audit and show this requirement with another fitting,
        keeping its place. The original stays underneath. Replacing with the original
        fitting is an undo. Any fitting the role could use is allowed (decision 11-D).
        """
        requirement = self._requirement(role_uid, req_uid)
        if fit_uid == requirement.get('fit_uid'):
            self.undo_replacement(role_uid, req_uid)
            return
        if not fit_matches_role(role_uid, fit_uid):
            kind, other = ("shared", "local") if is_shared_role_uid(role_uid) else ("local", "shared")
            raise ValueError(f"A {kind} role can't use a {other} fitting.")
        role = self.roles[role_uid]
        for req in role.get('requirements', []):
            if req is not requirement and (fitting_in_use(req), req.get('system_id'), req.get('location_id')) == (
                    fit_uid, requirement.get('system_id'), requirement.get('location_id')):
                raise ValueError("This role already has a requirement for that fitting at that location.")
        requirement['replacement'] = {"fit_uid": fit_uid, "replaced_at": datetime.now().isoformat(timespec="seconds")}
        self.save_roles()

    def undo_replacement(self, role_uid: int, req_uid: int) -> bool:
        """Removes the pilot's correction: the requirement's own fitting applies again. False if there was none."""
        requirement = self._requirement(role_uid, req_uid)
        if requirement.pop('replacement', None) is None:
            return False
        self.save_roles()
        return True

    def remove_roles(self, role_uids) -> int:
        """Deletes several roles with one save. Returns how many were removed."""
        removed = [uid for uid in set(role_uids) if self.roles.pop(uid, None) is not None]
        if removed:
            self.save_roles()
        return len(removed)

    def requirements_using_fittings(self, fit_uids) -> List[Dict[str, Any]]:
        """Requirements (with their role) whose own fitting is any of these (remove_requirements_for_fittings removes them)."""
        fit_uids = set(fit_uids)
        return [{"role_uid": uid, "role_name": role.get('role_name', str(uid)), **req}
                for uid, role in sorted(self.roles.items())
                for req in role.get('requirements', []) if req.get('fit_uid') in fit_uids]

    def remove_requirements_for_fittings(self, fit_uids, role_uids=None) -> int:
        """
        Removes every requirement that points at any of these fittings, with one save.
        A replacement that does loses only the replacement: the requirement's own
        fitting applies again. With role_uids, only those roles' requirements are touched.
        Returns how many requirements were removed.
        """
        fit_uids = set(fit_uids)
        removed, undone = 0, 0
        for uid, role in self.roles.items():
            if role_uids is not None and uid not in role_uids:
                continue
            kept = [r for r in role.get('requirements', []) if r.get('fit_uid') not in fit_uids]
            removed += len(role.get('requirements', [])) - len(kept)
            for requirement in kept:
                if fitting_in_use(requirement) in fit_uids and requirement.pop('replacement', None) is not None:
                    undone += 1
            role['requirements'] = kept
        if removed or undone:
            self.save_roles()
        return removed

    def clear(self) -> None:
        """Deletes every role."""
        self.roles = {}
        self.save_roles()

    def remove_requirement(self, role_uid: int, req_uid: int) -> bool:
        """
        Removes a requirement from a role by its req_uid.
        """
        role = self.get_role(role_uid)
        if not role:
            return False

        original_count = len(role['requirements'])
        role['requirements'] = [
            r for r in role['requirements'] 
            if r.get('req_uid') != req_uid
        ]

        if len(role['requirements']) != original_count:
            self.save_roles()
            return True
        return False
