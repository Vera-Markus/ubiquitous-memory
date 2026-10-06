import threading
import tkinter.filedialog as filedialog
from tkinter import messagebox
from typing import Any, Dict, Optional

from app import paths
from app.gui.dialogs.export_package_dialog import ExportPackageDialog
from app.gui.dialogs.import_confirm_dialog import ImportConfirmDialog
from app.gui.dialogs.removed_requirements_dialog import RemovedRequirementsDialog
from app.services.doctrine_export_service import DoctrineExportService, ExportError, ExportProfiles, ExportSelection
from app.services.doctrine_import_service import DoctrineImportService, ImportPlan, PackageError


class PackageActions:
    """
    Doctrine package export and import, started from the Library tab's buttons
    (and Clear Library's export offer).

    Moved from the Export tab in UI rework step 7.1, which closed that tab. The
    handlers are the tab's, with its status line replaced by `busy` and the
    separate Select Package step folded into Import: Import asks for the file,
    plans (writing nothing), then shows the plan for confirmation (design §11.3-11.4).
    """

    def __init__(self, app):
        self.app = app
        self.export_dialog = None
        self.confirm_dialog = None
        self.removed_dialog = None
        self.added_dialog = None
        self.busy: Optional[str] = None       # "Exporting" or "Importing" while a worker runs

    # --- Shared state owned by EVEFleetGUI ---------------------------------

    @property
    def root(self):
        return self.app.root

    @property
    def fitting_manager(self):
        return self.app.fitting_manager

    @property
    def role_manager(self):
        return self.app.role_manager

    @property
    def doctrine_manager(self):
        return self.app.doctrine_manager

    @property
    def evedb_loader(self):
        return self.app.evedb_loader

    @property
    def package_registry(self):
        return self.app.package_registry

    def _log(self, message: str):
        self.app._log(message)

    def _export_service(self) -> DoctrineExportService:
        return DoctrineExportService(self.fitting_manager, self.role_manager, self.doctrine_manager)

    def _import_service(self) -> DoctrineImportService:
        return DoctrineImportService(self.fitting_manager, self.role_manager, self.doctrine_manager,
                                     self.package_registry, self.evedb_loader)

    @staticmethod
    def _export_profiles() -> ExportProfiles:
        return ExportProfiles(paths.CONFIG_DIR / "export_profiles.json")

    def _refresh_after_import(self):
        self.app._refresh_after_library_change()

    # --- Export ------------------------------------------------------------------

    def _handle_export_package(self):
        """Opens the package dialog: choose a name and what goes in the package (design §11.3)."""
        self.export_dialog = ExportPackageDialog(self.app, self._export_service(), self._export_profiles(),
                                                 on_export=self._start_export)

    def _start_export(self, package_name: str, selection: ExportSelection, file_path: str):
        self._log(f"[INFO] Exporting doctrine package {package_name}...")
        self.busy = "Exporting"
        threading.Thread(target=self._execute_export, args=(package_name, selection, file_path), daemon=True).start()

    def _execute_export(self, package_name: str, selection: ExportSelection, file_path: str):
        try:
            result = self._export_service().export_package(package_name, selection, file_path)
            self._export_profiles().remember(package_name, selection)
            self.root.after(0, lambda: self._on_export_complete(package_name, result))
        except ExportError as e:
            self.root.after(0, lambda e=e: self._on_export_failed("\n".join(e.problems)))
        except Exception as e:
            self.root.after(0, lambda e=e: self._on_export_failed(str(e)))

    def _on_export_complete(self, package_name: str, result: Dict[str, Any]):
        self.busy = None
        path = result["path"]
        self._log(f"[SUCCESS] Doctrine package {package_name} exported to: {path}")
        escape = f" ({result['escape_fit_count']} escape fit)" if result.get("escape_fit_count") else ""
        approved = " as Doctrine Manager approved" if result.get("doctrine_manager_approved") else ""
        messagebox.showinfo("Export Success",
                            f"{package_name} exported{approved}.\n\n"
                            f"Doctrines: {result['doctrine_count']}\nRoles: {result['role_count']}\n"
                            f"Fittings: {result['fitting_count']}{escape}\n\nSaved to: {path}")

    def _on_export_failed(self, error: str):
        self.busy = None
        self._log(f"[ERROR] Export failed: {error}")
        messagebox.showerror("Export Error", f"Failed to export package:\n\n{error}")

    # --- Import ------------------------------------------------------------------

    def _handle_import_package(self):
        """
        Asks for the package file, plans the import (nothing is written), then shows the
        plan for confirmation (design §11.4).
        """
        path = filedialog.askopenfilename(filetypes=[("JSON files", "*.json")], title="Import Doctrine Package")
        if not path:
            return
        self._log(f"[INFO] Package selected: {path}")
        service = self._import_service()
        try:
            plan = service.plan_import(path)
        except PackageError as e:
            if e.details:
                self._log(f"[WARNING] Not imported ({path}): {'; '.join(e.details)}")
            self._on_import_failed("\n".join(e.problems))
            return
        self._log(f"[INFO] Import plan: {plan.summary()}")
        if plan.added_requirements and plan.ok:
            # The pilot's own added requirements: keep them? Yes: choose which. No: they go.
            count = len(plan.added_requirements)
            if messagebox.askyesno(
                    "Your Added Requirements",
                    f"You added {count} requirement{'s' if count != 1 else ''} to roles in {plan.package_name}, "
                    f"and this update would remove {'them' if count != 1 else 'it'}.\n\n"
                    f"Do you want to keep {'them' if count != 1 else 'it'}?"):
                self.added_dialog = RemovedRequirementsDialog(
                    self.app, plan, added=True,
                    on_continue=lambda keep, move: self._after_added(service, plan, keep))
                return
        self._after_added(service, plan, [])

    def _after_added(self, service: DoctrineImportService, plan: ImportPlan, keep_added):
        plan.choose_added(keep_added)
        if plan.removed_requirements and plan.ok:
            # Step 11.4: the pilot chooses what to keep (or move to personal) before confirming.
            self.removed_dialog = RemovedRequirementsDialog(
                self.app, plan, on_continue=lambda keep, move: self._confirm_import(service, plan, keep, move))
            return
        self._confirm_import(service, plan)

    def _confirm_import(self, service: DoctrineImportService, plan: ImportPlan, keep=(), move_to_personal=()):
        plan.choose_removals(keep, move_to_personal)
        self.confirm_dialog = ImportConfirmDialog(self.app, plan, on_confirm=lambda use: self._start_import(service, plan, use))

    def _start_import(self, service: DoctrineImportService, plan: ImportPlan, use_package_assignments: bool):
        self._log(f"[INFO] Importing doctrine package {plan.package_name}...")
        self.busy = "Importing"
        threading.Thread(target=self._execute_package_import, args=(service, plan, use_package_assignments),
                         daemon=True).start()

    def _execute_package_import(self, service: DoctrineImportService, plan: ImportPlan, use_package_assignments: bool):
        try:
            result = service.apply_import(plan, use_package_assignments=use_package_assignments)
            self.root.after(0, lambda: self._on_import_complete(result))
        except Exception as e:
            self.root.after(0, lambda e=e: self._on_import_failed(str(e)))

    def _on_import_complete(self, result: Dict[str, Any]):
        self.busy = None
        self._log(f"[SUCCESS] {result['summary']}")
        details = [result["summary"]]
        for title, items in (("Fittings replaced, because the hull changed:", result.get("fits_hull_changed", [])),
                             ("Kept your names for:", result.get("kept_names", [])),
                             ("Removed from your own roles, because the fitting's hull changed:",
                              result.get("local_requirements_on_new_hull", [])),
                             ("Your replacements kept:", result.get("corrections_kept", [])),
                             ("Your replacements kept, though the package changed the fitting:",
                              result.get("corrections_changed", [])),
                             ("Your replacements dropped (the original applies again):",
                              result.get("corrections_dropped", [])),
                             ("Requirements kept:", result.get("requirements_kept", [])),
                             ("Moved to your own fittings:", result.get("moved_to_personal", [])),
                             ("Character assignments dropped:", result["dropped_assignments"]),
                             ("Your own roles whose fitting was removed:", result["orphaned_local_requirements"]),
                             ("Warnings:", result["warnings"])):
            if items:
                details.append(title + "\n" + "\n".join(f"- {item}" for item in items))
        messagebox.showinfo("Import Success", "\n\n".join(details))
        # Refresh every view that shows library records
        self.root.after(0, self._refresh_after_import)

    def _on_import_failed(self, error: str):
        self.busy = None
        self._log(f"[ERROR] Import failed: {error}")
        messagebox.showerror("Import Error", f"Failed to import package:\n\n{error}")
