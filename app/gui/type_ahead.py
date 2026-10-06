"""
Dropdowns you can type in (noted during SDE step 9.3, done after Phase 11).

A Combobox with a long list (every solar system, every hull) that narrows as you
type, takes a name as soon as it's typed exactly, and says when nothing matches:

    ahead = TypeAhead(combo, on_accept=..., noun="system", hint=label, pinned=["<Any System>"])
    ahead.set_choices(names, current="Jita")

- Typing narrows the list to names containing the text, names starting with it first.
- A name typed exactly (any capitals) is taken at once: the box shows it as listed
  and on_accept runs, so the dependent dropdown (stations, fits) follows.
- Enter takes the only match; with several, it opens the narrowed list to pick from.
- Leaving the box with text that matches nothing puts back the last name taken.
- The hint label says how many names match, or that none do.
"""
import tkinter as tk
from typing import Callable, List, Optional, Sequence

from app.gui import style as ui_style

NAVIGATION_KEYS = ("Return", "KP_Enter", "Up", "Down", "Left", "Right", "Escape", "Tab",
                   "Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R", "Home", "End")


class TypeAhead:
    def __init__(self, combo, on_accept: Callable[[str], None], noun: str,
                 hint=None, pinned: Sequence[str] = ()):
        self.combo = combo
        self.on_accept = on_accept
        self.noun = noun
        self.hint = hint                    # a label for "3 systems match …", or None
        self.pinned = list(pinned)          # always first when they match, e.g. "<Any System>"
        self.choices: List[str] = []
        self.accepted = ""
        combo.configure(state="normal")
        combo.bind("<KeyRelease>", self.on_key, add="+")
        combo.bind("<Return>", self.on_enter, add="+")
        combo.bind("<KP_Enter>", self.on_enter, add="+")
        combo.bind("<<ComboboxSelected>>", lambda e: self.accept(self.combo.get()), add="+")
        combo.bind("<FocusOut>", self.on_focus_out, add="+")

    # --- the list -----------------------------------------------------------------------

    def set_choices(self, choices: Sequence[str], current: Optional[str] = None) -> None:
        """The full list; current (if listed) becomes the accepted name, without calling on_accept."""
        self.choices = [c for c in choices if c not in self.pinned]
        self.combo["values"] = self.pinned + self.choices
        if current is not None:
            self.accepted = current
            self.combo.set(current)
        self._say("")

    def all_names(self) -> List[str]:
        return self.pinned + self.choices

    def matches(self, text: str) -> List[str]:
        """Names containing text (any capitals): pinned ones first, then those starting with it, then the rest."""
        term = text.strip().casefold()
        if not term:
            return self.all_names()
        pinned = [p for p in self.pinned if term in p.casefold()]
        starts = [c for c in self.choices if c.casefold().startswith(term)]
        contains = [c for c in self.choices if term in c.casefold() and not c.casefold().startswith(term)]
        return pinned + starts + contains

    def exact(self, text: str) -> Optional[str]:
        term = text.strip().casefold()
        return next((name for name in self.all_names() if name.casefold() == term), None) if term else None

    # --- events -------------------------------------------------------------------------

    def on_key(self, event=None) -> None:
        if event is not None and getattr(event, "keysym", "") in NAVIGATION_KEYS:
            return
        text = self.combo.get()
        found = self.matches(text)
        self.combo["values"] = found
        name = self.exact(text)
        if name is not None:
            self.accept(name, keep_list=True)
        elif not text.strip():
            self._say("")
        elif not found:
            self._say(f"No {self.noun} matches '{text.strip()}'.", error=True)
        elif len(found) == 1:
            self._say(f"1 {self.noun} matches '{text.strip()}': press Enter to choose it.")
        else:
            self._say(f"{len(found)} {self.noun}s match '{text.strip()}': press Enter or ↓ to choose.")

    def on_enter(self, event=None):
        text = self.combo.get()
        name = self.exact(text)
        found = self.matches(text)
        if name is None and len(found) == 1:
            name = found[0]
        if name is not None:
            self.accept(name)
        elif found:
            self.combo["values"] = found
            self.combo.event_generate("<Down>")         # opens the narrowed list
        return "break"

    def on_focus_out(self, event=None) -> None:
        if event is not None:
            # Opening the list moves focus into it (a child of the box): only a real leave counts,
            # and focus has only moved once this event is over.
            self.combo.after_idle(self._left)
            return
        self._left(force=True)

    def _left(self, force: bool = False) -> None:
        try:
            focus = self.combo.focus_get()
        except (KeyError, tk.TclError):
            focus = None                    # focus is in a window Tk can't name (the open list on some platforms)
        if not force and (focus is None or str(focus).startswith(str(self.combo))):
            return
        if self.exact(self.combo.get()) is None:
            self.combo.set(self.accepted)
        self.combo["values"] = self.all_names()
        self._say("")

    # --- taking a name ------------------------------------------------------------------

    def accept(self, name: str, keep_list: bool = False) -> None:
        """Shows the name as listed and tells the tab, if it changed (or wasn't shown as listed)."""
        name = self.exact(name) or name
        if name not in self.all_names():
            return
        changed = name != self.accepted
        self.accepted = name
        if self.combo.get() != name:
            cursor = self.combo.index(tk.INSERT)
            self.combo.set(name)
            self.combo.icursor(cursor)
        if not keep_list:
            self.combo["values"] = self.all_names()
        self._say("")
        if changed:
            self.on_accept(name)

    def _say(self, text: str, error: bool = False) -> None:
        if self.hint is not None:
            self.hint.config(text=text, foreground=ui_style.ERROR if error else ui_style.MUTED)
