"""
The requirements editor's Fuel Bay (UI thoughts plan 20.1, U9): a slider and a number box
per fuel, in units. Each fuel's slider stops where the bay is full beside the others, from
the SDE's volumes. The hull's jump fuel and fitted modules' fuels are listed at 0; another
fuel can be added by name. A fuel at 0 isn't required.
"""
import tkinter as tk
from tkinter import ttk
from typing import Any, List

from app.gui import style as ui_style
from app.models.bay_registry import CATEGORY_MATERIAL
from app.services.doctrine_metadata_form import FuelRow, fuel_max, fuel_text, fuel_used, fuel_warnings


class FuelEditor:
    def __init__(self, parent, rows: List[FuelRow], capacity: float, sde: Any):
        self.rows = rows
        self.capacity = capacity
        self.sde = sde
        self.frame = ttk.Frame(parent)
        self.grid = ttk.Frame(self.frame)
        self.grid.pack(fill=tk.X)
        self.grid.columnconfigure(1, weight=1)
        self.lbl_used = ttk.Label(self.frame, text="", style=ui_style.HINT_LABEL)
        self.lbl_used.pack(anchor=tk.W, pady=(4, 0))
        add = ttk.Frame(self.frame)
        add.pack(fill=tk.X, pady=(4, 0))
        ttk.Label(add, text="Add fuel:").pack(side=tk.LEFT)
        self.entry_add = ttk.Entry(add, width=28)
        self.entry_add.pack(side=tk.LEFT, padx=5)
        self.entry_add.bind("<Return>", lambda e: self.add_fuel(self.entry_add.get()))
        ttk.Button(add, text="Add", command=lambda: self.add_fuel(self.entry_add.get())).pack(side=tk.LEFT)
        self.lbl_add = ttk.Label(add, text="", style=ui_style.HINT_LABEL)
        self.lbl_add.pack(side=tk.LEFT, padx=5)
        self._widgets = []          # per row: (scale, StringVar, max label)
        self._draw()

    # --- what the dialog reads -------------------------------------------------------------

    def text(self) -> str:
        return fuel_text(self.rows)

    def warnings(self) -> List[str]:
        return fuel_warnings(self.rows, self.capacity)

    def state(self) -> list:
        """[(name, quantity, max)] for the harness."""
        return [(r.name, r.quantity, fuel_max(self.rows, i, self.capacity)) for i, r in enumerate(self.rows)]

    # --- changing amounts -----------------------------------------------------------------

    def set_quantity(self, index: int, quantity: int) -> None:
        """Sets a fuel's amount, kept between 0 and what fits beside the others."""
        top = fuel_max(self.rows, index, self.capacity)
        quantity = max(0, int(quantity))
        self.rows[index].quantity = quantity if top is None else min(quantity, top)
        self._refresh()

    def add_fuel(self, name: str) -> None:
        name = name.strip()
        type_id = self.sde.get_typeid_by_name(name) if name else None
        if not type_id:
            self.lbl_add.config(text=f'"{name}" isn\'t in the EVE database' if name else "")
            return
        if any(r.type_id == type_id for r in self.rows):
            self.lbl_add.config(text=f"{name} is already listed")
            return
        note = "" if self.sde.get_type_category(type_id) == CATEGORY_MATERIAL else "not a usual fuel"
        self.lbl_add.config(text=note)
        self.rows.append(FuelRow(type_id, name, self.sde.get_type_volume(type_id)))
        self.entry_add.delete(0, tk.END)
        self._draw()

    def _on_scale(self, index: int, value: str) -> None:
        quantity = int(round(float(value)))
        if quantity != self.rows[index].quantity:
            self.set_quantity(index, quantity)

    def _on_typed(self, index: int) -> None:
        typed = self._widgets[index][1].get().replace(",", "").strip()
        try:
            quantity = int(float(typed)) if typed else 0
        except ValueError:
            quantity = self.rows[index].quantity
        self.set_quantity(index, quantity)

    # --- drawing ----------------------------------------------------------------------------

    def _draw(self) -> None:
        for child in self.grid.winfo_children():
            child.destroy()
        self._widgets = []
        if not self.rows:
            ttk.Label(self.grid, text="No fuel suggested for this hull: add one below.",
                      style=ui_style.HINT_LABEL).grid(row=0, column=0, sticky=tk.W)
        for index, row in enumerate(self.rows):
            ttk.Label(self.grid, text=row.name).grid(row=index, column=0, sticky=tk.W, padx=(0, 8), pady=2)
            scale = ttk.Scale(self.grid, from_=0, to=1, orient=tk.HORIZONTAL,
                              command=lambda v, i=index: self._on_scale(i, v))
            scale.grid(row=index, column=1, sticky="ew", pady=2)
            var = tk.StringVar()
            entry = ttk.Entry(self.grid, textvariable=var, width=12, justify=tk.RIGHT)
            entry.grid(row=index, column=2, padx=8, pady=2)
            for event in ("<Return>", "<FocusOut>"):
                entry.bind(event, lambda e, i=index: self._on_typed(i))
            top = ttk.Label(self.grid, text="", style=ui_style.HINT_LABEL, width=14)
            top.grid(row=index, column=3, sticky=tk.W)
            self._widgets.append((scale, var, top))
        self._refresh()

    def _refresh(self) -> None:
        """Every slider's end moves with the others' amounts; the numbers follow the rows."""
        for index, (scale, var, top) in enumerate(self._widgets):
            row = self.rows[index]
            most = fuel_max(self.rows, index, self.capacity)
            scale.configure(to=max(most if most is not None else row.quantity, 1))
            scale.set(row.quantity)
            var.set(f"{row.quantity:,}")
            top.config(text=f"max {most:,}" if most is not None else "no volume known")
        self.lbl_used.config(text=f"Uses {fuel_used(self.rows):,.0f} of {self.capacity:,.0f} m³")
