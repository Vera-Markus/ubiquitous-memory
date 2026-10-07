"""
Coloured status icons for the audit trees (homes and priorities plan, D6).

Tk draws emoji in one colour, so a row's leading status emoji (✅ ⚠ ❌ ⚪, as the presenter
writes them) becomes a small image in the theme's status colour, drawn here with Tk alone
(anti-aliased against the tree's background). A failing row is also tinted: hard in the
theme's error colour, soft in its warning colour. Every other emoji stays in the text.

    icons = StatusIcons(tree)
    icons.insert(parent, "end", text="❌ No Devoter in Jita", tone="hard")
    shown_text(tree, item)          # "❌ No Devoter in Jita" again: for logs and the harnesses

The images and tags are redrawn in place when the theme changes, so rows already in the
tree follow it.
"""
import math
import tkinter as tk
from typing import Dict, Optional, Tuple

from app.gui import style as ui_style

SIZE = 14           # pixels
SAMPLES = 4         # per pixel and axis, for the anti-aliasing
MARK = "[x] "       # the shopping list's mark on a line (AuditTab._mark_added)
GAP = " "           # between an icon and its text

# The leading emoji -> its icon; a soft failure's ❌ is drawn in the warning colour instead.
EMOJI = {"✅": "pass", "⚠": "warn", "❌": "hard", "⚪": "none"}
KINDS = {"pass": ("tick", "ok"), "warn": ("triangle", "warn"), "hard": ("x", "error"), "soft": ("x", "warn"),
         "none": ("ring", "muted")}
TONES = {"hard": "error", "soft": "warn"}        # row tint for a failing row


def _segment(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    dx, dy = bx - ax, by - ay
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


SHAPES = {          # inside(x, y), on a square from 0 to 1
    "x": lambda x, y: min(_segment(x, y, .2, .2, .8, .8), _segment(x, y, .8, .2, .2, .8)) < .1,
    "tick": lambda x, y: min(_segment(x, y, .15, .55, .4, .8), _segment(x, y, .4, .8, .85, .22)) < .1,
    "ring": lambda x, y: abs(math.hypot(x - .5, y - .5) - .33) < .08,
    "triangle": lambda x, y: (min(_segment(x, y, .5, .1, .08, .88), _segment(x, y, .08, .88, .92, .88),
                                  _segment(x, y, .92, .88, .5, .1)) < .07
                              or _segment(x, y, .5, .38, .5, .6) < .06 or math.hypot(x - .5, y - .74) < .055),
}


def _blend(colour: str, background: str, amount: float) -> str:
    fg = [int(colour[i:i + 2], 16) for i in (1, 3, 5)]
    bg = [int(background[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(round(b + (f - b) * amount) for f, b in zip(fg, bg))


def draw(image: tk.PhotoImage, shape: str, colour: str, background: str) -> None:
    """Draws a shape into the image, edges blended into the background, empty pixels transparent."""
    image.blank()
    inside = SHAPES[shape]
    for py in range(SIZE):
        for px in range(SIZE):
            hits = sum(inside((px + (sx + .5) / SAMPLES) / SIZE, (py + (sy + .5) / SAMPLES) / SIZE)
                       for sx in range(SAMPLES) for sy in range(SAMPLES))
            if hits:
                image.put(_blend(colour, background, hits / SAMPLES ** 2), (px, py))
            else:
                image.transparency_set(px, py, True)


def split(text: str, tone: Optional[str] = None) -> Tuple[Optional[str], str]:
    """(icon kind, the text without its leading status emoji), or (None, text) when there's none."""
    for emoji, kind in EMOJI.items():
        if text.startswith(emoji):
            if kind == "hard" and tone == "soft":
                kind = "soft"
            return kind, GAP + text[len(emoji):].lstrip()
    return None, text


def _palette() -> Dict[str, str]:
    return ui_style.THEMES.get(ui_style.current_theme or ui_style.DEFAULT_THEME, ui_style.THEMES[ui_style.DEFAULT_THEME])


class StatusIcons:
    """One per tree: its icon images and row tints, following the theme."""

    def __init__(self, tree):
        self.tree = tree
        self.images: Dict[str, tk.PhotoImage] = {kind: tk.PhotoImage(master=tree, width=SIZE, height=SIZE)
                                                 for kind in KINDS}
        self._names = {str(image): kind for kind, image in self.images.items()}
        self._drawn_for = None
        self.redraw()
        tree.bind("<<ThemeChanged>>", lambda e: self.redraw(), add="+")

    def redraw(self) -> None:
        p = _palette()
        if self._drawn_for == p:
            return
        self._drawn_for = p
        for kind, (shape, key) in KINDS.items():
            draw(self.images[kind], shape, p[key], p["surface"])
        for tone, key in TONES.items():
            self.tree.tag_configure(tone, foreground=p[key])

    def insert(self, parent, index, text: str = "", tone: Optional[str] = None, **kw) -> str:
        """Treeview.insert, with the leading status emoji drawn as an icon and a failing row tinted."""
        kind, text = split(text, tone)
        if kind is not None:
            kw["image"] = self.images[kind]
        if tone in TONES:
            kw["tags"] = tuple(kw.get("tags", ())) + (tone,)
        return self.tree.insert(parent, index, text=text, **kw)

    def kind_of(self, item) -> Optional[str]:
        image = self.tree.item(item, "image")
        return self._names.get(str(image[0] if isinstance(image, (list, tuple)) and image else image))


def shown_text(tree, item) -> str:
    """
    A row's text with its status emoji put back where the presenter wrote it: what the row
    says, for logs and the harnesses ("[x] " marks stay in front, as before the icons).
    """
    text = tree.item(item, "text")
    icons: Optional[StatusIcons] = getattr(tree, "status_icons", None)
    kind = icons.kind_of(item) if icons is not None else None
    if kind is None:
        return text
    emoji = next(e for e, k in EMOJI.items() if k == ("hard" if kind == "soft" else kind))
    body = text[len(GAP):] if text.startswith(GAP) else text
    if body.startswith(MARK):
        return f"{MARK}{emoji} {body[len(MARK):]}"
    return f"{emoji} {body}"


def mark(text: str) -> str:
    """The shopping list's "[x] " in front of a line's text, after an icon's gap."""
    if text.startswith(GAP + MARK) or text.startswith(MARK):
        return text
    return GAP + MARK + text[len(GAP):] if text.startswith(GAP) else MARK + text


def unmark(text: str) -> str:
    if text.startswith(GAP + MARK):
        return GAP + text[len(GAP + MARK):]
    return text[len(MARK):] if text.startswith(MARK) else text
