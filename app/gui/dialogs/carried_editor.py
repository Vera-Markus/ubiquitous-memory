"""
The requirements editor's Ship Maintenance Bay (UI thoughts plan 20.2, U10): a list of the
ships the carrier should hold, each a hull with any fitting or a saved fitting of it.
+ opens a chooser (hulls that fit in the bay and have a saved fitting, filtered as you
type, with a quantity); × removes the selected entry.
"""
import tkinter as tk
from tkinter import ttk
from typing import Any, List, Optional

from app.gui import style as ui_style
from app.models.doctrine_metadata import BayRequirement
from app.services.doctrine_metadata_form import (CarriedOption, carried_label, carried_options, carried_requirement,
                                                 carried_warnings)
from app.gui.window_placement import place_window


class CarriedEditor:
    def __init__(self, parent, fitting: dict, saved: List[BayRequirement], capacity: float, app: Any):
        self.app = app
        self.fitting = fitting
        self.capacity = capacity
        self.entries: List[BayRequirement] = [BayRequirement(**vars(r)) for r in saved]
        self.options: List[CarriedOption] = carried_options(fitting, app.fitting_manager, capacity, app.evedb_loader)
        self.chooser: Optional["CarriedChooser"] = None
        self.frame = ttk.Frame(parent)
        row = ttk.Frame(self.frame)
        row.pack(fill=tk.BOTH, expand=True)
        self.listbox = tk.Listbox(row, height=6, activestyle="none", exportselection=False)
        self.listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        buttons = ttk.Frame(row)
        buttons.pack(side=tk.LEFT, fill=tk.Y, padx=(5, 0))
        ttk.Button(buttons, text="+", width=3, command=self.open_chooser).pack(pady=(0, 4))
        ttk.Button(buttons, text="×", width=3, command=self.remove_selected).pack()
        self.lbl_empty = ttk.Label(self.listbox, text="No ships: press + to add one.", style=ui_style.ON_LIST_LABEL)
        self._draw()

    def labels(self) -> List[str]:
        return [carried_label(r, self.app.fitting_manager, self.app.evedb_loader) for r in self.entries]

    def warnings(self) -> List[str]:
        return carried_warnings(self.entries, self.capacity, self.app.evedb_loader)

    def add(self, label: str, quantity: int) -> bool:
        """Adds the chosen option; the same hull and fitting again adds to its quantity."""
        option = next((o for o in self.options if o.label == label), None)
        if option is None or quantity < 1:
            return False
        new = carried_requirement(option, int(quantity))
        same = next((r for r in self.entries if (r.match, r.type_id, r.fit_uid) == (new.match, new.type_id, new.fit_uid)),
                    None)
        if same is not None:
            same.min_quantity += new.min_quantity
        else:
            self.entries.append(new)
        self._draw()
        return True

    def remove_selected(self) -> None:
        selected = self.listbox.curselection()
        if selected:
            del self.entries[selected[0]]
            self._draw()

    def open_chooser(self) -> None:
        self.chooser = CarriedChooser(self)

    def _draw(self) -> None:
        self.listbox.delete(0, tk.END)
        for label in self.labels():
            self.listbox.insert(tk.END, label)
        if self.entries:
            self.lbl_empty.place_forget()
        else:
            self.lbl_empty.place(relx=0.5, rely=0.5, anchor=tk.CENTER)


class CarriedChooser:
    """The + window: a hull or saved fitting (type to filter) and how many."""

    def __init__(self, editor: CarriedEditor):
        self.editor = editor
        self.window = tk.Toplevel(editor.frame)
        self.window.title("Add a carried ship")
        self.window.transient(editor.frame.winfo_toplevel())
        body = ttk.Frame(self.window, padding=(10, 10))
        body.pack(fill=tk.BOTH, expand=True)
        labels = [o.label for o in editor.options]
        if labels:
            ttk.Label(body, text="Ship (type to filter):").grid(row=0, column=0, sticky=tk.W)
        else:
            ttk.Label(body, text="No saved fitting of a ship that fits in this bay: import one in Fittings.",
                      style=ui_style.HINT_LABEL).grid(row=0, column=0, columnspan=2, sticky=tk.W)
        self.combo = ttk.Combobox(body, values=labels, width=48)
        self.combo.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(2, 8))
        self.combo.bind("<KeyRelease>", self._on_type)
        ttk.Label(body, text="How many:").grid(row=2, column=0, sticky=tk.W)
        self.quantity = ttk.Spinbox(body, from_=1, to=999, width=6)
        self.quantity.set(1)
        self.quantity.grid(row=2, column=1, sticky=tk.W)
        self.message = ttk.Label(body, text="", foreground=ui_style.ERROR)
        self.message.grid(row=3, column=0, columnspan=2, sticky=tk.W)
        buttons = ttk.Frame(body)
        buttons.grid(row=4, column=0, columnspan=2, sticky=tk.E, pady=(8, 0))
        ttk.Button(buttons, text="Cancel", command=self.close).pack(side=tk.LEFT, padx=5)
        ttk.Button(buttons, text="Add", command=self.confirm).pack(side=tk.LEFT)
        place_window(self.window)        # centred on the app, not top left

    def _on_type(self, event):
        if event.keysym in ("Up", "Down", "Return", "Escape", "Tab"):
            return
        typed = self.combo.get().strip().casefold()
        self.combo["values"] = [o.label for o in self.editor.options if not typed or typed in o.label.casefold()]

    def confirm(self) -> None:
        try:
            quantity = int(self.quantity.get())
        except ValueError:
            quantity = 0
        if quantity < 1:
            self.message.config(text="How many must be 1 or more.")
            return
        if not self.editor.add(self.combo.get(), quantity):
            self.message.config(text="Choose a ship from the list.")
            return
        self.close()

    def close(self) -> None:
        self.window.destroy()
