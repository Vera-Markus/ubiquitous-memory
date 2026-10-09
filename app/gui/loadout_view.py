"""
The Fittings tab's Loadout view (1.7.2 plan, 36.3; mock-up D): the selected fitting laid out,
with its doctrine requirements edited in place. It replaces the Doctrine Requirements pop-up
and the escape ship chooser (design §7.2): the same checks and the same saving.

Left: the fit (every slot, empty ones faded), then the drone or fighter bay (fighters with
their tube squadrons beside them; a hull with neither shows a faded "Drone bay 0 m³").
Right: the cargo (and fleet hangar), then the requirement bays: fuel, fleet hangar stock,
ship maintenance bay, escape ship; then Notes, Clear all and Save Requirements.
An implant set (a Capsule fitting) shows one box with what it lists.

What it shows is worked out in app/services/loadout.py; what the requirement text means,
in app/services/doctrine_metadata_form.py.
"""
import tkinter as tk
from tkinter import ttk
from typing import Callable, Dict, List, Optional

from app.gui import style as ui_style
from app.gui.dialogs.carried_editor import CarriedEditor
from app.gui.dialogs.fuel_editor import FuelEditor
from app.models.bay_registry import ESCAPE_BAY
from app.models.doctrine_metadata import DoctrineMetadata, MetadataError
from app.services import loadout
from app.services.doctrine_metadata_form import (build_metadata, capacity_text, escape_label, escape_options,
                                                 filter_options, fuel_rows, fuel_suggestions, metadata_bays,
                                                 requirements_text, tube_problems)

FADED = "Faded.TLabelframe"
NO_BAYS = "No requirement bays on this hull."


class EscapeCombo:
    """The escape bay dropdown: No escape ship / Any ship / candidate fittings, filtered as you type."""

    def __init__(self, parent, fitting: dict, app, initial: str = ""):
        self.options = escape_options(fitting, app.fitting_manager, app.evedb_loader)
        self.combo = ttk.Combobox(parent, values=[o.label for o in self.options], width=48)
        self.combo.set(initial)
        self.combo.bind("<KeyRelease>", self._on_type)

    def _on_type(self, event):
        if event.keysym in ("Up", "Down", "Return", "Escape", "Tab"):
            return
        self.combo["values"] = filter_options(self.options, self.combo.get())

    def get(self) -> str:
        return self.combo.get()


def _styles(widget):
    style = ttk.Style(widget)
    style.configure(f"{FADED}.Label", foreground=ui_style.MUTED)
    style.configure("Faded.TLabel", foreground=ui_style.MUTED)
    style.configure("Slot.TLabel", foreground=ui_style.MUTED, font=(ui_style.FONT_FAMILY, 9, "bold"))
    style.configure("Empty.TLabel", foreground=ui_style.MUTED, font=(ui_style.FONT_FAMILY, 9, "italic"))


class LoadoutView:
    def __init__(self, parent, app, on_saved: Optional[Callable[[], None]] = None):
        self.app = app
        self.on_saved = on_saved
        self.frame = ttk.Frame(parent, padding=(6, 6))
        self.frame.bind("<<ThemeChanged>>", lambda e: _styles(self.frame), add="+")
        _styles(self.frame)
        self.fitting: Optional[dict] = None
        self._clear_state()

    def _clear_state(self):
        self.bays = []
        self.texts: Dict[str, tk.Text] = {}
        self.fuel: Optional[FuelEditor] = None
        self.carried: Optional[CarriedEditor] = None
        self.tubes = []                  # (TubeRow, Spinbox)
        self.tube_count = 0
        self.escape: Optional[EscapeCombo] = None
        self.notes: Optional[ttk.Entry] = None
        self.message: Optional[ttk.Label] = None
        self.btn_save = self.btn_clear = None
        self.cargo_tree = None
        self._acknowledged_warnings = None
        self._confirm_clear = False
        self._built_state = None

    # --- building -------------------------------------------------------------------------

    def show(self, fitting: Optional[dict]):
        """Draws a fitting (None: an empty view). Whatever was being edited is dropped."""
        for child in self.frame.winfo_children():
            child.destroy()
        self._clear_state()
        self.fitting = fitting
        if not fitting:
            ttk.Label(self.frame, text="Select a fitting on the left.", style="Faded.TLabel").pack(pady=20)
            return
        if loadout.is_contents_only(fitting):
            self._contents_only(fitting)
            return
        left, right = ttk.Frame(self.frame), ttk.Frame(self.frame)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        right.grid(row=0, column=1, sticky="nsew")
        self.frame.columnconfigure(0, weight=1, uniform="loadout")
        self.frame.columnconfigure(1, weight=1, uniform="loadout")
        self.frame.rowconfigure(0, weight=1)
        self.existing = self.app.fitting_manager.get_metadata(fitting["fit_uid"])
        self._fit_box(left, fitting)
        self._bay_box(left, fitting)
        self._cargo_box(right, fitting)
        self._requirements(right, fitting)
        self._built_state = self.form_state()

    def _box(self, parent, title, faded=False):
        return ttk.LabelFrame(parent, text=title, padding=(8, 6), **({"style": FADED} if faded else {}))

    def _contents_only(self, fitting):
        box = self._box(self.frame, loadout.contents_title(fitting, self.app.evedb_loader))
        box.pack(fill=tk.BOTH, expand=True)
        self._list(box, loadout.stacks(fitting, "cargo") or ["Empty"])

    def _fit_box(self, parent, fitting):
        box = self._box(parent, "Fit")
        box.pack(fill=tk.X)
        for section in loadout.slots(fitting, self.app.evedb_loader):
            ttk.Label(box, text=section.label, style="Slot.TLabel").pack(anchor=tk.W, pady=(4, 0))
            for name in section.modules:
                ttk.Label(box, text=f"  {name}").pack(anchor=tk.W)
            for _ in range(section.empty):
                ttk.Label(box, text="  empty slot", style="Empty.TLabel").pack(anchor=tk.W)

    def _bay_box(self, parent, fitting):
        info = loadout.bay_box(fitting, self.app.evedb_loader, self.existing.tubes)
        box = self._box(parent, info.title, info.faded)
        box.pack(fill=tk.X, pady=(6, 0))
        if info.kind != "fighters":
            for line in info.lines:
                ttk.Label(box, text=line, style="Faded.TLabel" if info.faded else "TLabel").pack(anchor=tk.W)
            return
        self.tube_count = info.tubes
        for index, line in enumerate(info.fighters):
            ttk.Label(box, text=f"{line.quantity} × {line.name}").grid(row=index, column=0, sticky=tk.W, padx=(0, 12))
            if line.tube is None or not info.tubes:
                continue
            ttk.Label(box, text="in tubes", style="Faded.TLabel").grid(row=index, column=1, sticky=tk.E)
            spin = ttk.Spinbox(box, from_=0, to=info.tubes, width=3)
            spin.set(line.tube.squadrons)
            spin.grid(row=index, column=2, padx=4, pady=1)
            ttk.Label(box, text=f"squadrons of {line.tube.squadron_size}", style="Faded.TLabel").grid(
                row=index, column=3, sticky=tk.W)
            self.tubes.append((line.tube, spin))
        box.columnconfigure(0, weight=1)
        if not info.fighters:
            ttk.Label(box, text="Empty", style="Faded.TLabel").grid(row=0, column=0, sticky=tk.W)
        elif self.tubes:
            ttk.Label(box, text=f"{info.tubes} tubes. All 0: tubes and bay are counted together.",
                      style="Faded.TLabel").grid(row=len(info.fighters), column=0, columnspan=4, sticky=tk.W,
                                                 pady=(4, 0))

    def _cargo_box(self, parent, fitting):
        box = self._box(parent, loadout.cargo_title(fitting, self.app.evedb_loader))
        box.pack(fill=tk.BOTH, expand=True)
        self.cargo_tree = self._list(box, loadout.stacks(fitting, "cargo") or ["Empty"])

    @staticmethod
    def _list(parent, lines, height=10):
        """A scrolling list of text lines (cargo, an implant set's contents)."""
        frame = ttk.Frame(parent)
        frame.pack(fill=tk.BOTH, expand=True)
        tree = ttk.Treeview(frame, show="tree", height=height, selectmode="none")
        bar = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=bar.set)
        for line in lines:
            tree.insert("", tk.END, text=line)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        bar.pack(side=tk.RIGHT, fill=tk.Y)
        return tree

    def _requirements(self, parent, fitting):
        sde = self.app.evedb_loader
        self.bays = metadata_bays(fitting, sde)
        for bay, capacity in self.bays:
            if bay.key == ESCAPE_BAY.key:
                continue
            label = f"{bay.label} + cargo" if bay.pooled_with_cargo else bay.label
            box = self._box(parent, f"{label} ({capacity_text(bay, capacity)})")
            box.pack(fill=tk.X, pady=(6, 0))
            if bay.key == "fuel_bay":
                self.fuel = FuelEditor(box, fuel_rows(fitting, self.existing.requirements(bay.key), sde), capacity, sde)
                self.fuel.frame.pack(fill=tk.BOTH, expand=True)
                _, hint = fuel_suggestions(fitting, sde)
                if hint:
                    ttk.Label(box, text=hint, justify=tk.LEFT, style="Faded.TLabel").pack(anchor=tk.W)
            elif bay.key == "ship_maintenance_bay":
                self.carried = CarriedEditor(box, fitting, self.existing.requirements(bay.key), capacity, self.app)
                self.carried.frame.pack(fill=tk.BOTH, expand=True)
            else:
                row = ttk.Frame(box)
                row.pack(fill=tk.BOTH, expand=True)
                text = tk.Text(row, width=30, height=4, undo=True)
                bar = ttk.Scrollbar(row, orient=tk.VERTICAL, command=text.yview)
                text.configure(yscrollcommand=bar.set)
                text.insert("1.0", requirements_text(self.existing.requirements(bay.key)))
                text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
                bar.pack(side=tk.RIGHT, fill=tk.Y)
                self.texts[bay.key] = text
        if any(bay.key == ESCAPE_BAY.key for bay, _ in self.bays):
            box = self._box(parent, f"{ESCAPE_BAY.label} (1 ship)")
            box.pack(fill=tk.X, pady=(6, 0))
            current = self.existing.requirements(ESCAPE_BAY.key)
            self.escape = EscapeCombo(box, fitting, self.app)
            self.escape.combo.set(escape_label(current[0] if current else None, self.escape.options))
            self.escape.combo.pack(anchor=tk.W, fill=tk.X)
        if not self.bays and not self.tubes:
            ttk.Label(parent, text=NO_BAYS, style="Faded.TLabel").pack(anchor=tk.W, pady=6)
            return
        row = ttk.Frame(parent)
        row.pack(fill=tk.X, pady=(6, 0))
        ttk.Label(row, text="Notes").pack(side=tk.LEFT)
        self.notes = ttk.Entry(row)
        self.notes.insert(0, self.existing.notes)
        self.notes.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6)
        self.btn_clear = ttk.Button(row, text="Clear all", command=self.clear_all)
        self.btn_clear.pack(side=tk.LEFT, padx=(0, 6))
        self.btn_save = ttk.Button(row, text="Save Requirements", command=self.save)
        self.btn_save.pack(side=tk.LEFT)
        self.message = ttk.Label(parent, text="", justify=tk.LEFT, anchor=tk.W, wraplength=520)
        self.message.pack(fill=tk.X)

    # --- state ----------------------------------------------------------------------------

    def has_requirements(self) -> bool:
        return self.btn_save is not None

    def form_state(self):
        """Everything the user can change, to tell whether anything was."""
        if not self.has_requirements():
            return None
        return ({key: text.get("1.0", tk.END).strip() for key, text in self.texts.items()},
                self.fuel.text() if self.fuel else None,
                tuple(self.carried.labels()) if self.carried else None,
                tuple(box.get() for _, box in self.tubes),
                self.escape.get() if self.escape else None,
                self.notes.get())

    def dirty(self) -> bool:
        """Requirement changes not saved yet."""
        return self.has_requirements() and self.form_state() != self._built_state

    def show_message(self, lines: List[str], colour=None):
        if self.message is not None:
            self.message.config(text="\n".join(lines), foreground=colour or ui_style.INFO)

    # --- actions (as the Doctrine Requirements editor's) -----------------------------------

    def save(self) -> bool:
        """Saves the requirements. False when there were errors, or warnings to read first (press again)."""
        if not self.has_requirements():
            return False
        existing_escape = (self.existing.requirements(ESCAPE_BAY.key) or [None])[0]
        texts = {key: text.get("1.0", tk.END) for key, text in self.texts.items()}
        if self.fuel is not None:
            texts["fuel_bay"] = self.fuel.text()
        result = build_metadata(texts,
                                self.escape.get() if self.escape else None,
                                self.escape.options if self.escape else [],
                                self.notes.get(), self.app.evedb_loader, existing_escape,
                                self.carried.entries if self.carried is not None else None)
        if self.fuel is not None:
            result.warnings[:0] = self.fuel.warnings()
        if self.carried is not None:
            result.warnings += self.carried.warnings()
        if self.tubes:
            rows = []
            for row, box in self.tubes:
                try:
                    row.squadrons = max(0, int(box.get() or 0))
                except ValueError:
                    result.errors.append(f"Fighter tubes: {row.name} needs a number of squadrons")
                rows.append(row)
            result.errors += tube_problems(rows, self.tube_count, self.fitting.get("hull", ""))
            result.metadata.tubes = {r.type_id: r.squadrons for r in rows if r.squadrons}
        if result.errors:
            self.show_message(["Fix these before saving:"] + [f"• {e}" for e in result.errors], ui_style.ERROR)
            return False
        if result.warnings and result.warnings != self._acknowledged_warnings:
            self._acknowledged_warnings = result.warnings
            self.show_message([f"⚠ {w}" for w in result.warnings] + ["Press Save again to save anyway."], ui_style.WARN)
            self.btn_save.config(text="Save anyway")
            return False
        return self._store(result.metadata)

    def clear_all(self):
        if not self._confirm_clear:
            self._confirm_clear = True
            self.btn_clear.config(text="Confirm clear")
            self.show_message(["This removes every requirement and the notes from this fitting. "
                               "Press Confirm clear to go ahead."], ui_style.WARN)
            return
        self._store(DoctrineMetadata())

    def _store(self, metadata: DoctrineMetadata) -> bool:
        try:
            saved = self.app.fitting_manager.set_metadata(self.fitting["fit_uid"], metadata)
        except MetadataError as error:
            self.show_message(["Couldn't save:"] + [f"• {p}" for p in error.problems], ui_style.ERROR)
            return False
        count = sum(len(r) for r in saved.bays.values())
        self.app._log(f"[SUCCESS] Saved doctrine requirements for {self.fitting['fit_name']}: {count} requirement(s).")
        fitting = self.app.fitting_manager.get_fitting(self.fitting["fit_uid"]) or self.fitting
        self.show(fitting)
        self.show_message([f"Saved: {count} requirement(s)."], ui_style.OK)
        if self.on_saved:
            self.on_saved()
        return True

    def describe(self) -> dict:
        """What the view shows, for the harnesses."""
        if not self.fitting:
            return {"fitting": None}
        state = {"fitting": self.fitting.get("fit_name"),
                 "boxes": [w.cget("text") for w in self._descendants(self.frame) if isinstance(w, ttk.LabelFrame)]}
        if self.has_requirements():
            state["bays"] = {key: text.get("1.0", tk.END).rstrip("\n") for key, text in self.texts.items()}
            if self.fuel is not None:
                state["fuel"] = self.fuel.state()
            if self.carried is not None:
                state["carried"] = self.carried.labels()
            if self.tubes:
                state["tubes"] = [(row.name, box.get()) for row, box in self.tubes]
            if self.escape is not None:
                state["escape"] = self.escape.get()
                state["escape_choices"] = list(self.escape.combo["values"])
            state["notes"] = self.notes.get()
            state["save_button"] = self.btn_save["text"]
            state["message"] = self.message["text"]
        return state

    @classmethod
    def _descendants(cls, widget):
        for child in widget.winfo_children():
            yield child
            yield from cls._descendants(child)
