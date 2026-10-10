"""
Assets ▸ right-click a ship ▸ Update Contents from Game… (1.7.4): paste the ship's contents as the
game copies them, to check a refit without waiting for the next pull.

    text = PasteContentsDialog(root, "Raven \"Big Bird\"").ask()     # the pasted text, or None
"""
import tkinter as tk
from tkinter import ttk
from typing import Optional

from app.gui import style as ui_style
from app.gui.window_placement import place_window

TITLE = "Update Contents from Game"


class PasteContentsDialog:
    def __init__(self, root, ship: str):
        self.result: Optional[str] = None
        self.window = tk.Toplevel(root)
        self.window.title(TITLE)
        self.window.transient(root)
        self.window.minsize(520, 360)
        body = ttk.Frame(self.window, padding=(15, 15))
        body.pack(fill=tk.BOTH, expand=True)
        ttk.Label(body, justify=tk.LEFT, anchor=tk.W, wraplength=560, text=(
            f"{ship}\n\n"
            "In the game, open the ship's contents, select every item (Ctrl+A), copy them (Ctrl+C) and paste "
            "them below. The audit uses them, marked unverified, until the next pull replaces them. "
            "Charges loaded in modules count as cargo; ships carried aboard stay as pulled.")).pack(fill=tk.X)
        frame = ttk.Frame(body)
        frame.pack(fill=tk.BOTH, expand=True, pady=(10, 0))
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        self.text = tk.Text(frame, wrap=tk.NONE, height=14, width=70, font=(ui_style.FONT_FAMILY, 9), undo=True)
        yscroll = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=self.text.yview)
        xscroll = ttk.Scrollbar(frame, orient=tk.HORIZONTAL, command=self.text.xview)
        self.text.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.text.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        self.error = ttk.Label(body, text="", foreground=ui_style.ERROR, wraplength=560, justify=tk.LEFT)
        self.error.pack(fill=tk.X, pady=(8, 0))
        buttons = ttk.Frame(body)
        buttons.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(buttons, text="Cancel", width=10, command=self.window.destroy).pack(side=tk.RIGHT)
        ttk.Button(buttons, text="Apply", width=10, command=self._apply).pack(side=tk.RIGHT, padx=5)
        self.window.bind("<Escape>", lambda e: self.window.destroy())
        self.text.focus_set()
        place_window(self.window)

    def _apply(self) -> None:
        text = self.text.get("1.0", tk.END).strip()
        if not text:
            self.error.config(text="Paste the ship's contents first.")
            return
        self.result = text
        self.window.destroy()

    def ask(self) -> Optional[str]:
        self.window.grab_set()
        self.window.wait_window()
        return self.result
