"""
One place for the GUI's look (UI rework step 8.1): ttk throughout, so buttons,
labels, frames, checkboxes and scrollbars match the native Windows controls the
trees and dropdowns already use. Lists and text boxes stay classic Tk (ttk has
none), as does tk.PanedWindow (its panes take pixel widths).

    style.apply(root)                       # once, when the main window is built
    ttk.Button(parent, text="Delete", style=style.DANGER_BUTTON)
    label.config(foreground=style.WARN)
"""
from tkinter import ttk

FONT_FAMILY = "Segoe UI"
FONT_MONO = ("Consolas", 10)

# Text colours, for labels whose colour carries meaning
ERROR = "#b00020"
WARN = "#b35c00"         # a cooldown counting down
OK = "#1e7b34"
INFO = "#1a5fb4"
MUTED = "#6b6b6b"
TEXT = ""                 # the theme's own text colour

DANGER_BUTTON = "Danger.TButton"
HEADING_LABEL = "Heading.TLabel"
HINT_LABEL = "Hint.TLabel"
ON_LIST_LABEL = "OnList.TLabel"      # a message drawn over an empty list or tree


def apply(root) -> ttk.Style:
    """Picks the native theme where there is one and defines the shared styles."""
    style = ttk.Style(root)
    for theme in ("vista", "winnative", "clam"):
        if theme in style.theme_names():
            style.theme_use(theme)
            break
    style.configure(DANGER_BUTTON, foreground=ERROR)
    style.configure(HEADING_LABEL, font=(FONT_FAMILY, 12, "bold"))
    style.configure(HINT_LABEL, foreground=MUTED)
    style.configure(ON_LIST_LABEL, foreground=MUTED, background=style.lookup("Treeview", "fieldbackground") or "white")
    return style
