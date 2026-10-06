import tkinter as tk
from tkinter import messagebox, ttk

from app.gui import style as ui_style
from app.gui.type_ahead import TypeAhead
from app.loaders.role_manager import fit_matches_role, fitting_in_use


class LibraryTab:
    """
    Library tab: role library, doctrine library, requirements, character
    assignments and the doctrine relationship tree.

    Extracted verbatim from EVEFleetGUI. Shared managers/services and the
    cross-tab character map are owned by the app and read through it.
    """

    def __init__(self, app, frame):
        self.app = app
        self.frame = frame

        # Library-only state
        self.library_system_map = {}
        self.library_station_map = {}       # station or structure name -> ID, for the chosen system
        # UIDs behind each listbox row, so a name containing " (" can't confuse a lookup
        self._doctrine_list_uids = []
        self._role_list_uids = []
        self._req_list_uids = []

        self._setup_library_tab()

    # --- Shared state owned by EVEFleetGUI ---------------------------------

    @property
    def role_manager(self):
        return self.app.role_manager

    @property
    def doctrine_manager(self):
        return self.app.doctrine_manager

    @property
    def fitting_manager(self):
        return self.app.fitting_manager

    @property
    def evedb_loader(self):
        return self.app.evedb_loader

    @property
    def relationship_tree_service(self):
        return self.app.relationship_tree_service

    @property
    def library_char_id_map(self):
        # Written by the Options tab, read by the Audit tab and here.
        return self.app.library_char_id_map

    def _log(self, message: str):
        self.app._log(message)

    @staticmethod
    def _selected_uid(listbox, uids):
        """The UID behind the listbox's selected row, or None."""
        selection = listbox.curselection()
        return uids[selection[0]] if selection and selection[0] < len(uids) else None

    def _populate_audit_doctrine_combo(self):
        # Cross-tab refresh: doctrine changes update the Audit tab selector.
        self.app._populate_audit_doctrine_combo()


    def _handle_create_role(self):
        """Handles creating a new role."""
        # In a real app, this would open a dialog. For now, using a simple prompt.
        import tkinter.simpledialog as sd
        role_name = sd.askstring("New Role", "Enter role name:")
        if role_name:
            try:
                is_doctrine_mode = self.doctrine_mode_var.get()
                self.role_manager.create_role(role_name, is_doctrine=is_doctrine_mode)
                self._refresh_role_list()
                self._refresh_role_selector_combo()
                self._log(f"[INFO] Role created: {role_name} (Doctrine Mode: {is_doctrine_mode})")
            except ValueError as e:
                messagebox.showerror("Error", str(e))

    def _handle_rename_role(self):
        """Handles renaming the selected role."""
        role_uid = self._selected_uid(self.library_role_listbox, self._role_list_uids)
        if role_uid is None:
            messagebox.showwarning("Warning", "Please select a role to rename.")
            return
        role_name = self.role_manager.get_role(role_uid)['role_name']

        import tkinter.simpledialog as sd
        new_name = sd.askstring("Rename Role", f"Enter new name for '{role_name}':", initialvalue=role_name)
        if new_name:
            try:
                self.role_manager.rename_role(role_uid, new_name)
                self._refresh_role_list()
                self._refresh_role_selector_combo()
                self._log(f"[INFO] Role renamed to: {new_name}")
            except (ValueError, KeyError) as e:
                messagebox.showerror("Error", str(e))

    def _handle_delete_role(self):
        """Handles deleting the selected role."""
        role_uid = self._selected_uid(self.library_role_listbox, self._role_list_uids)
        if role_uid is None:
            messagebox.showwarning("Warning", "Please select a role to delete.")
            return

        role_name = self.role_manager.get_role(role_uid)['role_name']
        message = f"Are you sure you want to delete role '{role_name}'?"
        using = [d['doctrine_name'] for d in self.doctrine_manager.doctrines_using_role(role_uid)]
        if using:
            message += ("\n\nIt will also be removed from these doctrines, with its character assignments there:\n"
                        + "\n".join(f"- {name}" for name in sorted(using)))
        if messagebox.askyesno("Confirm Delete", message):
            self.role_manager.delete_role(role_uid)
            changed = self.doctrine_manager.remove_role_everywhere(role_uid)
            self._refresh_role_list()
            self._refresh_role_selector_combo()
            self._refresh_library_requirement_list()
            if changed:
                self._refresh_doctrine_ui()       # redraws the tree
            self._log(f"[INFO] Role deleted: {role_name}" + (f" (removed from {', '.join(changed)})" if changed else ""))

    def _handle_add_requirement(self):
        """Adds a requirement to the role chosen in the Role Requirements dropdown."""
        role_uid, role = self._requirement_role()
        if role is None:
            messagebox.showwarning("Warning", "Please select a role first.")
            return
        role_name = role['role_name']

        fit_name = self.library_fit_combo.get()
        if not fit_name:
            messagebox.showwarning("Warning", "Please select a fitting.")
            return

        fit_uid = self.library_fit_uid_map.get(fit_name)
        if fit_uid is None:
            messagebox.showwarning("Warning", "Please select a fitting from the list.")
            return

        # Requirements store IDs (SDE plan Phase 10); <Any ...> is None. A name typed
        # but not picked from the list is refused rather than saved as an unknown place.
        system = self.library_system_combo.get()
        system_id = None
        if system and system != "<Any System>":
            system_id = self.library_system_map.get(system)
            if system_id is None:
                messagebox.showwarning("Warning", f"'{system}' isn't a solar system. Please pick one from the list.")
                return
        station = self.library_station_combo.get()
        location_id = None
        if system_id is not None and station and station != "<Any Station>":
            location_id = self.library_station_map.get(station)
            if location_id is None:
                messagebox.showwarning("Warning", f"'{station}' isn't a station in {system}. Please pick one from the list.")
                return

        # On a role a package installed, it's the pilot's own: updates ask before removing it.
        registry = getattr(self.app, "package_registry", None)
        added = bool(registry is not None and registry.installed_by("role", role_uid))
        try:
            self.role_manager.add_requirement(role_uid, fit_uid, system_id, location_id,
                                              station if location_id is not None else None, added=added)
            self._refresh_library_requirement_list()
            self._log(f"[INFO] Added requirement to role '{role_name}': {fit_name} @ "
                      f"{system if system_id is not None else '<Any System>'} @ "
                      f"{station if location_id is not None else '<Any Station>'}")
        except Exception as e:
            messagebox.showerror("Error", str(e))
            self._log(f"[ERROR] Failed to add requirement: {e}")

    def _handle_remove_requirement(self):
        """Removes a requirement in the Library tab."""
        req_uid = self._selected_uid(self.library_req_listbox, self._req_list_uids)
        if req_uid is None:
            messagebox.showwarning("Warning", "Please select a requirement to remove.")
            return

        role_uid, role = self._requirement_role()
        if role is None:
            messagebox.showwarning("Warning", "Please select a role first.")
            return
        role_name = role['role_name']

        if messagebox.askyesno("Confirm Delete", "Are you sure you want to remove this requirement?"):
            try:
                self.role_manager.remove_requirement(role_uid, req_uid)
                self._refresh_library_requirement_list()
                self._log(f"[INFO] Removed requirement from role '{role_name}'")
            except Exception as e:
                messagebox.showerror("Error", str(e))
                self._log(f"[ERROR] Failed to remove requirement: {e}")

    def _selected_requirement(self):
        """(role uid, requirement) selected in the requirement list, or (None, None)."""
        req_uid = self._selected_uid(self.library_req_listbox, self._req_list_uids)
        role_uid, role = self._requirement_role()
        if req_uid is None or role is None:
            return None, None
        requirement = next((r for r in role.get('requirements', []) if r.get('req_uid') == req_uid), None)
        return (role_uid, requirement) if requirement else (None, None)

    def _on_requirement_selected(self, event=None):
        """Undo Replacement is only enabled for a replaced requirement."""
        _, requirement = self._selected_requirement()
        replaced = requirement is not None and fitting_in_use(requirement) != requirement.get('fit_uid')
        self.btn_undo_replacement.config(state=tk.NORMAL if replaced else tk.DISABLED)

    def _handle_replace_requirement(self):
        """Replaces the selected requirement's fitting with the one chosen in Hull and Fit, keeping its place."""
        role_uid, requirement = self._selected_requirement()
        if requirement is None:
            messagebox.showwarning("Warning", "Please select a requirement to replace.")
            return
        fit_name = self.library_fit_combo.get()
        fit_uid = self.library_fit_uid_map.get(fit_name)
        if fit_uid is None:
            messagebox.showwarning("Warning", "Please choose the replacement fitting in Hull and Fit.")
            return
        if fit_uid == fitting_in_use(requirement):
            messagebox.showwarning("Warning", "That fitting is already the one in use for this requirement.")
            return
        req_uid = requirement['req_uid']
        try:
            self.role_manager.replace_requirement(role_uid, req_uid, fit_uid)
        except Exception as e:
            messagebox.showerror("Error", str(e))
            self._log(f"[ERROR] Failed to replace requirement: {e}")
            return
        self._refresh_library_requirement_list(select=req_uid)
        role_name = self.role_manager.get_role(role_uid)['role_name']
        self._log(f"[INFO] Requirement {req_uid} in role '{role_name}' now uses {fit_name}")

    def _handle_undo_replacement(self):
        """The selected requirement's own fitting applies again."""
        role_uid, requirement = self._selected_requirement()
        if requirement is None:
            messagebox.showwarning("Warning", "Please select a requirement.")
            return
        req_uid = requirement['req_uid']
        if self.role_manager.undo_replacement(role_uid, req_uid):
            self._refresh_library_requirement_list(select=req_uid)
            role_name = self.role_manager.get_role(role_uid)['role_name']
            self._log(f"[INFO] Requirement {req_uid} in role '{role_name}' uses its own fitting again")

    def _refresh_role_list(self):
        """Refreshes the role listbox: shared roles (1000-1999) in Doctrine Mode, local roles (2000+) otherwise."""
        self.library_role_listbox.delete(0, tk.END)
        self._role_list_uids = []
        is_doctrine_mode = self.doctrine_mode_var.get()
        for uid, role in sorted(self.role_manager.roles.items(), key=lambda x: x[1]['role_name']):
            if (1000 <= uid <= 1999) if is_doctrine_mode else (uid >= 2000):
                self.library_role_listbox.insert(tk.END, f"{role['role_name']} ({uid})")
                self._role_list_uids.append(uid)

    def _refresh_role_selector_combo(self):
        """Roles were created, renamed or deleted: refresh the lists that name them."""
        self._refresh_add_role_combo()
        self._refresh_library_requirement_list()

    def _setup_library_tab(self):
        """Sets up the Library tab layout (UI ONLY)."""
        
        # Main Content Container
        main_content_frame = ttk.Frame(self.frame)
        main_content_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        # --- Left Sidebar (220px) ---
        left_sidebar = ttk.Frame(main_content_frame, width=220)
        left_sidebar.pack(side=tk.LEFT, fill=tk.Y)
        left_sidebar.pack_propagate(False)

        # Side Management Frame (Doctrine Library above Role Library, as in the hierarchy)
        side_mgmt_frame = ttk.Frame(left_sidebar)
        side_mgmt_frame.pack(fill=tk.BOTH, expand=True)

        # Doctrine Mode Toggle
        doctrine_mode_frame = ttk.Frame(side_mgmt_frame)
        doctrine_mode_frame.pack(fill=tk.X, padx=5, pady=(5, 0))

        self.doctrine_mode_var = tk.BooleanVar(value=False)
        self.doctrine_mode_check = ttk.Checkbutton(
            doctrine_mode_frame,
            text="Doctrine Mode",
            variable=self.doctrine_mode_var,
            command=self._on_doctrine_mode_toggled
        )
        self.doctrine_mode_check.pack(anchor=tk.W)

        # --- Doctrine Library (Small Management) ---
        doctrine_lib_frame = ttk.LabelFrame(side_mgmt_frame, text="Doctrine Library", padding=(5, 5))
        doctrine_lib_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True, pady=(0, 5))

        doc_mgmt_btn_frame = ttk.Frame(doctrine_lib_frame)
        doc_mgmt_btn_frame.pack(fill=tk.X, pady=5)

        ttk.Button(doc_mgmt_btn_frame, text="Create", width=7, command=self._handle_create_doctrine).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=2)
        ttk.Button(doc_mgmt_btn_frame, text="Rename", width=7, command=self._handle_rename_doctrine).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=2)
        ttk.Button(doc_mgmt_btn_frame, text="Delete", width=7, command=self._handle_delete_doctrine).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=2)

        doc_list_container = ttk.Frame(doctrine_lib_frame)
        doc_list_container.pack(fill=tk.BOTH, expand=True)

        self.library_doctrine_listbox = tk.Listbox(doc_list_container)
        self.library_doctrine_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        doc_scrollbar = ttk.Scrollbar(doc_list_container, orient=tk.VERTICAL, command=self.library_doctrine_listbox.yview)
        doc_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.library_doctrine_listbox.config(yscrollcommand=doc_scrollbar.set)

        # --- Role Library (Small Management) ---
        role_lib_frame = ttk.LabelFrame(side_mgmt_frame, text="Role Library", padding=(5, 5))
        role_lib_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        role_mgmt_btn_frame = ttk.Frame(role_lib_frame)
        role_mgmt_btn_frame.pack(fill=tk.X, pady=5)

        ttk.Button(role_mgmt_btn_frame, text="Create", width=7, command=self._handle_create_role).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=2)
        ttk.Button(role_mgmt_btn_frame, text="Rename", width=7, command=self._handle_rename_role).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=2)
        ttk.Button(role_mgmt_btn_frame, text="Delete", width=7, command=self._handle_delete_role).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=2)

        role_list_container = ttk.Frame(role_lib_frame)
        role_list_container.pack(fill=tk.BOTH, expand=True)

        self.library_role_listbox = tk.Listbox(role_list_container)
        self.library_role_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        role_scrollbar = ttk.Scrollbar(role_list_container, orient=tk.VERTICAL, command=self.library_role_listbox.yview)
        role_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.library_role_listbox.config(yscrollcommand=role_scrollbar.set)

        # --- Doctrine packages (moved from the Export tab, UI rework step 7.1) ---
        package_frame = ttk.LabelFrame(left_sidebar, text="Doctrine Packages", padding=(5, 5))
        package_frame.pack(side=tk.BOTTOM, fill=tk.X, pady=(5, 0))
        self.btn_export_package = ttk.Button(package_frame, text="Export Doctrine Package…",
                                            command=lambda: self.app.package_actions._handle_export_package())
        self.btn_export_package.pack(fill=tk.X, pady=2)
        self.btn_import_package = ttk.Button(package_frame, text="Import Doctrine Package…",
                                            command=lambda: self.app.package_actions._handle_import_package())
        self.btn_import_package.pack(fill=tk.X, pady=2)

        # --- Center Area (Requirements) ---
        center_area = ttk.Frame(main_content_frame)
        center_area.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        req_frame = ttk.LabelFrame(center_area, text="Role Requirements", padding=(5, 5))
        req_frame.pack(fill=tk.BOTH, expand=True)

        # Its own Role dropdown: the only thing that picks whose requirements are shown,
        # added and removed (UI rework step 7.2).
        req_role_row = ttk.Frame(req_frame)
        req_role_row.pack(fill=tk.X, pady=(0, 5))
        ttk.Label(req_role_row, text="Role:").pack(side=tk.LEFT, padx=(0, 5))
        self.library_req_role_combo = ttk.Combobox(req_role_row, width=40, state="readonly")
        self.library_req_role_combo.pack(side=tk.LEFT)
        self.library_req_role_combo.bind("<<ComboboxSelected>>", self._on_requirement_role_selected)
        self._req_role_uid = None           # the chosen role, kept across renames and refreshes

        req_list_container = ttk.Frame(req_frame)
        req_list_container.pack(fill=tk.BOTH, expand=True)

        # exportselection off: picking the replacement in Hull and Fit mustn't clear the selected requirement
        self.library_req_listbox = tk.Listbox(req_list_container, exportselection=False)
        self.library_req_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        req_scrollbar = ttk.Scrollbar(req_list_container, orient=tk.VERTICAL, command=self.library_req_listbox.yview)
        req_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.library_req_listbox.config(yscrollcommand=req_scrollbar.set)

        # Requirement Entry Controls
        entry_controls_frame = ttk.LabelFrame(req_frame, text="New Requirement", padding=(5, 5))
        entry_controls_frame.pack(fill=tk.X, padx=5, pady=5)

        self.library_fit_uid_map = {}

        # The dropdowns and buttons sit together in one grid, centred in the block
        entry_grid = ttk.Frame(entry_controls_frame)
        entry_grid.pack()

        ttk.Label(entry_grid, text="Hull:").grid(row=0, column=0, sticky=tk.W, padx=2, pady=2)
        self.library_hull_combo = ttk.Combobox(entry_grid, width=20)
        self.library_hull_combo.grid(row=0, column=1, padx=5, pady=2)

        ttk.Label(entry_grid, text="Fit:").grid(row=0, column=2, sticky=tk.W, padx=2, pady=2)
        self.library_fit_combo = ttk.Combobox(entry_grid, width=20, state="readonly")
        self.library_fit_combo.grid(row=0, column=3, padx=5, pady=2)

        # Row 2: System and Station
        ttk.Label(entry_grid, text="System:").grid(row=1, column=0, sticky=tk.W, padx=2, pady=2)
        self.library_system_combo = ttk.Combobox(entry_grid, width=20)
        self.library_system_combo.grid(row=1, column=1, padx=5, pady=2)

        ttk.Label(entry_grid, text="Station:").grid(row=1, column=2, sticky=tk.W, padx=2, pady=2)
        self.library_station_combo = ttk.Combobox(entry_grid, width=20, state="readonly")
        self.library_station_combo.grid(row=1, column=3, padx=5, pady=2)

        # Hull and System have long lists: type to narrow them, and a name typed exactly is
        # taken at once, so Fit and Station follow. The hint says what the typing matches.
        self.library_entry_hint = ttk.Label(entry_grid, text="", style=ui_style.HINT_LABEL)
        self.library_entry_hint.grid(row=2, column=0, columnspan=4, sticky=tk.W, padx=2)
        self._hull_ahead = TypeAhead(self.library_hull_combo, lambda name: self._on_library_hull_selected(),
                                     noun="hull", hint=self.library_entry_hint)
        self._system_ahead = TypeAhead(self.library_system_combo, lambda name: self._on_library_system_selected(),
                                       noun="system", hint=self.library_entry_hint, pinned=["<Any System>"])

        # Buttons
        btn_frame = ttk.Frame(entry_grid)
        btn_frame.grid(row=3, column=0, columnspan=4, pady=(4, 10))

        # Replace swaps the selected requirement's fitting for the one chosen above, keeping its
        # place; Undo, beneath it, brings the original back (doctrine tweaks plan, step 11.2).
        ttk.Button(btn_frame, text="Add Requirement", command=self._handle_add_requirement).grid(
            row=0, column=0, padx=5, pady=2, sticky=tk.EW)
        ttk.Button(btn_frame, text="Remove Requirement", command=self._handle_remove_requirement).grid(
            row=1, column=0, padx=5, pady=2, sticky=tk.EW)
        ttk.Button(btn_frame, text="Replace Requirement", command=self._handle_replace_requirement).grid(
            row=0, column=1, padx=5, pady=2, sticky=tk.EW)
        self.btn_undo_replacement = ttk.Button(btn_frame, text="Undo Replacement", command=self._handle_undo_replacement,
                                               state=tk.DISABLED)
        self.btn_undo_replacement.grid(row=1, column=1, padx=5, pady=2, sticky=tk.EW)
        self.library_req_listbox.bind("<<ListboxSelect>>", self._on_requirement_selected)

        # --- Right Sidebar (320px) ---
        right_sidebar_frame = ttk.Frame(main_content_frame, width=320)
        right_sidebar_frame.pack(side=tk.RIGHT, fill=tk.Y)
        right_sidebar_frame.pack_propagate(False)

        # --- Right Sidebar: Doctrine Overview (UI rework step 7.3) ---
        # Pick a doctrine, add roles to it, and right-click a role to assign a character or
        # remove the role; right-click a character to remove them. A role needs no character.
        rel_tree_frame = ttk.LabelFrame(right_sidebar_frame, text="Doctrine Overview", padding=(5, 5))
        rel_tree_frame.pack(fill=tk.BOTH, expand=True)

        doctrine_row = ttk.Frame(rel_tree_frame)
        doctrine_row.pack(fill=tk.X, pady=(0, 3))
        ttk.Label(doctrine_row, text="Doctrine:", width=8, anchor=tk.W).pack(side=tk.LEFT)
        self.library_doctrine_combo = ttk.Combobox(doctrine_row, state="readonly")
        self.library_doctrine_combo.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.library_doctrine_combo.bind("<<ComboboxSelected>>", self._on_library_doctrine_selected)

        add_role_row = ttk.Frame(rel_tree_frame)
        add_role_row.pack(fill=tk.X, pady=(0, 5))
        ttk.Label(add_role_row, text="Add role:", width=8, anchor=tk.W).pack(side=tk.LEFT)
        self.library_add_role_combo = ttk.Combobox(add_role_row, state="readonly", width=18)
        self.library_add_role_combo.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.btn_add_role = ttk.Button(add_role_row, text="Add", command=self._handle_add_role_to_doctrine)
        self.btn_add_role.pack(side=tk.LEFT, padx=(5, 0))

        tree_container = ttk.Frame(rel_tree_frame)
        tree_container.pack(fill=tk.BOTH, expand=True)

        self.library_rel_tree = ttk.Treeview(tree_container, columns=("type"), show="tree", displaycolumns=())
        self.library_rel_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        rel_tree_scrollbar = ttk.Scrollbar(tree_container, orient=tk.VERTICAL, command=self.library_rel_tree.yview)
        rel_tree_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.library_rel_tree.config(yscrollcommand=rel_tree_scrollbar.set)

        # Bindings for relationship tree
        self.library_rel_tree.bind("<Button-3>", self._on_library_rel_tree_right_click)
        self.library_rel_tree_menu = tk.Menu(self.library_rel_tree, tearoff=0)

        self._refresh_doctrine_list()
        self._refresh_role_list()
        self._populate_library_systems()
        self._populate_library_doctrines()            # also draws the relationship tree
        self._refresh_library_requirement_list()      # Role Requirements starts on the first role (and fills Hull/Fit)

    def _requirement_role_fittings(self):
        """Fittings the chosen requirement role may use: shared for a shared role, local for a local one."""
        if self._req_role_uid is None:
            return []
        return [f for f in self.fitting_manager.list_fittings()
                if f.get("fit_name") and fit_matches_role(self._req_role_uid, f.get("fit_uid"))]

    def _on_library_hull_selected(self, event=None):
        """Lists the chosen hull's fittings for the requirement role; keeps the chosen fit if it's still there."""
        hull_name = self.library_hull_combo.get()
        if self._hull_ahead.exact(hull_name) == hull_name:
            self._hull_ahead.accepted = hull_name
        fit_data = sorted(
            (f"{f.get('hull', 'Unknown')} - {f.get('fit_name')} ({f.get('fit_uid')})", f.get("fit_uid"))
            for f in self._requirement_role_fittings() if hull_name and f.get("hull") == hull_name)
        self.library_fit_uid_map = dict(fit_data)
        fit_names = [name for name, _ in fit_data]
        self.library_fit_combo['values'] = fit_names
        current = self.library_fit_combo.get()
        self.library_fit_combo.set(current if current in fit_names else (fit_names[0] if fit_names else ""))

    def _populate_library_hulls(self):
        """Hulls with a fitting the requirement role may use; keeps the chosen hull while it's still listed."""
        hulls = sorted({f["hull"] for f in self._requirement_role_fittings() if f.get("hull")})
        current = self.library_hull_combo.get()
        self._hull_ahead.set_choices(hulls, current=current if current in hulls else (hulls[0] if hulls else ""))
        self._on_library_hull_selected()

    def _populate_library_systems(self):
        """Populates the Library tab System Combobox with all solar systems."""
        try:
            systems = self.evedb_loader.get_all_solar_systems()
            # Create a mapping of Name -> ID for easy lookup when station is selected
            self.library_system_map = {s['solarSystemName']: s['solarSystemID'] for s in systems}
            system_names = sorted(self.library_system_map.keys())
            # "<Any System>" saves no system: the requirement matches the hull wherever it is
            # (supercarriers and titans can't dock in NPC stations).
            # Default to "Jita" (the main trade hub) if available, otherwise the first name
            target_default = "Jita"
            if target_default in self.library_system_map:
                current = target_default
            else:
                current = system_names[0] if system_names else ""
            self._system_ahead.set_choices(system_names, current=current)
            self._on_library_system_selected()
        except Exception as e:
            self._log(f"[ERROR] Failed to populate library systems: {e}")

    def _on_library_system_type(self, event):
        """Typing in the System box narrows its list (and takes a name typed exactly); see TypeAhead."""
        self._system_ahead.on_key(event)

    def _on_library_system_selected(self, event=None):
        """Handles selection in the Library tab System Combobox."""
        system_name = self.library_system_combo.get()
        if self._system_ahead.exact(system_name) == system_name:
            self._system_ahead.accepted = system_name
        self.library_station_map = {}
        if not system_name or system_name not in self.library_system_map:
            self.library_station_combo['values'] = ["<Any Station>"]
            self.library_station_combo.set("<Any Station>")
            return

        system_id = self.library_system_map[system_name]
        try:
            stations = self.evedb_loader.get_stations_in_system(system_id)
            # Player structures come from the location cache (ones the characters have assets in)
            structures = self.evedb_loader.get_structures_in_system(system_id)
            # Name -> station or structure ID, which is what a requirement saves
            self.library_station_map = {name: location_id for location_id, name in structures}
            self.library_station_map.update({s['stationName']: s['stationID'] for s in stations})
            station_names = ["<Any Station>"] + sorted(self.library_station_map)
            self.library_station_combo['values'] = station_names
            self.library_station_combo.set(station_names[0])
            self._log(f"[INFO] Populated {len(station_names)-1} stations for system: {system_name}"
                      + (f" ({len(structures)} structure(s))" if structures else ""))
        except Exception as e:
            self._log(f"[ERROR] Failed to populate stations for system {system_name}: {e}")
            self.library_station_map = {}
            self.library_station_combo['values'] = ["<Any Station>"]
            self.library_station_combo.set("<Any Station>")

    def _on_library_doctrine_selected(self, event=None):
        """The overview's Doctrine changed: list the roles it can add and redraw the tree."""
        self._refresh_add_role_combo()
        self._update_doctrine_relationships()

    def _update_doctrine_relationships(self):
        """Updates the Doctrine Relationships view in the right sidebar."""
        self._update_relationship_tree()

    def _populate_library_doctrines(self):
        """Populates the Library tab Doctrine Combobox."""
        try:
            doctrines = self.doctrine_manager.list_doctrines()
            doctrine_names = sorted([d['doctrine_name'] for d in doctrines])
            self.library_doctrine_combo['values'] = doctrine_names
            if doctrine_names:
                # Keep the doctrine being looked at, if it still exists (step 7.3).
                if self.library_doctrine_combo.get() not in doctrine_names:
                    self.library_doctrine_combo.current(0)
            else:
                self.library_doctrine_combo.set("")
            self._on_library_doctrine_selected()
        except Exception as e:
            self._log(f"[ERROR] Failed to populate library doctrines: {e}")

    def _refresh_requirement_role_combo(self):
        """Every role, by name; keeps the chosen role (following a rename) or picks the first."""
        roles = sorted(self.role_manager.roles.items(), key=lambda item: item[1]['role_name'].casefold())
        self._req_role_names = {role['role_name']: uid for uid, role in roles}
        self.library_req_role_combo['values'] = list(self._req_role_names)
        if self._req_role_uid not in self.role_manager.roles:
            self._req_role_uid = roles[0][0] if roles else None
        role = self.role_manager.get_role(self._req_role_uid) if self._req_role_uid is not None else None
        self.library_req_role_combo.set(role['role_name'] if role else "")
        self._populate_library_hulls()      # the New Requirement lists follow the role's range

    def _on_requirement_role_selected(self, event=None):
        """The Role Requirements dropdown changed: show that role's requirements."""
        self._req_role_uid = self._req_role_names.get(self.library_req_role_combo.get())
        self._refresh_library_requirement_list()

    def _requirement_role(self):
        """(uid, role) chosen in the Role Requirements dropdown, or (None, None)."""
        uid = self._req_role_names.get(self.library_req_role_combo.get())
        role = self.role_manager.get_role(uid) if uid is not None else None
        return (uid, role) if role else (None, None)

    def _fit_label(self, fit_uid):
        """"Hull - Fit name" for a fitting, or why there isn't one."""
        if not fit_uid:
            return "[No Fitting UID]"
        fitting = self.fitting_manager.get_fitting(fit_uid)
        if not fitting:
            return f"[Missing Fitting (UID: {fit_uid})]"
        return f"{fitting.get('hull', 'Unknown')} - {fitting.get('fit_name', fitting.get('fitting', 'Unknown'))}"

    def _refresh_library_requirement_list(self, select=None):
        """
        Refreshes the Requirement listbox for the role chosen in the Role Requirements dropdown.
        select: a req_uid to select afterwards.
        """
        self._refresh_requirement_role_combo()
        self.library_req_listbox.delete(0, tk.END)
        self._req_list_uids = []
        self.btn_undo_replacement.config(state=tk.DISABLED)
        _, selected_role = self._requirement_role()
        if not selected_role:
            return

        for req in selected_role.get('requirements', []):
            req_uid = req.get('req_uid', 'Unknown')
            fit_uid = fitting_in_use(req)           # the pilot's replacement, if any (step 11.2)
            system = self.evedb_loader.get_system_name(req['system_id']) if req.get('system_id') else "<Any System>"
            station = (self.evedb_loader.location_label(req['location_id'], req.get('location_name'))
                       if req.get('location_id') else "<Any Station>")

            fit_info = self._fit_label(fit_uid)
            if ((self.fitting_manager.get_fitting(fit_uid) if fit_uid else None) or {}).get('doctrine_metadata'):
                fit_info += " [+ requirements]"
            notes = ""
            if fit_uid != req.get('fit_uid'):
                notes += f" (Replaced {self._fit_label(req.get('fit_uid'))})"
            if req.get('added'):
                notes += " (added)"
            elif req.get('kept'):
                notes += " (kept)"

            self.library_req_listbox.insert(tk.END, f"{fit_info} | {system} | {station}{notes} (UID: {req_uid})")
            self._req_list_uids.append(req_uid)
        if select in self._req_list_uids:
            index = self._req_list_uids.index(select)
            self.library_req_listbox.selection_set(index)
            self.library_req_listbox.see(index)
            self._on_requirement_selected()

    def _handle_create_doctrine(self):
        import tkinter.simpledialog as sd
        name = sd.askstring("New Doctrine", "Enter doctrine name:")
        if name:
            try:
                # Doctrine Mode creates a shared doctrine (5000-5999); otherwise it's local (6000+, D18).
                self.doctrine_manager.create_doctrine(name, is_doctrine=self.doctrine_mode_var.get())
                self._refresh_doctrine_ui()
                self._log(f"[INFO] Doctrine created: {name}")
            except ValueError as e:
                messagebox.showerror("Error", str(e))

    def _handle_rename_doctrine(self):
        doctrine_uid = self._selected_uid(self.library_doctrine_listbox, self._doctrine_list_uids)
        if doctrine_uid is None:
            messagebox.showwarning("Warning", "Please select a doctrine to rename.")
            return
        doctrine_name = self.doctrine_manager.get_doctrine(doctrine_uid)['doctrine_name']

        import tkinter.simpledialog as sd
        new_name = sd.askstring("Rename Doctrine", f"Enter new name for '{doctrine_name}':", initialvalue=doctrine_name)
        if new_name:
            try:
                self.doctrine_manager.rename_doctrine(doctrine_uid, new_name)
                if self.library_doctrine_combo.get() == doctrine_name:
                    # The overview keeps showing the doctrine under its new name
                    self.library_doctrine_combo.set(self.doctrine_manager.get_doctrine(doctrine_uid)['doctrine_name'])
                self._refresh_doctrine_ui()
                self._log(f"[INFO] Doctrine renamed to: {new_name}")
            except (ValueError, KeyError) as e:
                messagebox.showerror("Error", str(e))

    def _handle_delete_doctrine(self):
        doctrine_uid = self._selected_uid(self.library_doctrine_listbox, self._doctrine_list_uids)
        if doctrine_uid is None:
            messagebox.showwarning("Warning", "Please select a doctrine to delete.")
            return
        doctrine_name = self.doctrine_manager.get_doctrine(doctrine_uid)['doctrine_name']

        if messagebox.askyesno("Confirm Delete", f"Are you sure you want to delete doctrine '{doctrine_name}'?"):
            self.doctrine_manager.delete_doctrine(doctrine_uid)
            self._refresh_doctrine_ui()
            self._log(f"[INFO] Doctrine deleted: {doctrine_name}")

    def _refresh_doctrine_ui(self):
        """Unified refresh for all doctrine-related UI components."""
        self._refresh_doctrine_list()
        self._populate_library_doctrines()
        self._populate_audit_doctrine_combo()

    def _refresh_doctrine_list(self):
        """Refreshes the Library tab Doctrine Listbox."""
        self.library_doctrine_listbox.delete(0, tk.END)
        self._doctrine_list_uids = []

        # Doctrines are always shared, so every doctrine is listed in both modes
        # (Doctrine Mode only filters roles and fittings).
        for doctrine in self.doctrine_manager.list_doctrines():
            name = doctrine["doctrine_name"]
            uid = doctrine["doctrine_uid"]

            self.library_doctrine_listbox.insert(tk.END, f"{name} ({uid})")
            self._doctrine_list_uids.append(uid)

    def _update_relationship_tree(self):
        """Populates the relationship treeview with the hierarchy: Doctrine -> Roles -> Characters."""
        self.library_rel_tree.delete(*self.library_rel_tree.get_children())
        
        try:
            selected_doctrine_name = self.library_doctrine_combo.get()
            if not selected_doctrine_name:
                return

            # Invert the char map for the service: {id: name}
            char_id_to_name = {cid: name for name, cid in self.library_char_id_map.items()}

            hierarchy = self.relationship_tree_service.build_hierarchy(
                selected_doctrine_name, 
                char_id_to_name
            )

            for doctrine in hierarchy:
                d_id = self.library_rel_tree.insert(
                    "", "end", 
                    text=doctrine["name"], 
                    values=("Doctrine", doctrine["uid"]), 
                    tags=("doctrine",)
                )
                self.library_rel_tree.item(d_id, open=True)

                for role in doctrine.get("roles", []):
                    r_id = self.library_rel_tree.insert(
                        d_id, "end", 
                        text=role["name"], 
                        values=("Role", role["uid"]), 
                        tags=("role",)
                    )
                    self.library_rel_tree.item(r_id, open=True)

                    for char in role.get("characters", []):
                        self.library_rel_tree.insert(
                            r_id, "end", 
                            text=char["name"], 
                            values=("Character", char["uid"]), 
                            tags=("character",)
                        )

        except Exception as e:
            self._log(f"[ERROR] Failed to update relationship tree: {e}")

    def _on_library_rel_tree_right_click(self, event):
        """
        Right-click a role: assign a character to it, or remove it from the doctrine.
        Right-click a character: remove them from that role.
        """
        tree = self.library_rel_tree
        item_id = tree.identify_row(event.y)
        values = tree.item(item_id, "values") if item_id else ()
        if not values or values[0] not in ("Role", "Character"):
            return
        tree.selection_set(item_id)
        self._build_rel_tree_menu(item_id)
        try:
            self.library_rel_tree_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.library_rel_tree_menu.grab_release()

    def _build_rel_tree_menu(self, item_id):
        """Fills the right-click menu for a role or character row."""
        tree, menu = self.library_rel_tree, self.library_rel_tree_menu
        menu.delete(0, tk.END)
        kind = tree.item(item_id, "values")[0]
        if kind == "Character":
            role_name = tree.item(tree.parent(item_id), "text")
            menu.add_command(label=f"Remove {tree.item(item_id, 'text')} from {role_name}",
                             command=lambda: self._remove_character_from_tree(item_id))
            return
        role_uid = int(tree.item(item_id, "values")[1])
        doctrine_uid = int(tree.item(tree.parent(item_id), "values")[1])
        assign = tk.Menu(menu, tearoff=0)
        for name, character_id in self._assignable_characters(doctrine_uid, role_uid):
            assign.add_command(label=name, command=lambda c=character_id, n=name:
                               self._assign_character(doctrine_uid, role_uid, c, n))
        if assign.index("end") is None:
            assign.add_command(label="(every connected character is assigned)", state=tk.DISABLED)
        menu.add_cascade(label="Assign character", menu=assign)
        menu.add_separator()
        menu.add_command(label=f"Remove {tree.item(item_id, 'text')} from the doctrine",
                         command=lambda: self._remove_role_from_tree(item_id))

    def _remove_character_from_tree(self, item_id):
        """Removes the character on a relationship tree row from its role (the row's parent)."""
        tree = self.library_rel_tree
        role_item = tree.parent(item_id)
        doctrine_item = tree.parent(role_item)
        character_id = str(tree.item(item_id, "values")[1])      # Tk hands numeric IDs back as ints
        role_uid = int(tree.item(role_item, "values")[1])
        doctrine_uid = int(tree.item(doctrine_item, "values")[1])
        character_name = tree.item(item_id, "text")
        role_name = tree.item(role_item, "text")
        doctrine_name = tree.item(doctrine_item, "text")

        if not messagebox.askyesno("Confirm Removal", f"Remove {character_name} from {doctrine_name} ({role_name})?"):
            return
        try:
            self.doctrine_manager.remove_character_from_role(doctrine_uid, role_uid, character_id)
        except (KeyError, ValueError) as e:
            self._log(f"[ERROR] Failed to remove assignment: {e}")
            messagebox.showerror("Error", f"Failed to remove assignment: {e}")
            return
        self._log(f"[INFO] Removed {character_name} from {doctrine_name} ({role_name})")
        self._refresh_after_assignment_change()

    # --- Doctrine Overview (UI rework step 7.3) ------------------------------------

    def _overview_doctrine(self):
        """(uid, doctrine) shown in the overview, or (None, None)."""
        name = self.library_doctrine_combo.get()
        for doctrine in self.doctrine_manager.list_doctrines():
            if doctrine['doctrine_name'] == name:
                return doctrine['doctrine_uid'], doctrine
        return None, None

    def _refresh_add_role_combo(self):
        """Roles not yet in the overview's doctrine, by name."""
        _, doctrine = self._overview_doctrine()
        in_doctrine = set(doctrine.get('roles', [])) if doctrine else set()
        roles = sorted(((r['role_name'], uid) for uid, r in self.role_manager.roles.items() if uid not in in_doctrine),
                       key=lambda item: item[0].casefold())
        self._add_role_names = dict(roles)
        names = [name for name, _ in roles]
        self.library_add_role_combo['values'] = names
        if self.library_add_role_combo.get() not in names:
            self.library_add_role_combo.set(names[0] if names else "")
        state = tk.NORMAL if doctrine and names else tk.DISABLED
        self.btn_add_role.config(state=state)

    def _handle_add_role_to_doctrine(self):
        """Adds the chosen role to the overview's doctrine. No character is needed."""
        doctrine_uid, doctrine = self._overview_doctrine()
        role_name = self.library_add_role_combo.get()
        role_uid = self._add_role_names.get(role_name)
        if doctrine is None or role_uid is None:
            messagebox.showwarning("Warning", "Please choose a doctrine and a role to add.")
            return
        try:
            self.doctrine_manager.add_role(doctrine_uid, role_uid)
        except (KeyError, ValueError) as e:
            messagebox.showerror("Error", f"Failed to add the role: {e}")
            return
        self._log(f"[INFO] Added role {role_name} to {doctrine['doctrine_name']}")
        self._refresh_add_role_combo()
        self._refresh_after_assignment_change()

    def _assignable_characters(self, doctrine_uid, role_uid):
        """(name, ID) of connected characters not yet assigned to this role in this doctrine."""
        assigned = {str(c) for c in self.doctrine_manager.get_character_assignments(doctrine_uid).get(str(role_uid), [])}
        return sorted(((name, str(cid)) for name, cid in self.library_char_id_map.items() if str(cid) not in assigned),
                      key=lambda item: item[0].casefold())

    def _assign_character(self, doctrine_uid, role_uid, character_id, character_name):
        """Assigns a character to a role in a doctrine (from the role's right-click menu)."""
        doctrine = self.doctrine_manager.get_doctrine(doctrine_uid)
        role = self.role_manager.get_role(role_uid)
        try:
            self.doctrine_manager.assign_character_to_role(doctrine_uid, role_uid, str(character_id))
        except (KeyError, ValueError) as e:
            self._log(f"[ERROR] Failed to assign {character_name}: {e}")
            messagebox.showerror("Error", f"Failed to assign {character_name}: {e}")
            return
        self._log(f"[INFO] Assigned {character_name} to {doctrine['doctrine_name']} -> {role['role_name']}")
        self._refresh_after_assignment_change()

    def _remove_role_from_tree(self, item_id):
        """Removes the role on a tree row from its doctrine, with its character assignments there."""
        tree = self.library_rel_tree
        doctrine_item = tree.parent(item_id)
        role_uid = int(tree.item(item_id, "values")[1])
        doctrine_uid = int(tree.item(doctrine_item, "values")[1])
        role_name, doctrine_name = tree.item(item_id, "text"), tree.item(doctrine_item, "text")
        characters = [tree.item(c, "text") for c in tree.get_children(item_id)]
        message = f"Remove {role_name} from {doctrine_name}?"
        if characters:
            message += "\n\nIts character assignments there go too:\n" + "\n".join(characters)
        message += "\n\nThe role itself stays in the Role Library."
        if not messagebox.askyesno("Remove Role", message):
            return
        try:
            self.doctrine_manager.remove_role_from_doctrine(doctrine_uid, role_uid)
        except KeyError as e:
            messagebox.showerror("Error", f"Failed to remove the role: {e}")
            return
        self._log(f"[INFO] Removed role {role_name} from {doctrine_name}")
        self._refresh_add_role_combo()
        self._refresh_after_assignment_change()

    def _refresh_after_assignment_change(self):
        self._refresh_doctrine_list()
        self._refresh_role_list()
        self._refresh_role_selector_combo()
        self._refresh_library_requirement_list()
        self._update_relationship_tree()

    def _on_doctrine_mode_toggled(self):
        """Handles the Doctrine Mode checkbox toggle."""
        self._refresh_role_list()
        self._refresh_role_selector_combo()
        self._refresh_doctrine_list()
