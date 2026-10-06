"""
Where new windows appear: centred on the window that opened them, instead of Windows'
default top-left corner, and kept on screen when the app is near a screen edge.

Call place_window(window) at the end of a dialog's set-up, before Tk first draws it,
so it opens in place without flickering. A dialog opened before the main window shows
(the database download at first start) is centred on the screen.
"""
import re
import tkinter as tk
from typing import Optional, Tuple

MARGIN = 8          # px kept clear of the screen edges


def _size(window: tk.Misc) -> Tuple[int, int]:
    """The window's size: what was asked with geometry("900x500"), else what its contents need."""
    window.update_idletasks()
    asked = re.match(r"(\d+)x(\d+)", window.wm_geometry())
    width, height = (int(asked.group(1)), int(asked.group(2))) if asked else (1, 1)
    if width <= 1 or height <= 1:
        width, height = window.winfo_reqwidth(), window.winfo_reqheight()
    return width, height


def centred_position(size: Tuple[int, int], parent: Optional[Tuple[int, int, int, int]],
                     screen: Tuple[int, int]) -> Tuple[int, int]:
    """(x, y) for a window of `size`, centred on parent (x, y, width, height) or the screen, kept on it."""
    width, height = size
    screen_w, screen_h = screen
    if parent is not None:
        px, py, pw, ph = parent
        x, y = px + (pw - width) // 2, py + (ph - height) // 2
    else:
        x, y = (screen_w - width) // 2, (screen_h - height) // 2
    x = max(MARGIN, min(x, screen_w - width - MARGIN))
    y = max(MARGIN, min(y, screen_h - height - MARGIN))
    return x, y


def place_window(window: tk.Toplevel, parent: Optional[tk.Misc] = None) -> None:
    """Centres a new window on `parent` (default: the window it belongs to)."""
    try:
        owner = parent or (window.master.winfo_toplevel() if window.master is not None else None)
        size = _size(window)
        box = None
        if owner is not None and owner is not window and owner.winfo_viewable():
            box = (owner.winfo_rootx(), owner.winfo_rooty(), owner.winfo_width(), owner.winfo_height())
        x, y = centred_position(size, box, (window.winfo_screenwidth(), window.winfo_screenheight()))
        window.geometry(f"+{x}+{y}")
    except tk.TclError:
        pass        # the window closed already; nothing to place
