"""
The Tranquility status icon on the right of the tab row (ESI features plan 25.3).

A lamp in the theme's status colours (D11.4): green online, amber degraded (VIP or ESI
trouble), red offline (the daily window too), grey unknown. A small red dot on its
corner means the status page has something new (D11.7). Hovering shows the details;
clicking opens CCP's status page and clears the dot (D11.17).
"""
import tkinter as tk
import webbrowser
from tkinter import ttk
from typing import Callable, Optional

from app.esi_service.server_status import DEGRADED, OFFLINE, ONLINE, STATUS_PAGE, UNKNOWN, ServerStatus
from app.gui import style as ui_style

LAMP = 14           # px
PAD = 3
COLOUR_OF = {ONLINE: "ok", DEGRADED: "warn", OFFLINE: "error", UNKNOWN: "muted"}


def _palette():
    return ui_style.THEMES.get(ui_style.current_theme or ui_style.DEFAULT_THEME, ui_style.THEMES[ui_style.DEFAULT_THEME])


class ServerStatusIcon(ttk.Frame):
    def __init__(self, master, status: ServerStatus, on_click: Optional[Callable[[], None]] = None):
        super().__init__(master, cursor="hand2")
        self.status = status
        self.on_click = on_click
        size = LAMP + 2 * PAD
        self.canvas = tk.Canvas(self, width=size, height=size, highlightthickness=0, borderwidth=0, cursor="hand2")
        self.canvas.pack(side=tk.LEFT)
        self.label = ttk.Label(self, text="Tranquility", cursor="hand2")
        self.label.pack(side=tk.LEFT, padx=(2, 0))
        self.tip: Optional[tk.Toplevel] = None
        self.shown = (None, None)       # (state, dot) as last drawn
        for widget in (self, self.canvas, self.label):
            widget.bind("<Button-1>", self._clicked, add="+")
            widget.bind("<Enter>", self._show_tip, add="+")
            widget.bind("<Leave>", self._hide_tip, add="+")
        self.bind("<<ThemeChanged>>", lambda e: self.refresh(force=True), add="+")
        self.refresh(force=True)

    def refresh(self, force: bool = False) -> None:
        """Redraws the lamp if the state or the dot changed (or the theme did)."""
        shown = (self.status.shown_state(), self.status.dot())
        if shown == self.shown and not force:
            return
        self.shown = shown
        state, dot = shown
        p = _palette()
        c = self.canvas
        c.configure(background=p["bg"])
        c.delete("all")
        c.create_oval(PAD, PAD, PAD + LAMP, PAD + LAMP, fill=p[COLOUR_OF[state]], outline=p["border"])
        if dot:
            # A badge on the lamp's corner, ringed in the background colour so it stands apart
            # even on the red lamp.
            r = 4
            x, y = PAD + LAMP - 1, PAD + 1
            c.create_oval(x - r - 2, y - r - 2, x + r + 2, y + r + 2, fill=p["bg"], outline="")
            c.create_oval(x - r, y - r, x + r, y + r, fill=p["error"], outline=p["text"], width=1)
        if self.tip is not None:
            self._fill_tip()

    # --- tooltip and click ---------------------------------------------------------------------

    def _show_tip(self, event=None) -> None:
        if self.tip is not None:
            return
        p = _palette()
        self.tip = tk.Toplevel(self)
        self.tip.wm_overrideredirect(True)
        self.tip.configure(background=p["border"])
        self.tip_text = tk.Label(self.tip, justify=tk.LEFT, background=p["surface"], foreground=p["text"],
                                 font=(ui_style.FONT_FAMILY, 9), padx=8, pady=6)
        self.tip_text.pack(padx=1, pady=1)
        self._fill_tip()
        self.tip.update_idletasks()
        x = self.winfo_rootx() + self.winfo_width() - self.tip.winfo_reqwidth()
        y = self.winfo_rooty() + self.winfo_height() + 4
        self.tip.wm_geometry(f"+{max(x, 0)}+{y}")

    def _fill_tip(self) -> None:
        self.tip_text.configure(text="\n".join(self.status.summary()))

    def _hide_tip(self, event=None) -> None:
        if self.tip is not None:
            self.tip.destroy()
            self.tip = None

    def _clicked(self, event=None) -> None:
        self.status.clear_dot()
        self._hide_tip()
        self.refresh()
        if self.on_click:
            self.on_click()
        else:
            webbrowser.open(STATUS_PAGE)
