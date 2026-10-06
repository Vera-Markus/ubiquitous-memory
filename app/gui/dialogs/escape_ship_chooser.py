"""
The compact escape ship chooser (design §6.2), offered after importing a hull
whose only metadata bay is the escape bay (battleships, Marauders).
"""
import tkinter as tk
from tkinter import ttk
from typing import Callable, Optional

from app.gui.dialogs.doctrine_metadata_dialog import EscapeCombo
from app.models.bay_registry import ESCAPE_BAY
from app.models.doctrine_metadata import DoctrineMetadata, MetadataError
from app.services.doctrine_metadata_form import NO_ESCAPE_SHIP
from app.gui import style as ui_style


class EscapeShipChooser:
    def __init__(self, app, fitting: dict, on_saved: Optional[Callable[[], None]] = None):
        self.app = app
        self.fitting = fitting
        self.on_saved = on_saved

        self.window = tk.Toplevel(app.root)
        self.window.title(f"Escape Ship: {fitting['fit_name']}")
        self.window.transient(app.root)
        body = ttk.Frame(self.window, padding=(10, 10))
        body.pack(fill=tk.BOTH, expand=True)
        ttk.Label(body, text=f"This {fitting['hull']} has an escape bay. Which escape ship should it carry?").pack(anchor=tk.W)
        # Nothing is preselected: the recommendation should be a deliberate choice (§7.2).
        self.escape = EscapeCombo(body, fitting, app)
        self.escape.combo.pack(anchor=tk.W, pady=5, fill=tk.X)
        self.message = ttk.Label(body, text="", foreground=ui_style.ERROR, justify=tk.LEFT, anchor=tk.W)
        self.message.pack(fill=tk.X)
        buttons = ttk.Frame(body)
        buttons.pack(anchor=tk.E, pady=(10, 0))
        ttk.Button(buttons, text="Skip", command=self.close).pack(side=tk.LEFT, padx=5)
        ttk.Button(buttons, text="Save", command=self.save).pack(side=tk.LEFT, padx=5)
        try:
            self.window.grab_set()
        except tk.TclError:
            pass

    def save(self):
        choice = self.escape.get().strip()
        option = next((o for o in self.escape.options if o.label == choice), None)
        if option is None:
            self.message.config(text="Choose an escape ship from the list, or Skip.")
            return
        # Keep anything else already stored on the fitting; only the escape bay changes.
        metadata = self.app.fitting_manager.get_metadata(self.fitting["fit_uid"])
        bays = {key: reqs for key, reqs in metadata.bays.items() if key != ESCAPE_BAY.key}
        if option.label != NO_ESCAPE_SHIP:
            bays[ESCAPE_BAY.key] = [option.requirement]
        try:
            self.app.fitting_manager.set_metadata(self.fitting["fit_uid"], DoctrineMetadata(bays=bays, notes=metadata.notes))
        except MetadataError as error:
            self.message.config(text="\n".join(error.problems))
            return
        self.app._log(f"[SUCCESS] Escape ship for {self.fitting['fit_name']}: {option.label}.")
        if self.on_saved:
            self.on_saved()
        self.close()

    def close(self):
        self.window.destroy()
