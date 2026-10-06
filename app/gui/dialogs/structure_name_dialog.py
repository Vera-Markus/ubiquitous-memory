"""
Names a player structure by hand when no logged-in character could look it up
through ESI (dead code clean-up C2). The System box suggests matching systems as
you type; the name can be anything. A problem is shown in the window, so what
was typed stays put to be corrected.

    result = StructureNameDialog(root, structure_id, holder_names, systems).ask()
    # ("save", name, system_id), or None when skipped or closed
"""
import tkinter as tk
from tkinter import ttk
from typing import Dict, List, Optional, Tuple

from app.gui import style as ui_style

MAX_SUGGESTIONS = 50


class StructureNameDialog:
    def __init__(self, root, structure_id: int, holder_names: List[str], systems: Dict[str, int],
                 position: str = ""):
        self.systems = systems
        self._by_folded = {name.casefold(): name for name in systems}
        self.result: Optional[Tuple[str, str, int]] = None

        self.window = tk.Toplevel(root)
        self.window.title("Name a Structure" + (f" ({position})" if position else ""))
        self.window.resizable(False, False)
        self.window.transient(root)
        body = ttk.Frame(self.window, padding=(15, 15))
        body.pack(fill=tk.BOTH, expand=True)

        held_by = ", ".join(holder_names) if holder_names else "your characters"
        ttk.Label(body, justify=tk.LEFT, anchor=tk.W, wraplength=420, text=(
            f"None of your characters could look up structure {structure_id} through ESI. "
            f"{held_by} {'has' if len(holder_names) == 1 else 'have'} assets there.\n\n"
            "Enter its system and a name so audits can match it. It's still looked up on later "
            "pulls, and the official name replaces yours once a character can see it."
        )).pack(fill=tk.X)

        form = ttk.Frame(body)
        form.pack(fill=tk.X, pady=(12, 0))
        ttk.Label(form, text="System:").grid(row=0, column=0, sticky=tk.W, pady=2)
        self.system_combo = ttk.Combobox(form, width=32)
        self.system_combo.grid(row=0, column=1, sticky=tk.W, padx=(8, 0), pady=2)
        self.system_combo.bind("<KeyRelease>", self._suggest)
        ttk.Label(form, text="Name:").grid(row=1, column=0, sticky=tk.W, pady=2)
        self.name_entry = ttk.Entry(form, width=35)
        self.name_entry.grid(row=1, column=1, sticky=tk.W, padx=(8, 0), pady=2)

        self.error = ttk.Label(body, text="", foreground=ui_style.ERROR, wraplength=420, justify=tk.LEFT)
        self.error.pack(fill=tk.X, pady=(8, 0))

        buttons = ttk.Frame(body)
        buttons.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(buttons, text="Skip", width=10, command=self.window.destroy).pack(side=tk.RIGHT)
        ttk.Button(buttons, text="Save", width=10, command=self._save).pack(side=tk.RIGHT, padx=5)
        self.name_entry.bind("<Return>", lambda e: self._save())
        self.system_combo.focus_set()

    def _suggest(self, event=None) -> None:
        """Lists the systems whose names contain what's typed (those starting with it first)."""
        if event is not None and event.keysym in ("Return", "Up", "Down", "Left", "Right", "Escape", "Tab"):
            return
        typed = self.system_combo.get().strip().casefold()
        if not typed:
            self.system_combo["values"] = []
            return
        matches = [name for folded, name in self._by_folded.items() if typed in folded]
        matches.sort(key=lambda name: (not name.casefold().startswith(typed), name))
        self.system_combo["values"] = matches[:MAX_SUGGESTIONS]

    def _save(self) -> None:
        system = self._by_folded.get(self.system_combo.get().strip().casefold())
        name = self.name_entry.get().strip()
        if system is None:
            self.error.config(text="Choose a system from the suggestions (start typing its name).")
            self.system_combo.focus_set()
            return
        if not name:
            self.error.config(text="Enter a name for the structure.")
            self.name_entry.focus_set()
            return
        self.result = ("save", name, self.systems[system])
        self.window.destroy()

    def ask(self) -> Optional[Tuple[str, str, int]]:
        """Waits until the window closes: ("save", name, system_id), or None when skipped."""
        self.window.grab_set()
        self.window.wait_window()
        return self.result
