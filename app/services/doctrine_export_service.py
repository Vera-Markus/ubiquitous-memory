"""
Doctrine package export (design §11.3, decision D15).

A package holds exactly the doctrines, roles and fittings chosen for it, so a
public and a classified package can come from one master library. Only
records in the protected ranges are exported, character assignments only
when asked for, and a package never refers to a record it doesn't contain.

    selection = service.default_selection({5000})        # a doctrine, its roles, their fits and escape fits
    problems = service.check_selection(selection)        # [] when it can be exported
    service.export_package("SNUFF Public", selection, path)
"""
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from app.loaders.package_registry import in_protected_range, slugify
from app.models.bay_registry import ESCAPE_BAY

PACKAGE_TYPE = "shared_doctrine"
PACKAGE_VERSION = "2.0"      # 2.0: requirements carry system and location IDs (SDE plan step 10.3)


class ExportError(ValueError):
    def __init__(self, problems: List[str]):
        self.problems = list(problems)
        super().__init__("; ".join(self.problems))


@dataclass
class ExportSelection:
    doctrine_uids: Set[int] = field(default_factory=set)
    role_uids: Set[int] = field(default_factory=set)
    fit_uids: Set[int] = field(default_factory=set)
    include_assignments: bool = False
    doctrine_manager: bool = False      # marks the package Doctrine Manager approved

    def to_dict(self) -> Dict[str, Any]:
        return {"doctrine_uids": sorted(self.doctrine_uids), "role_uids": sorted(self.role_uids),
                "fit_uids": sorted(self.fit_uids), "include_assignments": self.include_assignments,
                "doctrine_manager": self.doctrine_manager}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ExportSelection":
        return cls(set(data.get("doctrine_uids", [])), set(data.get("role_uids", [])),
                   set(data.get("fit_uids", [])), bool(data.get("include_assignments", False)),
                   bool(data.get("doctrine_manager", False)))


@dataclass
class TreeNode:
    kind: str                   # "doctrine", "role", "fit", or "group" for a heading
    uid: Optional[int]
    label: str
    children: List["TreeNode"] = field(default_factory=list)


# A pilot's own tweaks to a requirement (doctrine tweaks plan): a package is always the
# baseline, so these never travel (decision 11-A).
PILOT_ONLY = ("replacement", "kept", "added")

# The tick tree's heading for doctrine fittings no role uses; ticking it ticks them all (step 11.5).
LOOSE_FITS = "Fittings not used by a role"


def escape_fit_uids(fitting: Dict[str, Any]) -> List[int]:
    """Fittings this one recommends as its escape ship."""
    bays = (fitting.get("doctrine_metadata") or {}).get("bays") or {}
    return [r["fit_uid"] for r in bays.get(ESCAPE_BAY.key) or []
            if isinstance(r, dict) and r.get("match") == "fit" and r.get("fit_uid") is not None]


def carried_fit_uids(fitting: Dict[str, Any]) -> List[int]:
    """Fittings this one's Ship Maintenance Bay names (UI thoughts plan 20.2)."""
    bays = (fitting.get("doctrine_metadata") or {}).get("bays") or {}
    return [r["fit_uid"] for r in bays.get("ship_maintenance_bay") or []
            if isinstance(r, dict) and r.get("match") == "fit" and r.get("fit_uid") is not None]


def linked_fit_uids(fitting: Dict[str, Any]) -> List[int]:
    """Every fitting this one names: its escape ship and the ships it carries."""
    return list(dict.fromkeys(escape_fit_uids(fitting) + carried_fit_uids(fitting)))


class DoctrineExportService:
    def __init__(self, fitting_manager: Any, role_manager: Any, doctrine_manager: Any):
        self.fittings = fitting_manager
        self.roles = role_manager
        self.doctrines = doctrine_manager

    # --- what a tick brings with it ------------------------------------------------------

    def roles_of(self, doctrine_uid: int) -> List[int]:
        doctrine = self.doctrines.get_doctrine(doctrine_uid) or {}
        return list(doctrine.get("roles", []))

    def fits_of(self, role_uid: int) -> List[int]:
        """The fittings a role's exported requirements use (a pilot's added ones aren't exported)."""
        role = self.roles.get_role(role_uid) or {}
        return list(dict.fromkeys(r["fit_uid"] for r in role.get("requirements", [])
                                  if r.get("fit_uid") is not None and not r.get("added")))

    def fits_with_escape_fits(self, fit_uids) -> Set[int]:
        found, todo = set(), list(fit_uids)
        while todo:
            uid = todo.pop()
            if uid in found:
                continue
            found.add(uid)
            fitting = self.fittings.get_fitting(uid)
            if fitting:
                todo.extend(linked_fit_uids(fitting))
        return found

    def default_selection(self, doctrine_uids, include_assignments: bool = False) -> ExportSelection:
        """Ticking a doctrine ticks its roles, the fittings they use and the escape fittings those recommend."""
        selection = ExportSelection(set(doctrine_uids), include_assignments=include_assignments)
        for doctrine_uid in doctrine_uids:
            selection.role_uids.update(self.roles_of(doctrine_uid))
        for role_uid in selection.role_uids:
            selection.fit_uids.update(self.fits_of(role_uid))
        selection.fit_uids = self.fits_with_escape_fits(selection.fit_uids)
        return selection

    # --- the tick tree (design §11.3) ----------------------------------------------------

    def tree(self) -> List["TreeNode"]:
        """
        Doctrine -> roles -> fittings -> escape fittings, for every doctrine in the
        protected range, then the doctrine roles and fittings no doctrine reaches.
        """
        nodes: List[TreeNode] = []
        reached_roles, reached_fits = set(), set()

        def fit_node(uid, link=""):
            fitting = self.fittings.get_fitting(uid)
            label = fitting["fit_name"] if fitting else f"Missing fitting {uid}"
            reached_fits.add(uid)
            escapes = escape_fit_uids(fitting or {})
            children = [fit_node(e, " (escape fit)" if e in escapes else " (carried fit)")
                        for e in linked_fit_uids(fitting or {}) if e != uid and e not in reached_fits]
            return TreeNode("fit", uid, label + link, children)

        def role_node(uid):
            role = self.roles.get_role(uid)
            reached_roles.add(uid)
            label = role["role_name"] if role else f"Missing role {uid}"
            if not in_protected_range("role", uid):
                label += " (local: can't be exported)"
            return TreeNode("role", uid, label, [fit_node(f) for f in self.fits_of(uid)])

        doctrines = sorted((d for d in self.doctrines.list_doctrines() if in_protected_range("doctrine", d["doctrine_uid"])),
                           key=lambda d: d["doctrine_name"].casefold())
        for d in doctrines:
            nodes.append(TreeNode("doctrine", d["doctrine_uid"], d["doctrine_name"],
                                  [role_node(r) for r in d.get("roles", [])]))
        loose_roles = sorted((r for r in self.roles.list_roles()
                              if in_protected_range("role", r["role_uid"]) and r["role_uid"] not in reached_roles),
                             key=lambda r: r["role_name"].casefold())
        if loose_roles:
            nodes.append(TreeNode("group", None, "Roles not in a doctrine", [role_node(r["role_uid"]) for r in loose_roles]))
        loose_fits = sorted((f for f in self.fittings.list_fittings()
                             if in_protected_range("fit", f["fit_uid"]) and f["fit_uid"] not in reached_fits),
                            key=lambda f: f["fit_name"].casefold())
        if loose_fits:
            nodes.append(TreeNode("group", None, LOOSE_FITS, [fit_node(f["fit_uid"]) for f in loose_fits]))
        return nodes

    def tick(self, selection: ExportSelection, kind: str, uid: int) -> None:
        """Ticking brings along what the item needs: a doctrine its roles, a role its fits, a fit its escape fits."""
        if kind == "doctrine":
            closure = self.default_selection({uid})
            selection.doctrine_uids |= closure.doctrine_uids
            selection.role_uids |= closure.role_uids
            selection.fit_uids |= closure.fit_uids
        elif kind == "role":
            selection.role_uids.add(uid)
            selection.fit_uids |= self.fits_with_escape_fits(self.fits_of(uid))
        elif kind == "fit":
            selection.fit_uids |= self.fits_with_escape_fits([uid])

    def loose_fit_uids(self) -> List[int]:
        """The fittings listed under LOOSE_FITS (doctrine fittings no role uses), in the tree's order."""
        group = next((n for n in self.tree() if n.kind == "group" and n.label == LOOSE_FITS), None)
        return [child.uid for child in group.children] if group else []

    def loose_fits_ticked(self, selection: ExportSelection) -> bool:
        uids = self.loose_fit_uids()
        return bool(uids) and all(uid in selection.fit_uids for uid in uids)

    def tick_loose_fits(self, selection: ExportSelection, ticked: bool) -> None:
        """All on (each with its escape fits) or all off, for the LOOSE_FITS heading (step 11.5)."""
        for uid in self.loose_fit_uids():
            if ticked:
                self.tick(selection, "fit", uid)
            else:
                self.untick(selection, "fit", uid)

    def untick(self, selection: ExportSelection, kind: str, uid: int) -> None:
        """
        Unticking a doctrine or role also unticks what it brought along, unless
        something still ticked needs it. Unticking a fitting unticks only that
        fitting, so check_selection can say who still needs it.
        """
        if kind == "fit":
            selection.fit_uids.discard(uid)
            return
        if kind == "doctrine":
            closure = self.default_selection({uid})
            selection.doctrine_uids.discard(uid)
            still_needed = {r for d in selection.doctrine_uids for r in self.roles_of(d)}
            selection.role_uids -= closure.role_uids - still_needed
        else:
            closure = ExportSelection(role_uids={uid}, fit_uids=self.fits_with_escape_fits(self.fits_of(uid)))
            selection.role_uids.discard(uid)
        needed = self.fits_with_escape_fits([f for r in selection.role_uids for f in self.fits_of(r)])
        needed |= self.fits_with_escape_fits(selection.fit_uids - closure.fit_uids)
        selection.fit_uids -= closure.fit_uids - needed

    def summary(self, selection: ExportSelection) -> str:
        """"1 doctrine · 2 roles · 4 fittings (1 escape fit)" """
        escape = {e for f in selection.fit_uids for e in escape_fit_uids(self.fittings.get_fitting(f) or {})} & selection.fit_uids
        plural = lambda n, word: f"{n} {word}" + ("" if n == 1 else "s")
        text = (f"{plural(len(selection.doctrine_uids), 'doctrine')} · {plural(len(selection.role_uids), 'role')} · "
                f"{plural(len(selection.fit_uids), 'fitting')}")
        if escape:
            text += f" ({plural(len(escape), 'escape fit')})"
        left_out = sum(1 for f in self.fittings.list_fittings()
                       if in_protected_range("fit", f.get("fit_uid")) and f["fit_uid"] not in selection.fit_uids)
        if left_out:
            # So a new fitting isn't forgotten (step 11.5)
            text += (f" · {left_out} doctrine fitting{'s' if left_out != 1 else ''} "
                     f"{'aren' if left_out != 1 else 'isn'}'t in this package")
        return text

    # --- checks --------------------------------------------------------------------------

    def _name(self, kind: str, uid: int) -> str:
        if kind == "doctrine":
            record = self.doctrines.get_doctrine(uid)
            return f"doctrine '{record['doctrine_name']}'" if record else f"doctrine {uid}"
        if kind == "role":
            record = self.roles.get_role(uid)
            return f"role '{record['role_name']}'" if record else f"role {uid}"
        record = self.fittings.get_fitting(uid)
        return f"fitting '{record['fit_name']}'" if record else f"fitting {uid}"

    def check_selection(self, selection: ExportSelection) -> List[str]:
        """
        Everything that stops this selection from being exported: records that
        don't exist or are local, and references to records left out. Empty when it's fine.
        """
        problems: List[str] = []
        lookups = {"doctrine": self.doctrines.get_doctrine, "role": self.roles.get_role, "fit": self.fittings.get_fitting}
        chosen = {"doctrine": selection.doctrine_uids, "role": selection.role_uids, "fit": selection.fit_uids}
        if not any(chosen.values()):
            return ["Nothing is selected."]
        for kind, uids in chosen.items():
            for uid in sorted(uids):
                if lookups[kind](uid) is None:
                    problems.append(f"{kind.capitalize()} UID {uid} doesn't exist.")
                elif not in_protected_range(kind, uid):
                    problems.append(f"The {self._name(kind, uid)} is a local record (UID {uid}); only doctrine records can be exported.")
        for doctrine_uid in sorted(selection.doctrine_uids):
            for role_uid in self.roles_of(doctrine_uid):
                if role_uid not in selection.role_uids:
                    problems.append(f"The {self._name('doctrine', doctrine_uid)} uses the {self._name('role', role_uid)}, which isn't selected.")
        for role_uid in sorted(selection.role_uids):
            for fit_uid in self.fits_of(role_uid):
                if fit_uid not in selection.fit_uids:
                    problems.append(f"The {self._name('role', role_uid)} uses the {self._name('fit', fit_uid)}, which isn't selected.")
        for fit_uid in sorted(selection.fit_uids):
            fitting = self.fittings.get_fitting(fit_uid)
            for escape_uid in escape_fit_uids(fitting or {}):
                if escape_uid not in selection.fit_uids:
                    problems.append(f"The {self._name('fit', fit_uid)} recommends the {self._name('fit', escape_uid)} "
                                    f"as its escape ship, which isn't selected.")
            for carried_uid in carried_fit_uids(fitting or {}):
                if carried_uid not in selection.fit_uids and carried_uid not in escape_fit_uids(fitting or {}):
                    problems.append(f"The {self._name('fit', fit_uid)} carries the {self._name('fit', carried_uid)} "
                                    f"in its Ship Maintenance Bay, which isn't selected.")
        return problems

    # --- the package ---------------------------------------------------------------------

    def build_package(self, package_name: str, selection: ExportSelection) -> Dict[str, Any]:
        problems = self.check_selection(selection)
        if not package_name.strip():
            problems.insert(0, "The package needs a name.")
        if problems:
            raise ExportError(problems)
        doctrines = []
        for uid in sorted(selection.doctrine_uids):
            d = self.doctrines.get_doctrine(uid)
            record = {"doctrine_uid": uid, "doctrine_name": d["doctrine_name"], "roles": list(d.get("roles", []))}
            if selection.include_assignments:
                record["character_assignments"] = d.get("character_assignments", {})
            doctrines.append(record)
        roles = []
        for uid in sorted(selection.role_uids):
            r = self.roles.get_role(uid)
            roles.append({"role_uid": uid, "role_name": r["role_name"], "requirements": [
                {k: v for k, v in req.items() if k not in PILOT_ONLY} for req in r.get("requirements", [])
                if not req.get("added")]})
        fittings = []
        for uid in sorted(selection.fit_uids):
            f = self.fittings.get_fitting(uid)
            record = {key: f.get(key) for key in ("fit_uid", "source", "hull", "hull_type_id", "fit_name", "fit")}
            record["schema_version"] = f.get("schema_version", 1)
            if f.get("doctrine_metadata"):
                record["doctrine_metadata"] = f["doctrine_metadata"]
            fittings.append(record)
        return {
            "package_type": PACKAGE_TYPE,
            "package_version": PACKAGE_VERSION,
            "package_id": slugify(package_name),
            "package_name": package_name.strip(),
            "exported_at": datetime.now().isoformat(timespec="seconds"),
            "includes_assignments": selection.include_assignments,
            "doctrine_manager_approved": selection.doctrine_manager,
            "doctrines": doctrines,
            "roles": roles,
            "fittings": fittings,
        }

    def export_package(self, package_name: str, selection: ExportSelection, output_path) -> Dict[str, Any]:
        """Write the package. Raises ExportError (listing every problem) when the selection can't be exported."""
        package = self.build_package(package_name, selection)
        with open(output_path, "w", encoding="utf-8") as file:
            json.dump(package, file, indent=4)
        return {
            "path": str(output_path),
            "package_id": package["package_id"],
            "doctrine_manager_approved": package["doctrine_manager_approved"],
            "doctrine_count": len(package["doctrines"]),
            "role_count": len(package["roles"]),
            "requirement_count": sum(len(r["requirements"]) for r in package["roles"]),
            "fitting_count": len(package["fittings"]),
            "escape_fit_count": len({uid for f in package["fittings"] for uid in escape_fit_uids(f)}),
        }


class ExportProfiles:
    """
    Package names this machine has exported, with each one's last selection
    (data/config/export_profiles.json). Publishing an update means picking the
    same name, so recipients' copies are replaced rather than duplicated.
    """

    def __init__(self, path):
        self.path = Path(path)
        self.profiles: Dict[str, Dict[str, Any]] = {}
        if self.path.exists():
            with self.path.open("r", encoding="utf-8") as file:
                data = json.load(file)
            self.profiles = data if isinstance(data, dict) else {}

    def names(self) -> List[str]:
        return sorted((p["package_name"] for p in self.profiles.values()), key=str.casefold)

    def selection_for(self, package_name: str) -> Optional[ExportSelection]:
        profile = self.profiles.get(slugify(package_name))
        return ExportSelection.from_dict(profile["selection"]) if profile else None

    def remember(self, package_name: str, selection: ExportSelection) -> None:
        self.profiles[slugify(package_name)] = {
            "package_name": package_name.strip(),
            "last_exported_at": datetime.now().isoformat(timespec="seconds"),
            "selection": selection.to_dict(),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as file:
            json.dump(self.profiles, file, indent=2)
