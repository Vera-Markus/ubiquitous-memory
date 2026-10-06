import threading
import tkinter as tk
import tkinter.simpledialog as simpledialog
from tkinter import messagebox, ttk

from app.gui.dialogs.doctrine_metadata_dialog import DoctrineMetadataDialog
from app.gui.dialogs.escape_ship_chooser import EscapeShipChooser
from app.loaders.fitting_validator import fittingValidator
from app.loaders.role_manager import fitting_in_use
from app.paths import EVE_DB_PATH
from app.services.doctrine_metadata_form import bay_list_text, metadata_bays, prompt_kind
from app.services.fitting_display_formatter import FittingDisplayFormatter
from app.services.fitting_tree_service import FittingTreeService, fitting_label
from app.services.shopping_list_service import fitting_items, items_text
from app.gui import style as ui_style
from app.gui.style import DANGER_BUTTON


class FittingsTab:
    """
    Fittings tab (formerly Import): browse saved fittings in a Class → Hull → Fitting tree
    (UI rework step 6.3), rename and delete them. One box beside the tree shows the
    selected fitting's modules; Edit turns it into the fitting's EFT text to change and
    Save, and New Fitting empties it to paste a fit and Import.

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
        paned_window = tk.PanedWindow(self.frame, orient=tk.HORIZONTAL)
        paned_window.pack(fill=tk.BOTH, expand=True)

        left_frame = ttk.Frame(paned_window)
        right_frame = ttk.Frame(paned_window)
        paned_window.add(left_frame)
        paned_window.add(right_frame)

        # Fitting List (Navigation)
        self.fitting_list_container = ttk.Frame(left_frame)
        self.fitting_list_container.pack(pady=5, padx=10, fill=tk.BOTH, expand=True)

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
        fitting_buttons = ttk.Frame(self.fitting_list_container)
        fitting_buttons.pack(pady=5)
        self.btn_rename_fitting = ttk.Button(
            fitting_buttons,
            text="Rename Selected fitting",
            command=self._handle_rename_fitting
        )
        self.btn_rename_fitting.pack(side=tk.LEFT, padx=5)
        self.btn_delete_fitting = ttk.Button(
            fitting_buttons,
            text="Delete Selected fitting",
            command=self._handle_delete_fitting,
            style=DANGER_BUTTON
        )
        self.btn_delete_fitting.pack(side=tk.LEFT, padx=5)

        self.fitting_menu = tk.Menu(self.fitting_tree, tearoff=0)
        self.fitting_menu.add_command(label="Rename…", command=self._handle_rename_fitting)
        self.fitting_menu.add_command(label="Delete…", command=self._handle_delete_fitting)
        self.fitting_menu.add_separator()
        self.fitting_menu.add_command(label="Copy-Multibuy", command=self._handle_copy_multibuy)
        self.fitting_tree.bind("<Button-3>", self._on_fitting_right_click)

        # --- Right Frame: the fitting box ---
        self.fit_title = ttk.Label(right_frame, font=(ui_style.FONT_FAMILY, 12, "bold"))
        self.fit_title.pack(pady=10)

        # _set_mode packs the buttons each mode uses into this row
        self.fit_toolbar = ttk.Frame(right_frame)
        self.fit_toolbar.pack(padx=10, fill=tk.X)
        self.btn_new_fitting = ttk.Button(self.fit_toolbar, text="New Fitting", command=self._handle_new_fitting)
        self.btn_edit_fitting = ttk.Button(self.fit_toolbar, text="Edit", command=self._handle_edit_fitting,
                                           state=tk.DISABLED)
        # Enabled when the selected fitting's hull has a bay that takes doctrine requirements
        self.btn_edit_requirements = ttk.Button(self.fit_toolbar, text="Edit Doctrine Requirements",
                                                command=self._handle_edit_requirements, state=tk.DISABLED)
        self.btn_replace_fitting = ttk.Button(self.fit_toolbar, text="Save", command=self._handle_replace_fitting)
        self.btn_import = ttk.Button(self.fit_toolbar, text="Import fitting", command=self._handle_import)
        self.shared_doctrine_check = ttk.Checkbutton(self.fit_toolbar, text="Shared Doctrine Fitting",
                                                     variable=self.shared_doctrine_var)
        self.btn_cancel_edit = ttk.Button(self.fit_toolbar, text="Cancel", command=self._handle_cancel_edit)
        self._mode_widgets = {
            "view": (self.btn_new_fitting, self.btn_edit_fitting, self.btn_edit_requirements),
            "edit": (self.btn_replace_fitting, self.btn_cancel_edit),
            "new": (self.btn_import, self.btn_cancel_edit, self.shared_doctrine_check),
        }

        self.fit_text_area = ui_style.ScrolledText(right_frame, state='disabled')
        self.fit_text_area.pack(pady=5, padx=10, fill=tk.BOTH, expand=True)

        self._set_mode("view")
        # Initial load of the listbox
        self._refresh_fitting_list()

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
            self.fit_text_area.focus_set()
            self._update_edit_requirements_button()
        else:
            self._editing_uid = None
            self.fit_title.config(text="Fitting Details")
            self._on_fitting_selected(None)

    def _show_text(self, text, editable=False):
        self.fit_text_area.configure(state='normal')
        self.fit_text_area.delete("1.0", tk.END)
        self.fit_text_area.insert(tk.END, text)
        self.fit_text_area.configure(state='normal' if editable else 'disabled')

    def _handle_new_fitting(self):
        self._editing_uid = None
        self._set_mode("new")

    def _handle_edit_fitting(self):
        """Turns the selected fitting's details into its EFT text, to change and Save."""
        fitting = self._selected_fitting()
        if not fitting:
            messagebox.showwarning("No Selection", "Please select a fitting to edit.")
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
        """The fitting as the game's Multibuy takes it: the hull and every item, one "Name x2" line each."""
        return items_text(fitting_items(fitting))

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
        confirm = messagebox.askyesno("Confirm Deletion", message)

        if confirm:
            try:
                success = self.fitting_manager.delete_fitting(fit_uid)
                if success:
                    removed = self.role_manager.remove_requirements_for_fittings([fit_uid])
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

    def _refresh_fitting_list(self):
        """Rebuilds the Class → Hull → Fitting tree. Classes and hulls keep their open/closed state."""
        self._log("[INFO] Refreshing fitting list...")
        try:
            fittings = self.fitting_manager.list_fittings()
            if self._fitting_tree_service is None:
                self._fitting_tree_service = FittingTreeService(self.evedb_loader)
            groups = self._fitting_tree_service.group(fittings)

            tree = self.fitting_tree
            known = {row: bool(tree.item(row, "open")) for row in self._tree_rows()}
            tree.delete(*tree.get_children())
            for class_name, hulls in groups:
                class_row = f"class:{class_name}"
                tree.insert("", tk.END, iid=class_row, text=class_name, open=known.get(class_row, True))
                for hull, records in hulls:
                    hull_row = f"hull:{class_name}:{hull}"
                    tree.insert(class_row, tk.END, iid=hull_row, text=f"{hull} ({len(records)})",
                                open=known.get(hull_row, True))
                    for record in records:
                        tree.insert(hull_row, tk.END, iid=f"fit:{record['fit_uid']}", text=fitting_label(record))

            self._update_edit_requirements_button()
            self._log(f"[SUCCESS] Refreshed fitting list ({len(fittings)} items).")
        except Exception as e:
            self._log(f"[ERROR] Failed to refresh fitting list: {e}")
            messagebox.showerror("Error", f"Failed to refresh fitting list: {e}")

    def _on_fitting_selected(self, event):
        """Shows the selected fitting's details. While editing, the box keeps the text being edited."""
        self._update_edit_requirements_button()
        if self.mode != "view":
            return
        fitting = self._selected_fitting()
        if not fitting:
            self._show_text("")
            return

        try:
            # Slot contents live under the record's "fit" key.
            self._show_text(FittingDisplayFormatter.format(fitting.get("fit", fitting)))
        except Exception as e:
            self._log(f"[ERROR] Failed to display fitting details: {e}")
            messagebox.showerror("Error", f"Failed to display fitting details: {e}")

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
        try:
            db_path = EVE_DB_PATH
            # A replacement stays in the fitting's own UID namespace; the
            # "Shared Doctrine Fitting" box only applies to new imports.
            existing = self.fitting_manager.get_fitting(fit_uid)
            shared_doctrine = existing.get("source") == "doctrine" if existing else self.shared_doctrine_var.get()
            replacement_succeeded = self.fitting_manager.import_fit(
                fit_text,
                shared_doctrine=shared_doctrine,
                operation="replace",
                fit_uid=fit_uid,
                db_path=str(db_path)
            )
            if replacement_succeeded:
                updated_fitting = self.fitting_manager.get_fitting(fit_uid)
                self._log(f"[SUCCESS] Replaced fitting UID {fit_uid}.")
                self.root.after(0, lambda: self._finish_saving(updated_fitting))
                self.root.after(150, lambda: self._offer_metadata_editor(fit_uid, replaced=True))
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
        """Shows a parsed fit in the fitting box. Safe to call from a worker thread."""
        text = FittingDisplayFormatter.format(fit)
        unresolved = fit.get("unresolved") or []
        if unresolved:
            text += "\n\nNot found in the EVE database (left out of the fit):\n" + "\n".join(f"- {name}" for name in unresolved)
        self.root.after(0, lambda: self._show_text(text))

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
            args=(fit_text,),
            daemon=True
        ).start()

    def _execute_fit_import(self, fit_text):
        try:
            db_path = EVE_DB_PATH
            new_fitting = self.fitting_manager.import_fit(
                fit_text,
                shared_doctrine=self.shared_doctrine_var.get(),
                operation="new",
                db_path=str(db_path)
            )
            if new_fitting:
                self._log(f"[SUCCESS] Imported fitting: {new_fitting['hull']} - {new_fitting['fit_name']}")
                self.root.after(0, lambda: self._finish_saving(new_fitting))
                self.root.after(150, lambda: self._offer_metadata_editor(new_fitting["fit_uid"]))
            else:
                self._log("[ERROR] Failed to import fitting.")
        except Exception as e:
            self._log(f"[ERROR] Import failed: {str(e)}")
        finally:
            self.btn_import.config(state=tk.NORMAL)

    # --- Doctrine requirements (design §6.2, §7) -----------------------------

    def _update_edit_requirements_button(self):
        """
        Edit Requirements needs a fitting whose hull has requirement bays; Edit, Rename
        and Delete need a fitting row. Rename and Delete wait while the box is being edited.
        """
        fitting = self._selected_fitting()
        has_bays = bool(fitting) and bool(metadata_bays(fitting, self.evedb_loader))
        self.btn_edit_requirements.config(state=tk.NORMAL if has_bays else tk.DISABLED)
        self.btn_edit_fitting.config(state=tk.NORMAL if fitting else tk.DISABLED)
        idle = fitting and self.mode == "view"
        self.btn_delete_fitting.config(state=tk.NORMAL if idle else tk.DISABLED)
        self.btn_rename_fitting.config(state=tk.NORMAL if idle else tk.DISABLED)

    def _tree_rows(self):
        """Every class and hull row in the fitting tree."""
        return [row for class_row in self.fitting_tree.get_children()
                for row in (class_row, *self.fitting_tree.get_children(class_row))]

    def _handle_edit_requirements(self):
        fitting = self._selected_fitting()
        if not fitting:
            messagebox.showwarning("No Selection", "Please select a fitting to edit its doctrine requirements.")
            return
        self._open_metadata_editor(fitting)

    def _open_metadata_editor(self, fitting):
        self.metadata_dialog = DoctrineMetadataDialog(self.app, fitting, on_saved=self._refresh_details)

    def _open_escape_chooser(self, fitting):
        self.metadata_dialog = EscapeShipChooser(self.app, fitting, on_saved=self._refresh_details)

    def _refresh_details(self):
        self._on_fitting_selected(None)

    def _offer_metadata_editor(self, fit_uid, replaced=False):
        """
        After an import, offer the requirements editor when the hull has a metadata
        bay (§6.2): the escape ship chooser when the escape bay is its only one,
        otherwise a prompt for the full editor. A Devoter is never prompted. After a
        replace, only fittings without requirements yet are prompted.
        """
        fitting = self.fitting_manager.get_fitting(fit_uid)
        if not fitting:
            return
        kind = prompt_kind(fitting, self.evedb_loader)
        if kind is None:
            return
        if replaced and not self.fitting_manager.get_metadata(fit_uid).is_empty():
            return
        if kind == "escape":
            self._open_escape_chooser(fitting)
            return
        bays = bay_list_text(metadata_bays(fitting, self.evedb_loader))
        if messagebox.askyesno("Doctrine Requirements",
                               f"This {fitting['hull']} has {bays}.\n\nAdd doctrine requirements now?"):
            self._open_metadata_editor(fitting)
