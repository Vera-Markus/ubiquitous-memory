"""
The Doctrine Requirements editor (design §7.2).

A modal Toplevel owned by the main window. Like the tabs, it takes `app` for
the managers, the SDE and _log, and never imports main_window. What the text
means is decided in app/services/doctrine_metadata_form.py.
"""
import tkinter as tk
from tkinter import ttk
from typing import Callable, Dict, Optional

from app.models.bay_registry import ESCAPE_BAY
from app.models.doctrine_metadata import DoctrineMetadata, MetadataError
from app.gui import style as ui_style
from app.services.doctrine_metadata_form import (capacity_text, escape_label, escape_options, filter_options,
                                                 build_metadata, fuel_suggestions, metadata_bays, requirements_text)

ERROR_COLOUR = "#b00020"
WARNING_COLOUR = "#8a5a00"


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


class DoctrineMetadataDialog:
    def __init__(self, app, fitting: dict, on_saved: Optional[Callable[[], None]] = None):
        self.app = app
        self.fitting = fitting
        self.on_saved = on_saved
        self.fit_uid = fitting["fit_uid"]
        self.existing = app.fitting_manager.get_metadata(self.fit_uid)
        self.bays = metadata_bays(fitting, app.evedb_loader)
        self.texts: Dict[str, tk.Text] = {}
        self.escape: Optional[EscapeCombo] = None
        self._acknowledged_warnings = None
        self._confirm_clear = False

        self.window = tk.Toplevel(app.root)
        self.window.title(f"Doctrine Requirements: {fitting['fit_name']} ({fitting['hull']})")
        self.window.transient(app.root)
        self._build()
        try:
            self.window.grab_set()
        except tk.TclError:
            pass    # not viewable yet; the dialog still works, just not modal

    # --- layout --------------------------------------------------------------------------

    def _build(self):
        body = ttk.Frame(self.window, padding=(10, 10))
        body.pack(fill=tk.BOTH, expand=True)

        boxes = [(bay, capacity) for bay, capacity in self.bays if bay.key != ESCAPE_BAY.key]
        for index, (bay, capacity) in enumerate(boxes):
            label = f"{bay.label} + cargo" if bay.pooled_with_cargo else bay.label
            frame = ttk.LabelFrame(body, text=f"{label} ({capacity_text(bay, capacity)})", padding=(5, 5))
            frame.grid(row=index // 2, column=index % 2, sticky="nsew", padx=5, pady=5)
            text = tk.Text(frame, width=40, height=6, undo=True)
            text.pack(fill=tk.BOTH, expand=True)
            current = requirements_text(self.existing.requirements(bay.key))
            hint = ""
            if bay.key == "fuel_bay":
                suggested, hint = fuel_suggestions(self.fitting, self.app.evedb_loader)
                current = current or suggested
            text.insert("1.0", current)
            if hint:
                ttk.Label(frame, text=hint, justify=tk.LEFT, foreground=ui_style.MUTED).pack(anchor=tk.W)
            self.texts[bay.key] = text

        rows = (len(boxes) + 1) // 2
        if any(bay.key == ESCAPE_BAY.key for bay, _ in self.bays):
            frame = ttk.LabelFrame(body, text=f"{ESCAPE_BAY.label} (1 ship)", padding=(5, 5))
            frame.grid(row=rows, column=0, columnspan=2, sticky="ew", padx=5, pady=5)
            current = self.existing.requirements(ESCAPE_BAY.key)
            self.escape = EscapeCombo(frame, self.fitting, self.app)
            self.escape.combo.set(escape_label(current[0] if current else None, self.escape.options))
            self.escape.combo.pack(anchor=tk.W)
            rows += 1
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)

        notes = ttk.Frame(body)
        notes.grid(row=rows, column=0, columnspan=2, sticky="ew", padx=5, pady=5)
        ttk.Label(notes, text="Notes").pack(side=tk.LEFT)
        self.notes = ttk.Entry(notes)
        self.notes.insert(0, self.existing.notes)
        self.notes.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)

        self.message = ttk.Label(body, text="", justify=tk.LEFT, anchor=tk.W, wraplength=640)
        self.message.grid(row=rows + 1, column=0, columnspan=2, sticky="ew", padx=5)

        buttons = ttk.Frame(body)
        buttons.grid(row=rows + 2, column=0, columnspan=2, sticky="e", pady=(10, 0))
        self.btn_clear = ttk.Button(buttons, text="Clear all", command=self.clear_all)
        self.btn_clear.pack(side=tk.LEFT, padx=5)
        ttk.Button(buttons, text="Cancel", command=self.close).pack(side=tk.LEFT, padx=5)
        self.btn_save = ttk.Button(buttons, text="Save", command=self.save)
        self.btn_save.pack(side=tk.LEFT, padx=5)

    def _show(self, lines, colour):
        self.message.config(text="\n".join(lines), foreground=colour)

    # --- actions -------------------------------------------------------------------------

    def save(self):
        existing_escape = (self.existing.requirements(ESCAPE_BAY.key) or [None])[0]
        result = build_metadata({key: text.get("1.0", tk.END) for key, text in self.texts.items()},
                                self.escape.get() if self.escape else None,
                                self.escape.options if self.escape else [],
                                self.notes.get(), self.app.evedb_loader, existing_escape)
        if result.errors:
            self._show(["Fix these before saving:"] + [f"• {e}" for e in result.errors], ERROR_COLOUR)
            return
        if result.warnings and result.warnings != self._acknowledged_warnings:
            self._acknowledged_warnings = result.warnings
            self._show([f"⚠ {w}" for w in result.warnings] + ["Press Save again to save anyway."], WARNING_COLOUR)
            self.btn_save.config(text="Save anyway")
            return
        self._store(result.metadata)

    def clear_all(self):
        if not self._confirm_clear:
            self._confirm_clear = True
            self.btn_clear.config(text="Confirm clear")
            self._show(["This removes every requirement and the notes from this fitting. "
                        "Press Confirm clear to go ahead."], WARNING_COLOUR)
            return
        self._store(DoctrineMetadata())

    def _store(self, metadata: DoctrineMetadata):
        try:
            saved = self.app.fitting_manager.set_metadata(self.fit_uid, metadata)
        except MetadataError as error:
            self._show(["Couldn't save:"] + [f"• {p}" for p in error.problems], ERROR_COLOUR)
            return
        count = sum(len(r) for r in saved.bays.values())
        self.app._log(f"[SUCCESS] Saved doctrine requirements for {self.fitting['fit_name']}: {count} requirement(s).")
        if self.on_saved:
            self.on_saved()
        self.close()

    def close(self):
        self.window.destroy()
