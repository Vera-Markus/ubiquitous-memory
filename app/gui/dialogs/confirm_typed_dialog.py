"""
A confirmation for actions that can't be undone (UI rework step 5.6): the
confirm button only works once the user has typed a word, e.g. RESET.

    if ConfirmTypedDialog(root, "Full Reset", message, "RESET").ask(): ...
"""
import tkinter as tk
from tkinter import ttk
from app.gui import style as ui_style
from app.gui.style import DANGER_BUTTON
from app.gui.window_placement import place_window


class ConfirmTypedDialog:
    def __init__(self, root, title: str, message: str, word: str, confirm_label: str = "Reset Everything"):
        self.word = word
        self.confirmed = False
        self.window = tk.Toplevel(root)
        self.window.title(title)
        self.window.resizable(False, False)
        self.window.transient(root)
        body = ttk.Frame(self.window, padding=(15, 15))
        body.pack(fill=tk.BOTH, expand=True)

        ttk.Label(body, text=message, justify=tk.LEFT, anchor=tk.W, wraplength=460).pack(fill=tk.X)
        ttk.Label(body, text=f"Type {word} to confirm:", font=(ui_style.FONT_FAMILY, 9, "bold")).pack(anchor=tk.W, pady=(12, 2))
        self.typed = tk.StringVar()
        self.entry = ttk.Entry(body, textvariable=self.typed, width=20)
        self.entry.pack(anchor=tk.W)
        self.typed.trace_add("write", lambda *_: self._update())

        buttons = ttk.Frame(body)
        buttons.pack(fill=tk.X, pady=(12, 0))
        ttk.Button(buttons, text="Cancel", width=10, command=self.window.destroy).pack(side=tk.RIGHT)
        self.btn_confirm = ttk.Button(buttons, text=confirm_label, style=DANGER_BUTTON, state=tk.DISABLED, command=self._confirm)
        self.btn_confirm.pack(side=tk.RIGHT, padx=5)
        self.entry.bind("<Return>", lambda e: self._confirm())
        self.entry.focus_set()
        place_window(self.window)        # centred on the app, not top left

    def _update(self) -> None:
        self.btn_confirm.config(state=tk.NORMAL if self.typed.get().strip() == self.word else tk.DISABLED)

    def _confirm(self) -> None:
        if self.typed.get().strip() != self.word:
            return
        self.confirmed = True
        self.window.destroy()

    def ask(self) -> bool:
        """Waits until the window closes. True only if the word was typed and confirmed."""
        self.window.grab_set()
        self.window.wait_window()
        return self.confirmed
