"""
Doctrine package import (design §11.4-11.5, decision D17).

An import replaces what the same package installed last time, so recipients
get exactly the current doctrine. It plans first and writes nothing until the
plan is confirmed:

    plan = service.plan_import(path)                 # raises PackageError for a file it can't use
    if plan.conflicts: show them; nothing can be applied
    result = service.apply_import(plan)              # backs up, applies, restores on failure

Only records in the protected ranges are ever touched (fittings and roles
1000-1999, doctrines 5000-5999). The pilot's own records never are.
"""
import copy
import json
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.loaders.package_registry import KINDS, PackageRegistry, in_protected_range
from app.loaders.role_manager import fitting_in_use, is_current_requirement
from app.models.bay_registry import BAYS
from app.models.doctrine_metadata import DoctrineMetadata, MetadataError
from app.services.doctrine_export_service import PACKAGE_TYPE, PACKAGE_VERSION

SUPPORTED_VERSIONS = ("2.0",)
# Anything this tool didn't export (a different file, a hand-edited package, a format
# before 2.0) gets one message; PackageError.details says why, for the session log.
INVALID_FILE = "This isn't a valid import file. Try again with a doctrine package exported from this tool."
NEWER_VERSION = "This package was made by a newer version of the tool. Update the tool to import it."
UID_KEYS = {"fit": "fit_uid", "role": "role_uid", "doctrine": "doctrine_uid"}
SECTIONS = {"fit": "fittings", "role": "roles", "doctrine": "doctrines"}
NAME_KEYS = {"fit": "fit_name", "role": "role_name", "doctrine": "doctrine_name"}
LABELS = {"fit": "fitting", "role": "role", "doctrine": "doctrine"}


class PackageError(ValueError):
    """A package file that can't be imported at all: problems are shown, details logged."""

    def __init__(self, problems: List[str], details: Optional[List[str]] = None):
        self.problems = list(problems)
        self.details = list(details or [])
        super().__init__("; ".join(self.problems))


class ImportFailed(RuntimeError):
    """Applying failed part-way; the library was restored from the backups."""


@dataclass
class RemovedRequirement:
    """
    An installed requirement the update would remove (doctrine tweaks plan, step 11.4).
    A retired one's fitting won't be in the library afterwards: it can't be kept, but
    the fitting can be moved to the pilot's own fittings (decision 11-C).
    """
    role_uid: int
    req_uid: int
    role_name: str
    fit_name: str                   # the fitting in use
    place: str
    replacing: Optional[str]        # the original fitting's name, when the pilot replaced it
    retired: bool
    role_stays: bool                # False when the package drops the whole role
    retired_fit_uids: List[int] = field(default_factory=list)   # what Move to personal copies

    @property
    def key(self) -> Tuple[int, int]:
        return (self.role_uid, self.req_uid)

    def label(self) -> str:
        replaced = f" (replacing {self.replacing})" if self.replacing else ""
        return f"{self.role_name}: {self.fit_name}{replaced} | {self.place}"


@dataclass
class ImportPlan:
    package: Dict[str, Any]
    registry_key: str
    package_id: str
    package_name: str
    exported_at: str
    approved: bool = False          # the exporter ticked Doctrine Manager
    add: Dict[str, List[int]] = field(default_factory=lambda: {k: [] for k in KINDS})
    update: Dict[str, List[int]] = field(default_factory=lambda: {k: [] for k in KINDS})
    remove: Dict[str, List[int]] = field(default_factory=lambda: {k: [] for k in KINDS})
    conflicts: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    kept_assignments: Dict[int, Dict[str, List[str]]] = field(default_factory=dict)
    dropped_assignments: List[str] = field(default_factory=list)
    package_has_assignments: bool = False
    orphaned_local_requirements: List[str] = field(default_factory=list)
    removed_names: List[str] = field(default_factory=list)
    # Updated fittings, compared with the local copy (UI rework step 6.5, user decision 2026-10-04):
    # same UID and hull -> the changes are applied; a different hull -> the local copy is replaced,
    # and the user's own roles drop their requirements for it (the package's roles already point
    # at the right fittings).
    fits_changed: List[str] = field(default_factory=list)          # same hull, contents differ
    fits_unchanged: List[str] = field(default_factory=list)
    fits_hull_changed: List[str] = field(default_factory=list)     # "Name (Devoter -> Revelation)"
    kept_names: List[str] = field(default_factory=list)            # "Your Name (the package calls it X)"
    local_requirements_on_new_hull: List[str] = field(default_factory=list)   # "Role: Fitting (Archon -> Revelation)"
    hull_changed_uids: List[int] = field(default_factory=list)
    # Pilots' corrections on package roles (doctrine tweaks plan, step 11.3): found by req_uid
    # and written onto the package's roles in the plan, so applying keeps them.
    corrections_kept: List[str] = field(default_factory=list)      # "Dread 1: SNUFF Moros 1.0 (replacing SNUFF RNI 1.0)"
    corrections_changed: List[str] = field(default_factory=list)   # the fitting manager changed the fitting (11-B)
    corrections_dropped: List[str] = field(default_factory=list)   # the replacement fitting left the package
    # Step 11.4: what the update would remove, and the pilot's choices (choose_removals).
    removed_requirements: List[RemovedRequirement] = field(default_factory=list)
    keep: List[Tuple[int, int]] = field(default_factory=list)                # (role_uid, req_uid)
    move_to_personal: List[Tuple[int, int]] = field(default_factory=list)
    # A pilot's own requirements on package roles the update would remove (asked first: keep them?).
    added_requirements: List[RemovedRequirement] = field(default_factory=list)
    keep_added: List[Tuple[int, int]] = field(default_factory=list)

    def choose_added(self, keep=()) -> None:
        """Which of the pilot's added requirements to keep (none when they answered No)."""
        rows = {r.key for r in self.added_requirements}
        self.keep_added = [k for k in keep if k in rows]

    def choose_removals(self, keep=(), move_to_personal=()) -> None:
        """The pilot's ticks: requirements to keep (not retired) and retired ones whose fitting to move."""
        rows = {r.key: r for r in self.removed_requirements}
        self.keep = [k for k in keep if k in rows and not rows[k].retired and rows[k].role_stays]
        self.move_to_personal = [k for k in move_to_personal if k in rows and rows[k].retired]

    def kept_lines(self) -> List[str]:
        rows = {r.key: r for r in self.removed_requirements + self.added_requirements}
        return [rows[k].label() for k in self.keep + self.keep_added]

    def removed_lines(self) -> List[str]:
        """Requirements that go: everything listed that the pilot didn't keep."""
        kept = set(self.keep + self.keep_added)
        return [r.label() for r in self.removed_requirements + self.added_requirements if r.key not in kept]

    def moved_lines(self) -> List[str]:
        rows = {r.key: r for r in self.removed_requirements}
        return sorted({rows[k].fit_name for k in self.move_to_personal})

    @property
    def ok(self) -> bool:
        return not self.conflicts

    def counts(self) -> Tuple[int, int, int]:
        return (sum(len(v) for v in self.add.values()), sum(len(v) for v in self.update.values()),
                sum(len(v) for v in self.remove.values()))

    def summary(self) -> str:
        """"Update SNUFF Public (exported 2026-10-12): adds 2, updates 19, removes 3 (…)." """
        added, updated, removed = self.counts()
        verb = "Update" if updated or removed else "Install"
        notes = ([f"exported {self.exported_at[:10]}"] if self.exported_at else []) + \
            (["Doctrine Manager approved"] if self.approved else [])
        when = f" ({', '.join(notes)})" if notes else ""
        text = f"{verb} {self.package_name}{when}: adds {added}, updates {updated}, removes {removed}"
        if self.removed_names:
            shown = ", ".join(self.removed_names[:5]) + (", …" if len(self.removed_names) > 5 else "")
            text += f" ({shown})"
        text += "."
        if self.fits_hull_changed:
            n = len(self.fits_hull_changed)
            text += f" {n} fitting{'s' if n != 1 else ''} will be replaced because the hull changed."
        if self.dropped_assignments:
            n = len(self.dropped_assignments)
            text += f" {n} character assignment{'s' if n != 1 else ''} will be dropped because {'their roles were' if n != 1 else 'its role was'} removed."
        return text


class DoctrineImportService:
    def __init__(self, fitting_manager: Any, role_manager: Any, doctrine_manager: Any, registry: PackageRegistry,
                 sde_loader: Optional[Any] = None):
        self.fittings = fitting_manager
        self.roles = role_manager
        self.doctrines = doctrine_manager
        self.registry = registry
        self.sde = sde_loader

    # --- reading the file ----------------------------------------------------------------

    def read_package(self, path) -> Dict[str, Any]:
        try:
            with open(path, "r", encoding="utf-8") as file:
                package = json.load(file)
        except (OSError, ValueError) as error:
            raise PackageError([INVALID_FILE], [f"not readable as JSON: {error}"])
        if not isinstance(package, dict) or package.get("package_type") != PACKAGE_TYPE:
            raise PackageError([INVALID_FILE], ["not a doctrine package"])
        version = str(package.get("package_version") or "")
        if version not in SUPPORTED_VERSIONS:
            if _version_tuple(version) > _version_tuple(PACKAGE_VERSION):
                raise PackageError([NEWER_VERSION], [f"package version {version}"])
            raise PackageError([INVALID_FILE], [f"package version {version or 'missing'}"])
        problems = [f"no {key}" for key in ("package_id", "package_name") if not isinstance(package.get(key), str)
                    or not package[key].strip()]
        for kind in KINDS:
            for record in package.get(SECTIONS[kind]) or []:
                uid = record.get(UID_KEYS[kind]) if isinstance(record, dict) else None
                if not in_protected_range(kind, uid):
                    name = record.get(NAME_KEYS[kind], "?") if isinstance(record, dict) else "?"
                    problems.append(f"The {LABELS[kind]} '{name}' has UID {uid}, outside the doctrine range; "
                                    f"packages can only carry doctrine records.")
        for role in package.get("roles") or []:
            if isinstance(role, dict) and not all(isinstance(r, dict) and is_current_requirement(r)
                                                  for r in role.get("requirements") or []):
                problems.append(f"The role '{role.get('role_name', '?')}' has a requirement without its "
                                f"system and location IDs.")
        if problems:
            raise PackageError([INVALID_FILE], problems)
        return package

    # --- planning ------------------------------------------------------------------------

    def plan_import(self, path) -> ImportPlan:
        """Work out everything the import would do. Writes nothing."""
        package = copy.deepcopy(self.read_package(path))
        package_id = package["package_id"]
        plan = ImportPlan(package=package, registry_key=package_id, package_id=package_id,
                          package_name=package["package_name"], exported_at=package.get("exported_at") or "",
                          approved=package.get("doctrine_manager_approved") is True,
                          package_has_assignments=any("character_assignments" in d for d in package.get("doctrines", [])))

        self._prepare_fittings(package, plan)
        incoming = {kind: {r[UID_KEYS[kind]] for r in package.get(SECTIONS[kind]) or []} for kind in KINDS}

        local = {"fit": {f["fit_uid"] for f in self.fittings.list_fittings()},
                 "role": set(self.roles.roles), "doctrine": set(self.doctrines.doctrines)}
        for kind in KINDS:
            for uid in sorted(incoming[kind]):
                (plan.update if uid in local[kind] else plan.add)[kind].append(uid)
                if uid in local[kind] and not self.registry.installed_by(kind, uid):
                    plan.conflicts.append(
                        f"The {LABELS[kind]} '{self._local_name(kind, uid)}' (UID {uid}) is already in your library but "
                        f"wasn't installed by a package. Delete it, or recreate it with Doctrine Mode off, then import again.")
            previous = self.registry.uids(package_id, kind)
            others = set().union(*(self.registry.uids(k, kind) for k in self.registry.entries if k != package_id))
            plan.remove[kind] = sorted(uid for uid in previous - incoming[kind] - others if uid in local[kind])
        plan.removed_names = [self._local_name(kind, uid) for kind in ("doctrine", "role", "fit") for uid in plan.remove[kind]]

        installed = self.registry.entries.get(package_id) or {}
        if installed.get("doctrine_manager_approved") and not plan.approved:
            plan.warnings.append(f"The {plan.package_name} you have is Doctrine Manager approved, but this file "
                                 f"isn't. Check where it came from before importing it.")

        self._carry_corrections(package, plan)
        self._find_removed_requirements(package, plan)
        self._check_names(package, plan)
        self._compare_fittings(package, plan)
        self._plan_assignments(package, plan)
        self._find_orphaned_local_requirements(plan)
        return plan

    def _prepare_fittings(self, package: Dict[str, Any], plan: ImportPlan) -> None:
        """Fill missing hull type IDs and drop metadata this tool doesn't understand (§11.4)."""
        for fit in package.get("fittings") or []:
            _fill_hull_type_id(fit, self.sde)
            metadata = fit.get("doctrine_metadata")
            if not metadata:
                continue
            bays = (metadata.get("bays") or {}) if isinstance(metadata, dict) else {}
            for bay_key in [k for k in bays if k not in BAYS or BAYS[k].source != "metadata"]:
                plan.warnings.append(f"Fitting '{fit.get('fit_name')}': dropped requirements for an unknown bay '{bay_key}'.")
                del bays[bay_key]
            try:
                DoctrineMetadata.from_dict(metadata).validate(fit.get("fit_uid"))
            except MetadataError as error:
                plan.warnings.append(f"Fitting '{fit.get('fit_name')}': its doctrine requirements were left out ({error}).")
                fit.pop("doctrine_metadata", None)

    def _compare_fittings(self, package: Dict[str, Any], plan: ImportPlan) -> None:
        """
        For each fitting the package updates: if the UID and hull match the local copy,
        scan it for changes and keep a name the user gave it (it was renamed here);
        if the hull differs, the local copy is replaced by the package's fitting as it is.
        Writes nothing: a kept name is set on the package record in the plan.
        """
        incoming = {f["fit_uid"]: f for f in package.get("fittings") or []}
        local_roles_by_fit: Dict[int, List[str]] = {}
        for role in self.roles.list_roles():
            if in_protected_range("role", role["role_uid"]):
                continue
            for requirement in role.get("requirements", []):
                local_roles_by_fit.setdefault(requirement.get("fit_uid"), []).append(role["role_name"])
        for uid in plan.update["fit"]:
            local, new = self.fittings.get_fitting(uid), incoming.get(uid)
            if not local or not new:
                continue
            local_name = local.get("fit_name", str(uid))
            if not _same_hull(local, new):
                plan.fits_hull_changed.append(f"{local_name} ({local.get('hull')} → {new.get('hull')})")
                plan.hull_changed_uids.append(uid)
                for role_name in sorted(set(local_roles_by_fit.get(uid, []))):
                    plan.local_requirements_on_new_hull.append(
                        f"{role_name}: {local_name} ({local.get('hull')} → {new.get('hull')})")
                continue
            if local.get("renamed") and local_name != new.get("fit_name"):
                plan.kept_names.append(f"{local_name} (the package calls it {new.get('fit_name')})")
                new["fit_name"] = local_name
                if isinstance(new.get("fit"), dict):
                    new["fit"]["fit_name"] = local_name
                new["renamed"] = True
            elif local.get("renamed"):
                new["renamed"] = True
            (plan.fits_changed if _contents(local) != _contents(new) else plan.fits_unchanged).append(local_name)

    def _carry_corrections(self, package: Dict[str, Any], plan: ImportPlan) -> None:
        """
        A pilot's replacement on a package requirement survives the update when the
        requirement (same req_uid) is still in the package and the replacement fitting
        is still in the library afterwards; otherwise the original applies again and
        it's reported. If the fitting manager changed the requirement's fitting, the
        replacement is kept and reported (decision 11-B); if they changed it to the
        replacement itself, the replacement isn't needed any more. Requirements the
        pilot kept are carried over while their fittings stay. Writes nothing: the
        package's roles in the plan are changed.
        """
        incoming_fits = {f["fit_uid"]: f for f in package.get("fittings") or []}

        def survives(fit_uid):
            return self._survives(package, plan, fit_uid)

        def name(fit_uid):
            record = incoming_fits.get(fit_uid) or self.fittings.get_fitting(fit_uid) or {}
            return record.get("fit_name") or f"fitting {fit_uid}"

        for role in package.get("roles") or []:
            installed = self.roles.get_role(role["role_uid"])
            if not installed:
                continue
            role_name = role.get("role_name", role["role_uid"])
            incoming = {r.get("req_uid"): r for r in role.get("requirements") or []}
            for old in installed.get("requirements", []):
                new = incoming.get(old.get("req_uid"))
                replacement = old.get("replacement") if isinstance(old.get("replacement"), dict) else None
                if new is None:
                    if old.get("kept") and survives(old.get("fit_uid")) and survives(fitting_in_use(old)):
                        role.setdefault("requirements", []).append(copy.deepcopy(old))
                        plan.corrections_kept.append(f"{role_name}: {name(fitting_in_use(old))} (kept)")
                    continue
                if replacement is None:
                    continue
                fit_uid = replacement.get("fit_uid")
                if not survives(fit_uid):
                    plan.corrections_dropped.append(f"{role_name}: {name(fit_uid)} is no longer in the package")
                elif new.get("fit_uid") == fit_uid:
                    plan.corrections_dropped.append(f"{role_name}: the package now asks for {name(fit_uid)}, "
                                                    f"so your replacement isn't needed")
                elif new.get("fit_uid") != old.get("fit_uid"):
                    new["replacement"] = dict(replacement)
                    plan.corrections_changed.append(f"{role_name}: the package now asks for {name(new.get('fit_uid'))}; "
                                                    f"your {name(fit_uid)} replacement was kept")
                else:
                    new["replacement"] = dict(replacement)
                    plan.corrections_kept.append(f"{role_name}: {name(fit_uid)} (replacing {name(new.get('fit_uid'))})")

    def _survives(self, package: Dict[str, Any], plan: ImportPlan, fit_uid) -> bool:
        """Whether a fitting is in the library after the import: in the package, or installed and staying."""
        return (any(f["fit_uid"] == fit_uid for f in package.get("fittings") or [])
                or (self.fittings.get_fitting(fit_uid) is not None and fit_uid not in plan.remove["fit"]))

    def _place(self, requirement: Dict[str, Any]) -> str:
        if self.sde is None:
            return "?"
        if requirement.get("location_id"):
            return self.sde.location_label(requirement["location_id"], requirement.get("location_name"))
        if requirement.get("system_id"):
            return self.sde.get_system_name(requirement["system_id"])
        return "Anywhere"

    def _find_removed_requirements(self, package: Dict[str, Any], plan: ImportPlan) -> None:
        """
        Installed requirements the update would remove from package roles (step 11.4):
        from a role that stays, every one not carried over (it can be kept, unless
        retired); from a role the package drops, only retired ones (to move the fitting).
        Kept ones that 11.3 carries over aren't asked about again. A pilot's own added
        requirements (not retired, in a role that stays) are listed apart, in
        added_requirements: the pilot is asked about those first.
        """
        incoming_roles = {r["role_uid"]: r for r in package.get("roles") or []}
        for role_uid in plan.update["role"] + plan.remove["role"]:
            installed = self.roles.get_role(role_uid) or {}
            incoming = incoming_roles.get(role_uid)
            carried = {r.get("req_uid") for r in (incoming or {}).get("requirements") or []}
            for requirement in installed.get("requirements", []):
                if requirement.get("req_uid") in carried:
                    continue
                in_use, own = fitting_in_use(requirement), requirement.get("fit_uid")
                leaving = [uid for uid in dict.fromkeys((in_use, own))
                           if not self._survives(package, plan, uid) and self.fittings.get_fitting(uid)]
                if incoming is None and not leaving:
                    continue
                replacing = self._local_name("fit", own) if in_use != own else None
                pilots_own = requirement.get("added") and not leaving and incoming is not None
                (plan.added_requirements if pilots_own else plan.removed_requirements).append(RemovedRequirement(
                    role_uid=role_uid, req_uid=requirement.get("req_uid"),
                    role_name=installed.get("role_name", str(role_uid)), fit_name=self._local_name("fit", in_use),
                    place=self._place(requirement), replacing=replacing, retired=bool(leaving),
                    role_stays=incoming is not None, retired_fit_uids=leaving))

    def _check_names(self, package: Dict[str, Any], plan: ImportPlan) -> None:
        """Role and doctrine names must stay unique once the import is done."""
        for kind, manager_records in (("role", self.roles.roles), ("doctrine", self.doctrines.doctrines)):
            incoming = package.get(SECTIONS[kind]) or []
            incoming_uids = {r[UID_KEYS[kind]] for r in incoming}
            staying = {uid: rec[NAME_KEYS[kind]] for uid, rec in manager_records.items()
                       if uid not in incoming_uids and uid not in plan.remove[kind]}
            seen: Dict[str, int] = {}
            for record in incoming:
                name = str(record.get(NAME_KEYS[kind], "")).strip()
                folded = name.casefold()
                clash = next((uid for uid, other in staying.items() if other.strip().casefold() == folded), None)
                if clash is not None:
                    plan.conflicts.append(f"The package's {LABELS[kind]} '{name}' has the same name as your {LABELS[kind]} "
                                          f"'{staying[clash]}' (UID {clash}). Rename yours, then import again.")
                if folded in seen:
                    plan.conflicts.append(f"The package contains two {LABELS[kind]}s named '{name}'.")
                seen[folded] = record[UID_KEYS[kind]]

    def _plan_assignments(self, package: Dict[str, Any], plan: ImportPlan) -> None:
        """Assignments are the recipient's own: keep each one whose doctrine and role survive (§11.5)."""
        incoming = {d["doctrine_uid"]: d for d in package.get("doctrines") or []}
        for uid in plan.update["doctrine"] + plan.remove["doctrine"]:
            local = self.doctrines.get_doctrine(uid) or {}
            new = incoming.get(uid)
            surviving_roles = {str(r) for r in new.get("roles", [])} if new else set()
            kept: Dict[str, List[str]] = {}
            for role_key, characters in (local.get("character_assignments") or {}).items():
                if role_key in surviving_roles:
                    kept[role_key] = list(characters)
                else:
                    role = self.roles.get_role(int(role_key)) if str(role_key).isdigit() else None
                    role_name = role["role_name"] if role else f"role {role_key}"
                    for character in characters:
                        plan.dropped_assignments.append(f"{character} in {role_name} ({local.get('doctrine_name', uid)})")
            if new is not None:
                plan.kept_assignments[uid] = kept

    def _find_orphaned_local_requirements(self, plan: ImportPlan) -> None:
        removed = set(plan.remove["fit"])
        for role in self.roles.list_roles():
            if in_protected_range("role", role["role_uid"]):
                continue
            for requirement in role.get("requirements", []):
                if requirement.get("fit_uid") in removed:
                    plan.orphaned_local_requirements.append(
                        f"{role['role_name']}: {self._local_name('fit', requirement['fit_uid'])}")

    def _local_name(self, kind: str, uid: int) -> str:
        record = {"fit": self.fittings.get_fitting, "role": self.roles.get_role, "doctrine": self.doctrines.get_doctrine}[kind](uid)
        return record.get(NAME_KEYS[kind], str(uid)) if record else str(uid)

    # --- applying ------------------------------------------------------------------------

    def apply_import(self, plan: ImportPlan, use_package_assignments: bool = False) -> Dict[str, Any]:
        """
        Back up the library and registry, apply the plan, and record the package.
        If anything fails, the backups are restored and ImportFailed is raised.
        """
        if plan.conflicts:
            raise ImportFailed("The import has conflicts and can't be applied: " + "; ".join(plan.conflicts))
        files = [self.fittings.fittings_path, self.roles.roles_path, self.doctrines.doctrines_path, self.registry.path]
        backup_dir = Path(tempfile.mkdtemp(prefix="package_import_"))
        backups = {}
        try:
            for index, path in enumerate(files):
                if Path(path).exists():
                    backups[path] = backup_dir / f"{index}_{Path(path).name}"
                    shutil.copy2(path, backups[path])
                else:
                    backups[path] = None
            try:
                self._apply(plan, use_package_assignments)
            except Exception as error:
                for path, saved in backups.items():
                    if saved is not None:
                        shutil.copy2(saved, path)
                    elif Path(path).exists():
                        Path(path).unlink()
                self._reload()
                raise ImportFailed(f"The import failed and your library was restored: {error}") from error
        finally:
            shutil.rmtree(backup_dir, ignore_errors=True)
        added, updated, removed = plan.counts()
        return {"package_name": plan.package_name, "added": added, "updated": updated, "removed": removed,
                "dropped_assignments": list(plan.dropped_assignments), "warnings": list(plan.warnings),
                "fits_hull_changed": list(plan.fits_hull_changed), "kept_names": list(plan.kept_names),
                "local_requirements_on_new_hull": list(plan.local_requirements_on_new_hull),
                "orphaned_local_requirements": list(plan.orphaned_local_requirements),
                "corrections_kept": list(plan.corrections_kept), "corrections_changed": list(plan.corrections_changed),
                "corrections_dropped": list(plan.corrections_dropped),
                "requirements_kept": plan.kept_lines(), "moved_to_personal": plan.moved_lines(),
                "summary": plan.summary()}

    def _apply(self, plan: ImportPlan, use_package_assignments: bool) -> None:
        package = plan.package
        rows = {r.key: r for r in plan.removed_requirements}
        # Move to personal (decision 11-C): copy each chosen retired fitting, whole, into the
        # pilot's own range before the package's copy is removed. The copy is in no role.
        moving = dict.fromkeys(uid for key in plan.move_to_personal for uid in rows[key].retired_fit_uids)
        for fit_uid in moving:
            original = self.fittings.get_fitting(fit_uid)
            if original:
                record = copy.deepcopy(original)
                for key in ("fit_uid", "renamed", "source"):
                    record.pop(key, None)
                self.fittings.create_fitting(record, source="local")
        # Requirements the pilot keeps stay in their role, marked kept, through later updates too.
        kept = {}
        for key in plan.keep + plan.keep_added:
            installed = self.roles.get_role(key[0]) or {}
            requirement = next((r for r in installed.get("requirements", []) if r.get("req_uid") == key[1]), None)
            if requirement is not None:
                kept.setdefault(key[0], []).append(dict(copy.deepcopy(requirement), kept=True))
        # Remove what the package no longer contains, and clear records being replaced, so
        # names swapped between records in the same package never clash on the way in.
        for uid in plan.remove["doctrine"] + plan.update["doctrine"]:
            self.doctrines.delete_doctrine(uid)
        for uid in plan.remove["role"] + plan.update["role"]:
            self.roles.delete_role(uid)
        for uid in plan.remove["fit"]:
            self.fittings.delete_fitting(uid)

        for fit in package.get("fittings") or []:
            self.fittings.inject_fitting(fit)
        if self.sde is not None and hasattr(self.fittings, "migrate"):
            self.fittings.migrate(self.sde)      # fittings from older packages may list fighters as cargo
        for role in package.get("roles") or []:
            record = dict(role)
            if record["role_uid"] in kept:
                requirements = list(record.get("requirements") or [])
                for requirement in kept[record["role_uid"]]:
                    place = (fitting_in_use(requirement), requirement.get("system_id"), requirement.get("location_id"))
                    if all((fitting_in_use(r), r.get("system_id"), r.get("location_id")) != place for r in requirements):
                        requirements.append(requirement)    # unless the package now has it under another number
                record["requirements"] = requirements
            self.roles.inject_role(record)
        if plan.hull_changed_uids:
            # The user's own roles drop a fitting whose hull changed (user decision, 2026-10-04).
            local_roles = {uid for uid in self.roles.roles if not in_protected_range("role", uid)}
            self.roles.remove_requirements_for_fittings(plan.hull_changed_uids, role_uids=local_roles)
        for doctrine in package.get("doctrines") or []:
            record = dict(doctrine)
            uid = record["doctrine_uid"]
            if not (use_package_assignments and "character_assignments" in doctrine):
                record["character_assignments"] = plan.kept_assignments.get(uid, {})
            self.doctrines.inject_doctrine(record)

        incoming = {kind: [r[UID_KEYS[kind]] for r in package.get(SECTIONS[kind]) or []] for kind in KINDS}
        self.registry.record(plan.registry_key, plan.package_id, plan.package_name, plan.exported_at, incoming,
                             approved=plan.approved)
        self.registry.save()

    def _reload(self) -> None:
        self.fittings.load_fittings()
        self.roles.load_roles()
        self.doctrines.load_doctrines()
        self.registry.load()


def _same_hull(local: Dict[str, Any], new: Dict[str, Any]) -> bool:
    """Compared by hull type ID when both have one, else by hull name."""
    if local.get("hull_type_id") and new.get("hull_type_id"):
        return int(local["hull_type_id"]) == int(new["hull_type_id"])
    return str(local.get("hull", "")).strip().casefold() == str(new.get("hull", "")).strip().casefold()


def _contents(record: Dict[str, Any]) -> Tuple[Any, Any]:
    """What a fitting holds, without its name: the parsed fit and its doctrine metadata."""
    fit = dict(record.get("fit") or {})
    fit.pop("fit_name", None)
    return json.dumps(fit, sort_keys=True), json.dumps(record.get("doctrine_metadata") or None, sort_keys=True)


def _version_tuple(version: str) -> Tuple[int, ...]:
    try:
        return tuple(int(part) for part in version.split("."))
    except ValueError:
        return (0,)


def _fill_hull_type_id(fit: Dict[str, Any], sde_loader: Optional[Any]) -> None:
    """
    The audit identifies a fitting's hull by its top-level hull_type_id, which
    packages exported before step 0.6 left out. Take it from the parsed fit,
    or else look the hull name up in the SDE.
    """
    if fit.get("hull_type_id"):
        return
    nested = fit.get("fit") if isinstance(fit.get("fit"), dict) else {}
    hull_type_id = nested.get("hull_type_id")
    if not hull_type_id and sde_loader is not None and fit.get("hull"):
        hull_type_id = sde_loader.get_typeid_by_name(fit["hull"])
    if hull_type_id:
        fit["hull_type_id"] = hull_type_id
