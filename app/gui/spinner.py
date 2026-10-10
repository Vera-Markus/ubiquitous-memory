"""
A spinning circle with a line of text, laid over a widget while its data loads (1.7.4).

    spinner = Spinner(parent)
    spinner.show(over=tree, text="Loading ships…")
    ...
    spinner.hide()

Drawn on a canvas in the theme's colours; it turns every FRAME_MS while shown.
"""
import tkinter as tk
from tkinter import ttk

from app.gui import style as ui_style

SIZE = 36           # px
WIDTH = 4           # the arc's line width
EXTENT = 100        # degrees of arc drawn
STEP = 15           # degrees turned per frame
FRAME_MS = 40


def _palette():
    return ui_style.THEMES.get(ui_style.current_theme or ui_style.DEFAULT_THEME, ui_style.THEMES[ui_style.DEFAULT_THEME])


class Spinner(ttk.Frame):
    def __init__(self, master):
        super().__init__(master, padding=(16, 12))
        self.canvas = tk.Canvas(self, width=SIZE, height=SIZE, highlightthickness=0, borderwidth=0)
        self.canvas.pack()
        self.label = ttk.Label(self, text="")
        self.label.pack(pady=(8, 0))
        self._angle = 0
        self._job = None

    @property
    def shown(self) -> bool:
        return self._job is not None

    def show(self, over: tk.Widget, text: str = "Loading…") -> None:
        self.label.config(text=text)
        p = _palette()
        self.canvas.configure(background=p["bg"])
        self.place(in_=over, relx=0.5, rely=0.5, anchor=tk.CENTER)
        self.lift()
        if self._job is None:
            self._turn()

    def hide(self) -> None:
        if self._job is not None:
            self.after_cancel(self._job)
            self._job = None
        self.place_forget()

    def _turn(self) -> None:
        p = _palette()
        c = self.canvas
        c.delete("all")
        pad = WIDTH
        c.create_oval(pad, pad, SIZE - pad, SIZE - pad, outline=p["border"], width=WIDTH)
        c.create_arc(pad, pad, SIZE - pad, SIZE - pad, start=-self._angle, extent=EXTENT,
                     style=tk.ARC, outline=p["info"], width=WIDTH)
        self._angle = (self._angle + STEP) % 360
        self._job = self.after(FRAME_MS, self._turn)
