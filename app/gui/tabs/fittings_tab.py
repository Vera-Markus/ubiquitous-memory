import threading
import tkinter as tk
from app.gui import themed_dialogs as simpledialog
from tkinter import ttk
from app.gui import themed_dialogs as messagebox

from app.gui.loadout_view import LoadoutView
from app.loaders.fitting_loader import parse_fit
from app.loaders.fitting_validator import fittingValidator
from app.loaders.role_manager import fitting_in_use
from app.paths import EVE_DB_PATH
from app.services.doctrine_metadata_form import bay_list_text, metadata_bays, prompt_kind
from app.services.fitting_display_formatter import FittingDisplayFormatter
from app.services.fitting_tree_service import FittingTreeService, filter_groups, fitting_label
from app.services.shopping_list_service import fitting_items, items_text
from app.gui import style as ui_style
from app.services import esi_features
from app.gui.style import DANGER_BUTTON


LIST_WIDTH = 280      # the fitting list: 20% of the 1400 px window, not draggable (1.7.2 plan, 36.3.1)


class FittingsTab:
    """
    Fittings tab (formerly Import): browse saved fittings in a Class → Hull → Fitting tree
    (UI rework step 6.3), rename and delete them. Beside the tree (locked at 20% of the
    window, 1.7.2 plan 36.3), two tabs: **Loadout**, the selected fitting laid out with its
    doctrine requirements edited in place (LoadoutView), and **EFT text**. Edit turns the
    text into the fitting's EFT to change and Save; New Fitting empties it to paste a fit
    and Import.

    Extracted from EVEFleetGUI (step 2.4). Shared managers, the root window
    and the Library tab are owned by the app and read through it. Step 2.4
    added the Doctrine Requirements editor and the prompt after an import.
    """

    def __init__(self, app, frame):
        self.app = app
        self.frame = frame

        # Import-only state
        self.shared_doctrine_var = tk.BooleanVar(value=False)
        self._fitting_tree_service = None     # built on first refresh (needs the SDE loader)
        self.metadata_dialog = None     # the editor or escape chooser last opened
        self.mode = "view"              # the fitting box: "view", "edit" or "new" (see _set_mode)
        self._editing_uid = None        # the fitting Save replaces, in edit mode
        self._edit_start_text = ""      # what the box held when editing began (Cancel asks if it changed)
        self.search_var = tk.StringVar()    # filters the tree by class, hull or fitting name
        self._open_rows = {}            # class and hull rows' open state, kept while a search opens them all
        self._tree_filtered = False     # the tree shows search results (so its open state isn't the user's)

        self._setup_fittings_tab()

    # --- Shared state owned by EVEFleetGUI ---------------------------------

    @property
    def root(self):
        return self.app.root

    @property
    def fitting_manager(self):
        return self.app.fitting_manager

    @property
    def library_view(self):
        return self.app.library_view

    @property
    def role_manager(self):
        return self.app.role_manager

    @property
    def evedb_loader(self):
        return self.app.evedb_loader

    def _log(self, message: str):
        self.app._log(message)


    def _setup_fittings_tab(self):
        # Left: the fitting tree. Right: one box that shows the selected fitting's details,
        # or EFT text to edit (Edit) or paste (New Fitting).
        # The list is locked at 20% of the window (1.7.2 plan, 36.3.1): the window is a fixed 1400 px.
        left_frame = ttk.Frame(self.frame, width=LIST_WIDTH)
        left_frame.pack(side=tk.LEFT, fill=tk.Y)
        left_frame.pack_propagate(False)
        right_frame = ttk.Frame(self.frame)
        right_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Fitting List (Navigation)
        self.fitting_list_container = ttk.Frame(left_frame)
        self.fitting_list_container.pack(pady=5, padx=10, fill=tk.BOTH, expand=True)

        # Search: filters the tree by ship class, hull or fitting name as you type
        search_frame = ttk.Frame(self.fitting_list_container)
        search_frame.pack(fill=tk.X, pady=(0, 5))
        ttk.Label(search_frame, text="Search:").pack(side=tk.LEFT)
        self.search_entry = ttk.Entry(search_frame, textvariable=self.search_var)
        self.search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        self.search_entry.bind("<Escape>", lambda _event: self.search_var.set(""))
        ttk.Button(search_frame, text="Clear", command=lambda: self.search_var.set("")).pack(side=tk.LEFT)
        self.search_var.trace_add("write", lambda *_args: self._refresh_fitting_list(quiet=True))

        # Class → Hull → Fitting (UI rework step 6.3). Row IDs: "class:<class>",
        # "hull:<class>:<hull>", "fit:<fit_uid>".
        tree_frame = ttk.Frame(self.fitting_list_container)
        tree_frame.pack(fill=tk.BOTH, expand=True)
        self.fitting_tree = ttk.Treeview(tree_frame, show="tree", selectmode="browse")
        self.fitting_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.fitting_tree.bind("<<TreeviewSelect>>", self._on_fitting_selected)

        scrollbar = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.fitting_tree.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.fitting_tree.config(yscrollcommand=scrollbar.set)

        # Rename and Delete act on the selected fitting row (also on its right-click menu)
        # Two even columns, so they fit the list's fixed 280 px (1.7.2 plan, 36.3.1).
        fitting_buttons = ttk.Frame(self.fitting_list_container)
        fitting_buttons.pack(fill=tk.X, pady=5)
        fitting_buttons.columnconfigure((0, 1), weight=1, uniform="buttons")
        self.btn_rename_fitting = ttk.Button(fitting_buttons, text="Rename…", command=self._handle_rename_fitting)
        self.btn_rename_fitting.grid(row=0, column=0, sticky="ew", padx=(0, 3))
        self.btn_delete_fitting = ttk.Button(fitting_buttons, text="Delete…", command=self._handle_delete_fitting,
                                             style=DANGER_BUTTON)
        self.btn_delete_fitting.grid(row=0, column=1, sticky="ew", padx=(3, 0))

        # In-game fitting sync (ESI features plan 29.2, 29.5): shown while Options ▸ Fitting sync is on.
        # One per row: side by side, their names don't fit the list's width.
        self.game_buttons = ttk.Frame(self.fitting_list_container)
        self.btn_import_from_game = ttk.Button(self.game_buttons, text="Import from Game…",
                                               command=self._handle_import_from_game)
        self.btn_import_from_game.pack(fill=tk.X)
        self.btn_deleted_from_game = ttk.Button(self.game_buttons, text="Deleted from Game…",
                                                command=self._handle_deleted_from_game)
        self.btn_deleted_from_game.pack(fill=tk.X, pady=(5, 0))
        self.show_game_buttons()

        self.fitting_menu = tk.Menu(self.fitting_tree, tearoff=0)
        self.fitting_menu.add_command(label="Rename…", command=self._handle_rename_fitting)
        self.fitting_menu.add_command(label="Delete…", command=self._handle_delete_fitting)
        self.fitting_menu.add_separator()
        self.fitting_menu.add_command(label="Copy-Multibuy", command=self._handle_copy_multibuy)
        self.fitting_tree.bind("<Button-3>", self._on_fitting_right_click)

        # --- Right Frame: the title and buttons on one row, then Loadout | EFT text (36.3.2) ---
        header = ttk.Frame(right_frame)
        header.pack(fill=tk.X, padx=10, pady=(8, 0))
        self.fit_title = ttk.Label(header, font=(ui_style.FONT_FAMILY, 12, "bold"))
        self.fit_title.pack(side=tk.LEFT)

        # _set_mode packs the buttons each mode uses into this row
        self.fit_toolbar = ttk.Frame(header)
        self.fit_toolbar.pack(side=tk.RIGHT)
        self.btn_new_fitting = ttk.Button(self.fit_toolbar, text="New Fitting", command=self._handle_new_fitting)
        self.btn_edit_fitting = ttk.Button(self.fit_toolbar, text="Edit", command=self._handle_edit_fitting,
                                           state=tk.DISABLED)
        self.btn_replace_fitting = ttk.Button(self.fit_toolbar, text="Save", command=self._handle_replace_fitting)
        self.btn_import = ttk.Button(self.fit_toolbar, text="Import fitting", command=self._handle_import)
        self.shared_doctrine_check = ttk.Checkbutton(self.fit_toolbar, text="Shared Doctrine Fitting",
                                                     variable=self.shared_doctrine_var)
        self.btn_cancel_edit = ttk.Button(self.fit_toolbar, text="Cancel", command=self._handle_cancel_edit)
        self._mode_widgets = {
            "view": (self.btn_new_fitting, self.btn_edit_fitting),
            "edit": (self.btn_replace_fitting, self.btn_cancel_edit),
            "new": (self.btn_import, self.btn_cancel_edit, self.shared_doctrine_check),
        }

        self.fit_view = ttk.Notebook(right_frame)
        self.fit_view.pack(fill=tk.BOTH, expand=True, padx=10, pady=(6, 10))
        self.loadout = LoadoutView(self.fit_view, self.app)
        self.fit_view.add(self.loadout.frame, text="Loadout")
        text_tab = ttk.Frame(self.fit_view, padding=(4, 4))
        self.fit_view.add(text_tab, text="EFT text")
        self.fit_text_area = ui_style.ScrolledText(text_tab, state='disabled')
        self.fit_text_area.pack(fill=tk.BOTH, expand=True)
        self._shown_uid = None              # the fitting the Loadout shows (to go back to on Cancel)
        self._reselecting = False

        self._set_mode("view")
        # Initial load of the listbox
        self._refresh_fitting_list()

    # --- In-game fitting sync (ESI features plan 29) --------------------------

    def show_game_buttons(self):
        """Import from Game… and Deleted from Game… while Fitting sync is on (Options)."""
        if esi_features.enabled("fittings"):
            self.game_buttons.pack(fill=tk.X, pady=(0, 5))
        else:
            self.game_buttons.pack_forget()

    def _handle_import_from_game(self):
        from app.gui.dialogs.game_fittings_dialogs import ImportFromGameDialog
        if self.app._guard("Import from Game"):
            self.game_dialog = ImportFromGameDialog(self.app)

    def _handle_deleted_from_game(self):
        from app.gui.dialogs.game_fittings_dialogs import DeletedFromGameDialog
        self.game_dialog = DeletedFromGameDialog(self.app)

    # --- The fitting box: view, edit and new ---------------------------------

    def _set_mode(self, mode, text=""):
        """
        "view": the selected fitting's details, read-only. "edit": its EFT text, to change
        and Save. "new": an empty box to paste a fit into and Import. While editing, the
        tree is locked so a click can't swap the fitting being edited.
        """
        self.mode = mode
        editing = mode != "view"
        for widget in self.fit_toolbar.winfo_children():
            widget.pack_forget()
        for widget in self._mode_widgets[mode]:
            widget.pack(side=tk.LEFT, padx=5, pady=(0, 5))
        self.fitting_tree.config(selectmode="none" if editing else "browse")
        if editing:
            self._edit_start_text = text
            self.fit_title.config(text="New Fitting: paste EFT text" if mode == "new" else "Editing Fitting (EFT)")
            self._show_text(text, editable=True)
            self.fit_view.tab(0, state="disabled")          # the text is what's being worked on
            self.fit_view.select(1)
            self.fit_text_area.focus_set()
            self._update_buttons()
        else:
            self._editing_uid = None
            self.fit_view.tab(0, state="normal")
            self.fit_view.select(0)
            self._shown_uid = None                          # draw the Loadout again
            self._on_fitting_selected(None)

    def _show_text(self, text, editable=False):
        self.fit_text_area.configure(state='normal')
        self.fit_text_area.delete("1.0", tk.END)
        self.fit_text_area.insert(tk.END, text)
        self.fit_text_area.configure(state='normal' if editable else 'disabled')

    def _handle_new_fitting(self):
        if not self._requirements_settled():
            return
        self._editing_uid = None
        self._set_mode("new")

    def _handle_edit_fitting(self):
        """Turns the selected fitting's details into its EFT text, to change and Save."""
        fitting = self._selected_fitting()
        if not fitting:
            messagebox.showwarning("No Selection", "Please select a fitting to edit.")
            return
        if not self._requirements_settled():
            return
        self._set_mode("edit", FittingDisplayFormatter.eft(fitting))
        self._editing_uid = fitting["fit_uid"]

    def _handle_cancel_edit(self):
        """Back to the details, asking first when the text was changed."""
        changed = self.fit_text_area.get("1.0", tk.END).strip() != self._edit_start_text.strip()
        if changed and not messagebox.askyesno("Discard Changes", "Discard the changes to this fitting?"):
            return
        self._set_mode("view")

    def _selected_fit_uid(self):
        """The selected fitting's UID, or None when nothing (or a class or hull row) is selected."""
        selection = self.fitting_tree.selection()
        if not selection or not selection[0].startswith("fit:"):
            return None
        return int(selection[0][len("fit:"):])

    def _selected_fitting(self):
        uid = self._selected_fit_uid()
        return self.fitting_manager.get_fitting(uid) if uid is not None else None

    def select_fitting(self, fit_uid):
        """Selects a fitting's row, opening its class and hull."""
        row = f"fit:{fit_uid}"
        if not self.fitting_tree.exists(row) and self.search_var.get().strip():
            self.search_var.set("")                     # hidden by the search: show everything again
        if not self.fitting_tree.exists(row):
            return
        parent = self.fitting_tree.parent(row)
        while parent:
            self.fitting_tree.item(parent, open=True)
            parent = self.fitting_tree.parent(parent)
        self.fitting_tree.selection_set(row)
        self.fitting_tree.see(row)
        self._on_fitting_selected(None)

    def fitting_names(self):
        """Every fitting's name, in the order the tree shows them."""
        return [self.fitting_tree.item(fit, "text")
                for class_row in self.fitting_tree.get_children()
                for hull in self.fitting_tree.get_children(class_row)
                for fit in self.fitting_tree.get_children(hull)]

    def _on_fitting_right_click(self, event):
        """Right-click a fitting row: select it and offer Rename, Delete and Copy-Multibuy."""
        row = self.fitting_tree.identify_row(event.y)
        if not row or not row.startswith("fit:") or self.mode != "view":
            return
        self.fitting_tree.selection_set(row)
        self._on_fitting_selected(None)
        self.fitting_menu.post(event.x_root, event.y_root)

    def multibuy_text(self, fitting) -> str:
        """The fitting as the game's Multibuy takes it: the hull and every item, one "Name x2" line each.
        Mutated modules are left out (they can't be bought on the market, M4)."""
        return items_text(fitting_items(fitting, self.app.audit_engine.rules.is_mutated))

    def _handle_copy_multibuy(self):
        """Copies the selected fitting's hull and items to the clipboard, ready for Multibuy."""
        fitting = self._selected_fitting()
        if not fitting:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(self.multibuy_text(fitting))
        self._log(f"[INFO] {fitting.get('fit_name', 'Fitting')} copied for Multibuy.")

    def _handle_rename_fitting(self):
        """
        Renames the selected fitting (UI rework step 6.4). The new name is used
        everywhere: the saved record, its EFT header, the Library's requirements
        and fit lists, and exports.
        """
        fitting = self._selected_fitting()
        if not fitting:
            messagebox.showwarning("No Selection", "Please select a fitting to rename.")
            return
        old_name = fitting_label(fitting)
        new_name = simpledialog.askstring("Rename Fitting", f"New name for \"{old_name}\":",
                                          initialvalue=old_name, parent=self.root)
        if new_name is None or new_name.strip() == old_name:
            return
        try:
            renamed = self.fitting_manager.rename_fitting(fitting["fit_uid"], new_name)
        except (KeyError, ValueError) as e:
            messagebox.showerror("Rename Fitting", str(e))
            return
        self._log(f"[INFO] Renamed fitting \"{old_name}\" to \"{renamed['fit_name']}\".")
        self.app._refresh_all_tabs()                          # includes this tree
        self._refresh_library_fittings()
        self.select_fitting(renamed["fit_uid"])

    def _handle_delete_fitting(self):
        """Handles the deletion of a selected fitting with confirmation."""
        fitting = self._selected_fitting()
        if not fitting:
            messagebox.showwarning("No Selection", "Please select a fitting to delete.")
            return

        fitting_name = fitting_label(fitting)
        
        # Confirmation Dialog, naming any fittings that recommend this one as their escape ship
        message = f"Delete fitting?\n\nName:\n{fitting_name}"
        references = self.fitting_manager.find_escape_references(fitting["fit_uid"])
        if references:
            names = [self.fitting_manager.get_fitting(uid)["fit_name"] for uid in references]
            message += (f"\n\nUsed as the escape ship by {len(references)} fitting(s):\n" + "\n".join(names) +
                        "\n\nTheir audits will warn until a new escape ship is chosen.")
        carriers = self.fitting_manager.find_carried_references(fitting["fit_uid"])
        if carriers:
            names = [self.fitting_manager.get_fitting(uid)["fit_name"] for uid in carriers]
            message += (f"\n\nCarried in the Ship Maintenance Bay of {len(carriers)} fitting(s):\n" + "\n".join(names) +
                        "\n\nThose entries will then accept any fitting of the hull.")
        # Roles that use it: their requirements for it are removed with it, so the Library never
        # shows a missing fitting; where it's a pilot's replacement, the original applies again.
        fit_uid = fitting["fit_uid"]
        required, replacing = [], []
        for role in sorted(self.role_manager.list_roles(), key=lambda r: r["role_name"].casefold()):
            for requirement in role.get("requirements", []):
                if requirement.get("fit_uid") == fit_uid:
                    required.append(role["role_name"])
                elif fitting_in_use(requirement) == fit_uid:
                    replacing.append(role["role_name"])
        if required:
            message += (f"\n\nRequired by {len(required)} role requirement(s), which will be removed:\n"
                        + "\n".join(dict.fromkeys(required)))
        if replacing:
            message += (f"\n\nA replacement in {len(replacing)} role requirement(s), which will go back to "
                        f"their original fitting:\n" + "\n".join(dict.fromkeys(replacing)))
        designations = getattr(self.app, "ship_designations", None)
        designated = [d for d in (designations.designations.values() if designations else []) if d["fit_uid"] == fit_uid]
        if designated:
            message += f"\n\n{len(designated)} ship(s) assigned this fitting in the Ships tab will lose it."
        confirm = messagebox.askyesno("Confirm Deletion", message)

        if confirm:
            try:
                success = self.fitting_manager.delete_fitting(fit_uid)
                if success:
                    removed = self.role_manager.remove_requirements_for_fittings([fit_uid])
                    if designations is not None:
                        designations.prune(f["fit_uid"] for f in self.fitting_manager.list_fittings())
                    if removed or replacing:
                        self._log(f"[INFO] Removed {removed} requirement(s) and {len(replacing)} replacement(s) "
                                  f"for {fitting_name}")
                    self._refresh_fitting_list()
                    self._refresh_library_fittings()
                    self._log(f"[SUCCESS] Deleted fitting: {fitting_name}")
                    messagebox.showinfo("Success", f"fitting '{fitting_name}' has been removed.")
                else:
                    self._log(f"[ERROR] Failed to delete fitting: {fitting_name}")
                    messagebox.showerror("Error", f"Could not delete fitting: {fitting_name}")
            except Exception as e:
                self._log(f"[ERROR] Error during deletion: {str(e)}")
                messagebox.showerror("Error", f"An error occurred: {str(e)}")

    def _refresh_library_fittings(self):
        """The Library's Hull and Fit dropdowns and its requirement list, after a fitting is added, replaced, renamed or deleted."""
        self.library_view._populate_library_hulls()           # keeps the chosen hull while it still has fittings
        self.library_view._refresh_library_requirement_list()

    def _refresh_fitting_list(self, quiet=False):
        """
        Rebuilds the Class → Hull → Fitting tree, showing only what matches the search.
        Classes and hulls start collapsed and keep their open/closed state; while searching
        they're all open, and clearing the search puts them back as they were. A fitting
        selected (after an import, say) opens its class and hull (Treeview.see). quiet: no log lines (typing).
        """
        if not quiet:
            self._log("[INFO] Refreshing fitting list...")
        try:
            fittings = self.fitting_manager.list_fittings()
            if self._fitting_tree_service is None:
                self._fitting_tree_service = FittingTreeService(self.evedb_loader)
            query = self.search_var.get().strip()
            groups = filter_groups(self._fitting_tree_service.group(fittings), query)

            tree = self.fitting_tree
            if not self._tree_filtered:
                self._open_rows.update({row: bool(tree.item(row, "open")) for row in self._tree_rows()})
            self._tree_filtered = bool(query)
            selected = tree.selection()
            tree.delete(*tree.get_children())
            for class_name, hulls in groups:
                class_row = f"class:{class_name}"
                tree.insert("", tk.END, iid=class_row, text=class_name,
                            open=bool(query) or self._open_rows.get(class_row, False))     # collapsed at first
                for hull, records in hulls:
                    hull_row = f"hull:{class_name}:{hull}"
                    tree.insert(class_row, tk.END, iid=hull_row, text=f"{hull} ({len(records)})",
                                open=bool(query) or self._open_rows.get(hull_row, False))
                    for record in records:
                        tree.insert(hull_row, tk.END, iid=f"fit:{record['fit_uid']}", text=fitting_label(record))

            kept = [row for row in selected if tree.exists(row)]
            if kept:
                tree.selection_set(kept)
                tree.see(kept[0])
            self._update_buttons()
            if not quiet:
                self._log(f"[SUCCESS] Refreshed fitting list ({len(fittings)} items).")
        except Exception as e:
            self._log(f"[ERROR] Failed to refresh fitting list: {e}")
            messagebox.showerror("Error", f"Failed to refresh fitting list: {e}")

    def _on_fitting_selected(self, event):
        """
        Shows the selected fitting: its Loadout and its EFT text. While editing, the box keeps
        the text being edited. Unsaved requirement changes are asked about first (Q36.1).
        """
        if self._reselecting:
            return
        if self.mode != "view":
            self._update_buttons()
            return
        fitting = self._selected_fitting()
        uid = fitting["fit_uid"] if fitting else None
        if uid == self._shown_uid and self.loadout.fitting is not None:
            self._update_buttons()
            return
        if not self._requirements_settled():
            self._reselect(self._shown_uid)                 # Cancel: stay on the fitting being edited
            return
        self._update_buttons()
        self._shown_uid = uid
        self.fit_title.config(text=f"{fitting['fit_name']}  ·  {fitting['hull']}" if fitting else "Fitting Details")
        try:
            self.loadout.show(fitting)
            self._show_text(FittingDisplayFormatter.eft(fitting) if fitting else "")
        except Exception as e:
            self._log(f"[ERROR] Failed to display fitting details: {e}")
            messagebox.showerror("Error", f"Failed to display fitting details: {e}")

    def _requirements_settled(self) -> bool:
        """
        Unsaved requirement changes (Q36.1): Save, Discard or Cancel. True to go on; False to
        stay (Cancel, or a save that needs something fixed or a warning read first).
        """
        if not self.loadout.dirty():
            return True
        name = (self.loadout.fitting or {}).get("fit_name", "this fitting")
        answer = messagebox.askyesnocancel("Unsaved Requirements",
                                           f"Save the requirement changes to {name}?\n\n"
                                           "Yes saves them, No discards them, Cancel stays here.")
        if answer is None:
            return False
        if answer:
            return self.loadout.save()
        self._log(f"[INFO] Discarded the requirement changes to {name}.")
        self.loadout.show(None)
        return True

    def _reselect(self, fit_uid):
        """Puts the tree's selection back without redrawing the Loadout."""
        row = f"fit:{fit_uid}" if fit_uid is not None else None
        self._reselecting = True
        try:
            if row and self.fitting_tree.exists(row):
                self.fitting_tree.selection_set(row)
                self.fitting_tree.see(row)
            else:
                self.fitting_tree.selection_set(())
            self.fitting_tree.update_idletasks()
        finally:
            self.fitting_tree.after_idle(lambda: setattr(self, "_reselecting", False))

    def _handle_replace_fitting(self):
        """Save in edit mode: replaces the fitting being edited with the box's EFT text."""
        fit_uid = self._editing_uid
        if fit_uid is None:
            messagebox.showwarning("No Selection", "Please select a fitting and press Edit first.")
            return

        fit_text = self.fit_text_area.get("1.0", tk.END).strip()
        if not fit_text:
            self._log("[ERROR] No replacement fit text provided.")
            return

        validator = fittingValidator()
        result = validator.validate(fit_text)
        if not result.is_valid:
            self._show_import_errors(result.errors)
            return

        self.btn_replace_fitting.config(state=tk.DISABLED)
        threading.Thread(
            target=self._execute_replace,
            args=(fit_text, fit_uid),
            daemon=True
        ).start()

    def _execute_replace(self, fit_text, fit_uid):
        """Worker: parse the edited text, then ask on the Tk thread if it's the same as another fitting."""
        try:
            parsed = parse_fit(fit_text, str(EVE_DB_PATH))
            if not parsed:
                raise ValueError("Fit text did not produce a fitting record")
            duplicates = self.fitting_manager.find_duplicates(parsed, self._equivalence_key(), exclude_uid=fit_uid)
            self.root.after(0, lambda: self._save_replacement(parsed, fit_uid, duplicates))
        except Exception as error:
            self._log(f"[ERROR] Replacement failed: {error}")
            self.root.after(0, lambda error=error: messagebox.showerror(
                "Replace Failed", f"The fitting could not be replaced:\n\n{error}"))
            self.btn_replace_fitting.config(state=tk.NORMAL)

    def _equivalence_key(self):
        """Identical-stat twins count as the same item in the duplicate check, as in the audit."""
        rules = getattr(getattr(self.app, "audit_engine", None), "rules", None)
        return rules.equivalence_key if rules is not None else None

    def _duplicate_ok(self, duplicates, action: str) -> bool:
        """With an identical fitting saved already, ask before saving another (the user's choice, 2026-10-06)."""
        if not duplicates:
            return True
        names = "\n".join(f"• {fitting_label(d)} ({'shared doctrine' if d.get('source') == 'doctrine' else 'local'} "
                          f"fitting)" for d in duplicates)
        if messagebox.askyesno("Same Fitting Already Saved",
                               f"This fit is the same as:\n\n{names}\n\n(same hull, modules, drones, fighters and "
                               f"cargo). {action} anyway?"):
            self._log(f"[INFO] Saved although it matches {', '.join(d['fit_name'] for d in duplicates)}.")
            return True
        self._log(f"[INFO] Not saved: the same as {', '.join(d['fit_name'] for d in duplicates)}.")
        return False

    def _save_replacement(self, parsed, fit_uid, duplicates):
        """Tk thread: replace the fitting being edited, unless a duplicate was declined."""
        try:
            if not self._duplicate_ok(duplicates, "Save it"):
                return
            # A replacement stays in the fitting's own UID namespace; the
            # "Shared Doctrine Fitting" box only applies to new imports.
            existing = self.fitting_manager.get_fitting(fit_uid)
            old_version = int((existing or {}).get("version", 1))
            shared_doctrine = existing.get("source") == "doctrine" if existing else self.shared_doctrine_var.get()
            replacement_succeeded = self.fitting_manager.replace_fitting(
                fit_uid, parsed, source="doctrine" if shared_doctrine else "local")
            if replacement_succeeded:
                updated_fitting = self.fitting_manager.get_fitting(fit_uid)
                self._log(f"[SUCCESS] Replaced fitting UID {fit_uid}.")
                self.root.after(0, lambda: self._finish_saving(updated_fitting))
                self.root.after(150, lambda: self._point_to_requirements(fit_uid, replaced=True))
                if int(updated_fitting.get("version", 1)) > old_version:
                    # 29.4: pilots with an older copy saved in game are offered the update.
                    from app.gui.dialogs.game_fittings_dialogs import offer_update
                    self.root.after(300, lambda: offer_update(self.app, updated_fitting))
            else:
                self._log(f"[ERROR] Fitting UID {fit_uid} was not found.")
                self.root.after(0, lambda: messagebox.showerror(
                    "Replace Failed", f"The selected fitting (UID {fit_uid}) no longer exists."))
        except Exception as error:
            self._log(f"[ERROR] Replacement failed: {error}")
            self.root.after(0, lambda error=error: messagebox.showerror(
                "Replace Failed", f"The fitting could not be replaced:\n\n{error}"))
        finally:
            self.btn_replace_fitting.config(state=tk.NORMAL)

    def _finish_saving(self, fitting):
        """After an import or a Save: back to the details, with the fitting selected
        and any lines the parser couldn't find listed under it."""
        self._refresh_fitting_list()
        self._refresh_library_fittings()
        self._set_mode("view")
        self.select_fitting(fitting["fit_uid"])
        self._show_parser_result(fitting.get("fit", fitting))

    def _show_import_errors(self, errors):
        """Shows fit validation errors, worded as in _handle_import."""
        error_msg = "❌ Validation Errors:\n\n" + "\n".join(f"- {e}" for e in errors)
        self._log(f"[ERROR] Validation failed: {len(errors)} errors found.")
        messagebox.showerror("Validation Error", error_msg)

    def _show_parser_result(self, fit):
        """After an import or a Save: lines the parser couldn't find are listed (they were left out)."""
        unresolved = fit.get("unresolved") or []
        if unresolved:
            text = "Not found in the EVE database (left out of the fit):\n" + "\n".join(f"- {n}" for n in unresolved)
            self._log(f"[WARNING] {len(unresolved)} line(s) not found in the EVE database: {', '.join(unresolved)}")
            self.root.after(0, lambda: messagebox.showwarning("Left Out of the Fit", text))

    def _handle_import(self):
        fit_text = self.fit_text_area.get("1.0", tk.END).strip()
        if not fit_text:
            self._log("[ERROR] No text provided for import.")
            return

        # Validate fitting
        validator = fittingValidator()
        result = validator.validate(fit_text)

        if not result.is_valid:
            self._show_import_errors(result.errors)
            return

        if result.warnings:
            warning_msg = "⚠️ Validation Warnings:\n\n" + "\n".join(f"- {w}" for w in result.warnings)
            self._log(f"[WARNING] Validation warnings: {len(result.warnings)} warnings found.")
            messagebox.showwarning("Validation Warning", warning_msg)

        self.btn_import.config(state=tk.DISABLED)
        threading.Thread(
            target=self._execute_fit_import,
            args=(fit_text, bool(self.shared_doctrine_var.get())),      # the box as it was when Import was pressed
            daemon=True
        ).start()

    def _execute_fit_import(self, fit_text, shared_doctrine=False):
        """Worker: parse the pasted text, then ask on the Tk thread if it's the same as a saved fitting."""
        try:
            parsed = parse_fit(fit_text, str(EVE_DB_PATH))
            if not parsed:
                raise ValueError("Fit text did not produce a fitting record")
            duplicates = self.fitting_manager.find_duplicates(parsed, self._equivalence_key())
            self.root.after(0, lambda: self._save_import(parsed, duplicates, shared_doctrine))
        except Exception as e:
            self._log(f"[ERROR] Import failed: {str(e)}")
            self.btn_import.config(state=tk.NORMAL)

    def _save_import(self, parsed, duplicates, shared_doctrine=False):
        """Tk thread: save the new fitting, unless it duplicates one and the user declines."""
        try:
            if not self._duplicate_ok(duplicates, "Import it"):
                return
            new_fitting = self.fitting_manager.create_fitting(parsed, source="doctrine" if shared_doctrine else "local")
            if new_fitting:
                self._log(f"[SUCCESS] Imported fitting: {new_fitting['hull']} - {new_fitting['fit_name']}")
                self.root.after(0, lambda: self._finish_saving(new_fitting))
                self.root.after(150, lambda: self._point_to_requirements(new_fitting["fit_uid"]))
            else:
                self._log("[ERROR] Failed to import fitting.")
        except Exception as e:
            self._log(f"[ERROR] Import failed: {str(e)}")
        finally:
            self.btn_import.config(state=tk.NORMAL)

    # --- Doctrine requirements (design §6.2, §7): in the Loadout view since 1.7.2 -------------

    def _update_buttons(self):
        """Edit, Rename and Delete need a fitting row; Rename and Delete wait while the box is being edited."""
        fitting = self._selected_fitting()
        self.btn_edit_fitting.config(state=tk.NORMAL if fitting else tk.DISABLED)
        idle = fitting and self.mode == "view"
        self.btn_delete_fitting.config(state=tk.NORMAL if idle else tk.DISABLED)
        self.btn_rename_fitting.config(state=tk.NORMAL if idle else tk.DISABLED)

    def _tree_rows(self):
        """Every class and hull row in the fitting tree."""
        return [row for class_row in self.fitting_tree.get_children()
                for row in (class_row, *self.fitting_tree.get_children(class_row))]

    def _point_to_requirements(self, fit_uid, replaced=False):
        """
        After an import (§6.2): a hull with requirement bays says so in its Loadout, which is
        already showing. A Devoter says nothing; after a replace, only fittings without
        requirements yet are pointed at them.
        """
        fitting = self.fitting_manager.get_fitting(fit_uid)
        if not fitting or self.loadout.fitting is None or self.loadout.fitting.get("fit_uid") != fit_uid:
            return
        kind = prompt_kind(fitting, self.evedb_loader)
        if kind is None or (replaced and not self.fitting_manager.get_metadata(fit_uid).is_empty()):
            return
        if kind == "escape":
            text = f"This {fitting['hull']} has an escape bay: choose its escape ship below, then Save Requirements."
        else:
            bays = bay_list_text(metadata_bays(fitting, self.evedb_loader))
            text = f"This {fitting['hull']} has {bays}: set its doctrine requirements here, then Save Requirements."
        self.loadout.show_message([text])
