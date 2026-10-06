"""
The Export Doctrine Package dialog (design §11.3, decision D15).

Pick a package name (a remembered one publishes an update), tick what goes in
it, and export. The tick rules and checks live in DoctrineExportService.
"""
import tkinter as tk
import tkinter.filedialog as filedialog
from tkinter import ttk
from typing import Callable, Dict, List, Optional, Tuple

from app.loaders.package_registry import slugify
from app.services.doctrine_export_service import (LOOSE_FITS, DoctrineExportService, ExportProfiles, ExportSelection,
                                                 TreeNode)
from app.gui import style as ui_style
from app.gui.window_placement import place_window

TICKED, UNTICKED = "☑", "☐"


class ExportPackageDialog:
    def __init__(self, app, exporter: DoctrineExportService, profiles: ExportProfiles,
                 on_export: Callable[[str, ExportSelection, str], None]):
        self.app = app
        self.exporter = exporter
        self.profiles = profiles
        self.on_export = on_export
        self.selection = ExportSelection()
        self._items: Dict[str, Tuple[str, Optional[int]]] = {}     # tree item -> (kind, uid)

        self.window = tk.Toplevel(app.root)
        self.window.title("Export Doctrine Package")
        self.window.transient(app.root)
        body = ttk.Frame(self.window, padding=(10, 10))
        body.pack(fill=tk.BOTH, expand=True)

        name_row = ttk.Frame(body)
        name_row.pack(fill=tk.X)
        ttk.Label(name_row, text="Package name").pack(side=tk.LEFT)
        self.name_combo = ttk.Combobox(name_row, values=profiles.names(), width=36)
        self.name_combo.pack(side=tk.LEFT, padx=5)
        self.name_combo.bind("<<ComboboxSelected>>", lambda e: self._load_profile())
        self.name_combo.bind("<KeyRelease>", lambda e: self._refresh())
        ttk.Label(name_row, text="(pick a previous name to publish an update)", foreground=ui_style.MUTED).pack(side=tk.LEFT)

        self.tree = ttk.Treeview(body, show="tree", height=16)
        self.tree.pack(fill=tk.BOTH, expand=True, pady=5)
        self.tree.bind("<Button-1>", self._on_click)

        self.assignments_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(body, text="Include character assignments", variable=self.assignments_var,
                       command=self._refresh).pack(anchor=tk.W)
        manager_row = ttk.Frame(body)
        manager_row.pack(fill=tk.X)
        self.manager_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(manager_row, text="Doctrine Manager", variable=self.manager_var,
                        command=self._refresh).pack(side=tk.LEFT)
        ttk.Label(manager_row, text="(marks this export as Doctrine Manager approved)",
                  foreground=ui_style.MUTED).pack(side=tk.LEFT, padx=5)
        self.summary = ttk.Label(body, text="", anchor=tk.W)
        self.summary.pack(fill=tk.X)
        self.problems = ttk.Label(body, text="", foreground=ui_style.ERROR, justify=tk.LEFT, anchor=tk.W, wraplength=640)
        self.problems.pack(fill=tk.X)

        buttons = ttk.Frame(body)
        buttons.pack(anchor=tk.E, pady=(10, 0))
        ttk.Button(buttons, text="Cancel", command=self.close).pack(side=tk.LEFT, padx=5)
        self.btn_export = ttk.Button(buttons, text="Export…", command=self.export)
        self.btn_export.pack(side=tk.LEFT, padx=5)

        self._build_tree()
        self._refresh()
        try:
            self.window.grab_set()
        except tk.TclError:
            pass
        place_window(self.window)        # centred on the app, not top left

    # --- tree ----------------------------------------------------------------------------

    def _build_tree(self):
        def add(parent: str, node: TreeNode):
            item = self.tree.insert(parent, "end", open=node.kind in ("doctrine", "group"))
            self._items[item] = (node.kind, node.uid)
            self._labels[item] = node.label
            for child in node.children:
                add(item, child)

        self._labels: Dict[str, str] = {}
        for node in self.exporter.tree():
            add("", node)

    def _is_ticked(self, kind: str, uid: Optional[int]) -> bool:
        return uid in {"doctrine": self.selection.doctrine_uids, "role": self.selection.role_uids,
                       "fit": self.selection.fit_uids}.get(kind, set())

    def _on_click(self, event):
        item = self.tree.identify_row(event.y)
        if item and self.tree.identify_element(event.x, event.y) != "Treeitem.indicator":
            kind, uid = self._items.get(item, ("group", None))
            if kind != "group":
                self.toggle(kind, uid)
            elif self._labels.get(item) == LOOSE_FITS:
                self.toggle_loose_fits()

    def toggle(self, kind: str, uid: int):
        if self._is_ticked(kind, uid):
            self.exporter.untick(self.selection, kind, uid)
        else:
            self.exporter.tick(self.selection, kind, uid)
        self._refresh()

    def toggle_loose_fits(self):
        """The Fittings not used by a role heading: all on, or all off when they're all ticked (step 11.5)."""
        self.exporter.tick_loose_fits(self.selection, not self.exporter.loose_fits_ticked(self.selection))
        self._refresh()

    def _load_profile(self):
        remembered = self.profiles.selection_for(self.name_combo.get())
        if remembered is not None:
            # Records deleted since the last export drop out of the remembered selection.
            exists = {"doctrine": self.exporter.doctrines.get_doctrine, "role": self.exporter.roles.get_role,
                      "fit": self.exporter.fittings.get_fitting}
            self.selection = ExportSelection(
                {u for u in remembered.doctrine_uids if exists["doctrine"](u)},
                {u for u in remembered.role_uids if exists["role"](u)},
                {u for u in remembered.fit_uids if exists["fit"](u)},
                remembered.include_assignments, remembered.doctrine_manager)
            self.assignments_var.set(remembered.include_assignments)
            self.manager_var.set(remembered.doctrine_manager)
        self._refresh()

    def _refresh(self):
        self.selection.include_assignments = bool(self.assignments_var.get())
        self.selection.doctrine_manager = bool(self.manager_var.get())
        for item, (kind, uid) in self._items.items():
            label = self._labels[item]
            if kind == "group" and label == LOOSE_FITS:
                ticked = self.exporter.loose_fits_ticked(self.selection)
            elif kind == "group":
                self.tree.item(item, text=label)
                continue
            else:
                ticked = self._is_ticked(kind, uid)
            self.tree.item(item, text=f"{TICKED if ticked else UNTICKED} {label}")
        self.summary.config(text="Summary: " + self.exporter.summary(self.selection))
        problems = self._problems()
        self.problems.config(text="\n".join(problems))
        self.btn_export.config(state=tk.DISABLED if problems else tk.NORMAL)

    def _problems(self) -> List[str]:
        problems = [] if self.name_combo.get().strip() else ["Give the package a name."]
        return problems + self.exporter.check_selection(self.selection)

    # --- actions -------------------------------------------------------------------------

    def export(self):
        if self._problems():
            self._refresh()
            return
        name = self.name_combo.get().strip()
        path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON files", "*.json")],
                                            initialfile=f"{slugify(name)}.json", title="Export Doctrine Package")
        if not path:
            return
        selection = ExportSelection(set(self.selection.doctrine_uids), set(self.selection.role_uids),
                                    set(self.selection.fit_uids), self.selection.include_assignments,
                                    self.selection.doctrine_manager)
        self.close()
        self.on_export(name, selection, path)

    def close(self):
        self.window.destroy()

    def describe(self) -> dict:
        """What the dialog shows, for the harness."""
        def lines(parent="", depth=0):
            out = []
            for item in self.tree.get_children(parent):
                out.append("  " * depth + self.tree.item(item, "text"))
                out.extend(lines(item, depth + 1))
            return out
        return {"name": self.name_combo.get(), "names": list(self.name_combo["values"]), "tree": lines(),
                "assignments": bool(self.assignments_var.get()), "doctrine_manager": bool(self.manager_var.get()),
                "summary": self.summary["text"],
                "problems": self.problems["text"], "export_button": str(self.btn_export["state"])}
