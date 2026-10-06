"""
A sliding on/off switch (UI thoughts 9; plan 17.5, reused by the ship view's EFT tab in 19.2).

ttk has no switch, so this draws one on a small canvas next to a ttk label, in the
current theme's colours: the accent when on, the border colour when off. A theme change
reaches the label as <<ThemeChanged>>, and the switch redraws.

    switch = ToggleSwitch(parent, "Assigned only", variable=tk.BooleanVar(), command=on_change)
    switch.pack(side=tk.RIGHT)
"""
import tkinter as tk
from tkinter import ttk
from typing import Callable, Optional

from app.gui import style as ui_style

WIDTH, HEIGHT, PAD = 34, 18, 2


class ToggleSwitch(ttk.Frame):
    def __init__(self, master, text: str, variable: Optional[tk.BooleanVar] = None,
                 command: Optional[Callable[[], None]] = None, text_after: str = ""):
        super().__init__(master)
        self.variable = variable if variable is not None else tk.BooleanVar(value=False)
        self.command = command
        self.label = ttk.Label(self, text=text)
        self.label.pack(side=tk.LEFT, padx=(0, 6))
        self.canvas = tk.Canvas(self, width=WIDTH, height=HEIGHT, highlightthickness=0, bd=0, cursor="hand2")
        self.canvas.pack(side=tk.LEFT)
        if text_after:                      # a two-sided switch: "Current [switch] Expected"
            self.label_after = ttk.Label(self, text=text_after)
            self.label_after.pack(side=tk.LEFT, padx=(6, 0))
        for widget in (self.canvas, self.label):
            widget.bind("<Button-1>", lambda e: self.toggle())
        self.label.bind("<<ThemeChanged>>", lambda e: self.after_idle(self.redraw), add="+")
        self.variable.trace_add("write", lambda *a: self.redraw())
        self.redraw()

    def get(self) -> bool:
        return bool(self.variable.get())

    def set(self, value: bool) -> None:
        self.variable.set(bool(value))

    def toggle(self) -> None:
        self.set(not self.get())
        if self.command is not None:
            self.command()

    def redraw(self) -> None:
        p = ui_style.THEMES.get(ui_style.current_theme or ui_style.DEFAULT_THEME, ui_style.THEMES[ui_style.DEFAULT_THEME])
        on = self.get()
        c = self.canvas
        c.delete("all")
        c.configure(background=p["bg"])
        r = HEIGHT / 2
        track = p["accent"] if on else p["border"]
        c.create_oval(0, 0, HEIGHT, HEIGHT, fill=track, outline=track)
        c.create_oval(WIDTH - HEIGHT, 0, WIDTH, HEIGHT, fill=track, outline=track)
        c.create_rectangle(r, 0, WIDTH - r, HEIGHT, fill=track, outline=track)
        x = WIDTH - HEIGHT + PAD if on else PAD
        knob = p["select_text"] if on else p["text"]
        c.create_oval(x, PAD, x + HEIGHT - 2 * PAD, HEIGHT - PAD, fill=knob, outline=knob)
