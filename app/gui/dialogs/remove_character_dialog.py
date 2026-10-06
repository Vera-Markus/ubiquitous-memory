"""
Characters ▸ Remove Character (UI rework step 5.4). Pick a connected character,
see everything that will be deleted, and confirm with Remove.
"""
import tkinter as tk
from tkinter import ttk
from typing import Callable, Dict, Optional

from app.services.character_removal_service import CharacterRemovalService, RemovalPlan
from app.gui.style import DANGER_BUTTON
from app.gui.window_placement import place_window


class RemoveCharacterDialog:
    def __init__(self, app, service: CharacterRemovalService, characters: Dict[str, str],
                 on_remove: Callable[[str], None], preselect: Optional[str] = None):
        """characters maps character ID to name; on_remove gets the chosen ID after Remove."""
        self.app = app
        self.service = service
        self.on_remove = on_remove
        self.labels = {f"{name} ({cid})": cid for cid, name in sorted(characters.items(), key=lambda c: c[1].lower())}

        self.window = tk.Toplevel(app.root)
        self.window.title("Remove Character")
        self.window.transient(app.root)
        body = ttk.Frame(self.window, padding=(10, 10))
        body.pack(fill=tk.BOTH, expand=True)

        ttk.Label(body, text="Character:").pack(anchor=tk.W)
        self.combo = ttk.Combobox(body, values=list(self.labels), state="readonly", width=45)
        self.combo.pack(anchor=tk.W, fill=tk.X, pady=(0, 8))
        self.combo.bind("<<ComboboxSelected>>", lambda e: self._show_plan())

        self.details = ttk.Label(body, text="", justify=tk.LEFT, anchor=tk.W, wraplength=420)
        self.details.pack(fill=tk.BOTH, expand=True)

        buttons = ttk.Frame(body)
        buttons.pack(fill=tk.X, pady=(10, 0))
        ttk.Button(buttons, text="Cancel", width=10, command=self.window.destroy).pack(side=tk.RIGHT)
        self.btn_remove = ttk.Button(buttons, text="Remove", width=10, style=DANGER_BUTTON, command=self.confirm, state=tk.DISABLED)
        self.btn_remove.pack(side=tk.RIGHT, padx=5)

        for label, cid in self.labels.items():
            if cid == preselect:
                self.combo.set(label)
        if not self.combo.get() and len(self.labels) == 1:
            self.combo.current(0)
        self._show_plan()
        place_window(self.window)        # centred on the app, not top left

    def selected_id(self) -> Optional[str]:
        return self.labels.get(self.combo.get())

    def _show_plan(self) -> Optional[RemovalPlan]:
        cid = self.selected_id()
        if cid is None:
            self.details.config(text="Choose a character to see what will be removed.")
            self.btn_remove.config(state=tk.DISABLED)
            return None
        plan = self.service.plan(cid)
        self.details.config(text=plan.summary())
        self.btn_remove.config(state=tk.NORMAL)
        return plan

    def confirm(self) -> None:
        cid = self.selected_id()
        if cid is None:
            return
        self.window.destroy()
        self.on_remove(cid)
