"""
Debug ▸ View Logs: this session's log lines in their own window (the replacement
for the log panel, defect F10). It shows the lines the app has kept so far and
adds new ones while it's open. Only one viewer is open at a time.
"""
import tkinter as tk
from tkinter import ttk
from typing import Iterable

from app.gui import style as ui_style


class LogViewer:
    def __init__(self, app, lines: Iterable[str], max_lines: int):
        self.app = app
        self.max_lines = max_lines

        self.window = tk.Toplevel(app.root)
        self.window.title("Logs")
        self.window.geometry("900x500")
        self.window.transient(app.root)
        body = ttk.Frame(self.window, padding=(10, 10))
        body.pack(fill=tk.BOTH, expand=True)

        self.text = ui_style.ScrolledText(body, state='disabled', font=("Consolas", 9), wrap=tk.NONE)
        self.text.pack(fill=tk.BOTH, expand=True)

        buttons = ttk.Frame(body)
        buttons.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(buttons, text="Close", width=10, command=self.close).pack(side=tk.RIGHT)
        ttk.Button(buttons, text="Export…", width=10, command=app._handle_export_logs).pack(side=tk.RIGHT, padx=5)

        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.append("".join(lines))

    def is_open(self) -> bool:
        try:
            return bool(self.window.winfo_exists())
        except tk.TclError:
            return False

    def show(self) -> None:
        self.window.deiconify()
        self.window.lift()
        self.window.focus_set()

    def contents(self) -> str:
        return self.text.get("1.0", tk.END)

    def append(self, text: str) -> None:
        """Adds lines at the end, scrolling with them only if the view was already at the bottom."""
        if not text:
            return
        at_bottom = self.text.yview()[1] >= 0.999
        self.text.configure(state='normal')
        self.text.insert(tk.END, text)
        excess = int(self.text.index('end-1c').split('.')[0]) - self.max_lines
        if excess > 0:
            self.text.delete('1.0', f'{excess + 1}.0')
        self.text.configure(state='disabled')
        if at_bottom:
            self.text.see(tk.END)

    def close(self) -> None:
        self.window.destroy()
