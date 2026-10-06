"""
A list with centred group headings (UI thoughts 10 follow-up; plan 17.6, the Library's
Role Requirements grouped by system).

A tk.Listbox can't centre one row, so this draws the list in a read-only Text widget: a
heading line is centred and shaded with a gap above it, rows below are selectable. It
answers the Listbox calls the Library and its harness use (delete, insert, get, size,
curselection, selection_set, selection_clear, see, <<ListboxSelect>>), counting headings
as items, so an index means the same thing in both. Headings can't be selected:
selecting one takes the first row under it.

    groups = GroupedList(parent)
    groups.add_heading("Amarr")
    groups.insert(tk.END, "Devoter - SNUFF Devoter 1.0 · Amarr VIII (Oris)")
"""
import tkinter as tk
from typing import List, Optional, Tuple

from app.gui import style as ui_style


class GroupedList(tk.Text):
    def __init__(self, master, **kw):
        kw.setdefault("height", 10)
        kw.setdefault("font", (ui_style.FONT_FAMILY, 9))     # a Text defaults to a fixed-width font
        super().__init__(master, wrap="none", cursor="arrow", state="disabled", padx=4, pady=4,
                         exportselection=False, **kw)
        self._items: List[Tuple[str, bool]] = []         # (text, is a heading)
        self._selected: Optional[int] = None
        self.bind("<Button-1>", self._on_click)
        self.bind("<Up>", lambda e: self._step(-1))
        self.bind("<Down>", lambda e: self._step(1))
        self._style_tags()

    # --- Listbox-like calls ----------------------------------------------------------------

    def delete(self, first, last=None):
        """Only the whole list is ever deleted (delete(0, END))."""
        self._items, self._selected = [], None
        self._redraw()

    def insert(self, index, text: str):
        """A selectable row, at the end."""
        self._items.append((str(text), False))
        self._redraw()

    def add_heading(self, text: str) -> None:
        self._items.append((str(text), True))
        self._redraw()

    def get(self, first, last=None):
        if last is None:
            return self._items[self._index(first)][0]
        return tuple(text for text, _ in self._items)

    def _index(self, index) -> int:
        """A Listbox index: a number, or "end" for the last item."""
        return len(self._items) - 1 if str(index) == tk.END else int(index)

    def size(self) -> int:
        return len(self._items)

    def is_heading(self, index: int) -> bool:
        return 0 <= index < len(self._items) and self._items[index][1]

    def curselection(self) -> Tuple[int, ...]:
        return (self._selected,) if self._selected is not None else ()

    def selection_set(self, first, last=None) -> None:
        index = self._index(first)
        while self.is_heading(index):
            index += 1                      # a heading: its first row
        self._selected = index if 0 <= index < len(self._items) else None
        self._paint_selection()

    def selection_clear(self, first=0, last=None) -> None:
        self._selected = None
        self._paint_selection()

    def see(self, index) -> None:
        super().see(f"{self._index(index) + 1}.0")

    # --- drawing ---------------------------------------------------------------------------

    def _style_tags(self) -> None:
        p = ui_style.THEMES.get(ui_style.current_theme or ui_style.DEFAULT_THEME, ui_style.THEMES[ui_style.DEFAULT_THEME])
        self.tag_configure("heading", justify="center", background=p["heading"],
                           foreground=p.get("heading_text", p["accent"]), font=(ui_style.FONT_FAMILY, 9, "bold"),
                           spacing1=3, spacing3=3)
        self.tag_configure("gap", spacing1=12)
        self.tag_configure("row", lmargin1=8, spacing1=1, spacing3=1)
        self.tag_configure("selected", background=p["select"], foreground=p["select_text"])
        self.tag_raise("selected")

    def _redraw(self) -> None:
        self._style_tags()
        super().configure(state="normal")
        super().delete("1.0", tk.END)
        for index, (text, heading) in enumerate(self._items):
            tags = ("heading", "gap") if heading and index > 0 else ("heading",) if heading else ("row",)
            super().insert(tk.END, text + "\n", tags)
        super().configure(state="disabled")
        self._paint_selection()

    def _paint_selection(self) -> None:
        self.tag_remove("selected", "1.0", tk.END)
        if self._selected is not None:
            line = self._selected + 1
            self.tag_add("selected", f"{line}.0", f"{line + 1}.0")      # the newline too: the whole width

    # --- events -----------------------------------------------------------------------------

    def _on_click(self, event):
        self.focus_set()
        index = int(self.index(f"@{event.x},{event.y}").split(".")[0]) - 1
        if 0 <= index < len(self._items) and not self._items[index][1]:
            self._selected = index
            self._paint_selection()
            self.event_generate("<<ListboxSelect>>")
        return "break"

    def _step(self, direction: int):
        index = (self._selected if self._selected is not None else -1) + direction
        while 0 <= index < len(self._items) and self._items[index][1]:
            index += direction
        if 0 <= index < len(self._items):
            self._selected = index
            self._paint_selection()
            self.see(index)
            self.event_generate("<<ListboxSelect>>")
        return "break"
