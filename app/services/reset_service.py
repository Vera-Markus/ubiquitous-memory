import os
import shutil
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional, Tuple

from app.loaders.package_registry import in_protected_range


@dataclass
class LocalClearPlan:
    """What Clear Non-Doctrine Data removes: everything outside the protected (package) ranges."""
    fittings: List[str] = field(default_factory=list)          # names
    roles: List[str] = field(default_factory=list)
    doctrines: List[str] = field(default_factory=list)
    package_doctrine_roles: List[Tuple[str, str]] = field(default_factory=list)   # (package doctrine, local role) it loses
    requirements: List[Tuple[str, str]] = field(default_factory=list)              # (package role, local fitting) it loses
    fit_uids: List[int] = field(default_factory=list)
    role_uids: List[int] = field(default_factory=list)
    doctrine_uids: List[int] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.fit_uids or self.role_uids or self.doctrine_uids)

    def summary(self, limit: int = 8) -> str:
        def listed(title: str, items: List[str]) -> List[str]:
            if not items:
                return []
            shown = [f"    {item}" for item in items[:limit]]
            if len(items) > limit:
                shown.append(f"    …and {len(items) - limit} more")
            return [f"- {title} ({len(items)}):", *shown]

        lines = ["This deletes everything created with Doctrine Mode off:"]
        lines += listed("local fittings", self.fittings)
        lines += listed("local roles, with their requirements", self.roles)
        lines += listed("local doctrines, with their character assignments", self.doctrines)
        if self.package_doctrine_roles:
            lines.append("")
            lines += listed("package doctrines that lose a local role",
                            [f"{d} → {r}" for d, r in self.package_doctrine_roles])
        if self.requirements:
            lines += listed("package roles that lose a requirement for a local fitting",
                            [f"{r} → {f}" for r, f in self.requirements])
        lines.append("\nPackage fittings, roles and doctrines are kept.")
        return "\n".join(lines)


class ResetService:
    """Service responsible for resetting local application data."""

    def __init__(self, project_root: Path, auth_dir: Optional[Path] = None, raw_dir: Optional[Path] = None,
                 generated_dir: Optional[Path] = None, config_dir: Optional[Path] = None,
                 clones_dir: Optional[Path] = None, corp_dir: Optional[Path] = None):
        """
        Initialize the ResetService.

        Args:
            project_root (Path): The root directory of the project.
            The data folders default to project_root/data/<name>; the GUI passes
            app.paths' folders so tests can redirect them.
        """
        self.project_root = project_root
        data = project_root / "data"
        self.auth_dir = Path(auth_dir or data / "auth")
        self.raw_dir = Path(raw_dir or data / "raw")
        self.generated_dir = Path(generated_dir or data / "generated")
        self.config_dir = Path(config_dir or data / "config")
        self.clones_dir = Path(clones_dir or self.raw_dir.parent / "clones")
        self.corp_dir = Path(corp_dir or self.raw_dir.parent / "corp")

    # --- Clear Asset Data ------------------------------------------------------------------

    def asset_files(self) -> List[Path]:
        files = sorted(self.raw_dir.glob("*.json")) if self.raw_dir.exists() else []
        if self.clones_dir.exists():
            files += sorted(self.clones_dir.glob("*.json"))      # clones and implants: pulled data too
        if self.corp_dir.exists():
            files += sorted(self.corp_dir.glob("*.json"))        # corporation hangars: pulled data too
        if (self.generated_dir / "all_assets.json").exists():
            files.append(self.generated_dir / "all_assets.json")
        return files

    def clear_assets(self, pull_state_service: Any = None) -> int:
        """
        Deletes the pulled asset data (the raw files and the merged file) and forgets
        when the last pulls ran, so a pull can start straight away. Logins, the
        library and the Auto Pull setting stay. So does location_cache.json: player
        structure names can only be looked up again while a character has access.
        Returns how many files went.
        """
        files = self.asset_files()
        for path in files:
            path.unlink()
        if pull_state_service is not None:
            pull_state_service.last_pull_time = None
            pull_state_service.last_auto_pull_time = None
            pull_state_service.save()
        return len(files)

    # --- Clear Non-Doctrine Data -------------------------------------------------------------

    def plan_clear_local_library(self, fittings: Any, roles: Any, doctrines: Any) -> LocalClearPlan:
        plan = LocalClearPlan()
        local_fits = {f["fit_uid"]: f.get("fit_name", str(f["fit_uid"])) for f in fittings.list_fittings()
                      if not in_protected_range("fit", f.get("fit_uid"))}
        local_roles = {uid: r.get("role_name", str(uid)) for uid, r in roles.roles.items()
                       if not in_protected_range("role", uid)}
        local_doctrines = {uid: d.get("doctrine_name", str(uid)) for uid, d in doctrines.doctrines.items()
                           if not in_protected_range("doctrine", uid)}

        plan.fit_uids, plan.fittings = sorted(local_fits), sorted(local_fits.values(), key=str.lower)
        plan.role_uids, plan.roles = sorted(local_roles), sorted(local_roles.values(), key=str.lower)
        plan.doctrine_uids, plan.doctrines = sorted(local_doctrines), sorted(local_doctrines.values(), key=str.lower)

        for uid, doctrine in sorted(doctrines.doctrines.items()):
            if uid in local_doctrines:
                continue
            for role_uid in doctrine.get("roles", []):
                if role_uid in local_roles:
                    plan.package_doctrine_roles.append((doctrine["doctrine_name"], local_roles[role_uid]))
        for req in roles.requirements_using_fittings(local_fits):
            if req["role_uid"] not in local_roles:
                plan.requirements.append((req["role_name"], local_fits[req["fit_uid"]]))
        return plan

    def clear_local_library(self, fittings: Any, roles: Any, doctrines: Any) -> LocalClearPlan:
        """Removes everything plan_clear_local_library lists. Returns that plan."""
        plan = self.plan_clear_local_library(fittings, roles, doctrines)
        doctrines.remove_doctrines(plan.doctrine_uids)
        for role_uid in plan.role_uids:
            doctrines.remove_role_everywhere(role_uid)       # also drops the role's assignments
        roles.remove_requirements_for_fittings(plan.fit_uids)
        roles.remove_roles(plan.role_uids)
        fittings.remove_fittings(plan.fit_uids)
        return plan

    # --- Clear Library -------------------------------------------------------------------------

    def clear_library(self, fittings: Any, roles: Any, doctrines: Any, registry: Any,
                      ship_designations: Any = None) -> Tuple[int, int, int]:
        """
        Deletes every fitting, role and doctrine (with their requirements and
        character assignments), the installed-package records, the remembered
        export package names and the ships' designations (they point at
        fittings).
        Asset data and logins stay. Returns the counts removed.
        """
        counts = (len(fittings.list_fittings()), len(roles.roles), len(doctrines.doctrines))
        if ship_designations is not None:
            ship_designations.clear()
        doctrines.clear()
        roles.clear()
        fittings.clear()
        registry.entries = {}
        registry.save()
        profiles = self.config_dir / "export_profiles.json"
        if profiles.exists():
            profiles.unlink()
        return counts

    # --- Full Reset ------------------------------------------------------------------------------

    def reset_local_data(self) -> Tuple[bool, str]:
        """
        Full reset: performs the actual deletion of local data files. The app
        closes afterwards, because its managers still hold the old data.

        Returns:
            Tuple[bool, str]: (True, success_message) if successful, (False, error_message) otherwise.
        """
        try:
            # 1. Define paths to clear. data/generated holds the whole library: fittings,
            # roles, doctrines and installed_packages.json (the package registry goes with
            # the records it describes). export_profiles.json remembers which records each
            # exported package held, so it goes too.
            paths_to_clear = [
                self.auth_dir,
                self.raw_dir,
                self.clones_dir,
                self.corp_dir,
                self.generated_dir,
                self.config_dir / "pull_state.json",
                self.config_dir / "esi_cache.json",
                self.config_dir / "export_profiles.json",
            ]

            # 2. Perform deletion
            for path in paths_to_clear:
                if not path.exists():
                    continue

                if path.is_dir():
                    # Remove read-only attribute if present
                    os.chmod(path, stat.S_IWRITE)

                    shutil.rmtree(path)
                    path.mkdir(parents=True, exist_ok=True)
                else:
                    path.unlink()

            return True, "Local data reset completed successfully."

        except Exception as e:
            return False, f"An error occurred during reset: {e}"
