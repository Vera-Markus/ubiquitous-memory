"""
One place for the GUI's look: ttk throughout (UI rework step 8.1), drawn with ttk's
"clam" theme in one of the colour themes below. Lists and text boxes stay classic
Tk (ttk has none), as does tk.PanedWindow (its panes take pixel widths); apply()
colours those too.

    style.apply(root, theme)                # when the main window is built, and again on a change
    ttk.Button(parent, text="Delete", style=style.DANGER_BUTTON)
    label.config(foreground=style.WARN)     # read at the call: the value follows the theme

A theme change applies straight away: the ttk styles change in place, the classic
widgets in every open window are recoloured, and a label showing a status colour
(ERROR, WARN, ...) gets the new theme's colour for the same status. New windows
take their colours from Tk's option database. Message boxes and the name prompt are
drawn by themed_dialogs; the Windows menu bar and file dialogs keep the system look.
"""
import json
import logging
import tkinter as tk
from pathlib import Path
from tkinter import ttk

logger = logging.getLogger(__name__)

FONT_FAMILY = "Segoe UI"
FONT_MONO = ("Consolas", 10)

# About a dozen named colours per theme. "heading_text" (optional) colours headings,
# tab names and column headers; it defaults to "text".
THEMES = {
    "Amarr": dict(dark=True, bg="#1f190c", surface="#2a2211", text="#f5e8c2", muted="#bba571",
                  border="#6a5420", button="#3d3115", hover="#4f3f19", accent="#f0c24b",
                  select="#80621a", select_text="#fff8e0", heading="#33290f", trough="#251e0e",
                  heading_text="#f0c24b",
                  error="#ff7a6b", warn="#ffa84a", ok="#a8d66a", info="#8fb8e0"),
    "Caldari": dict(dark=True, bg="#141a20", surface="#1c242c", text="#dce6ef", muted="#8797a6",
                    border="#2e3a46", button="#25313c", hover="#2f3d4a", accent="#4fb3d9",
                    select="#1f4d66", select_text="#ffffff", heading="#19212a", trough="#182028",
                    error="#ff6b6b", warn="#ffb454", ok="#5fd38a", info="#7cc7ff"),
    "Minmatar": dict(dark=True, bg="#150e0b", surface="#1f140f", text="#e6d2c6", muted="#9c8478",
                     border="#4d2a1c", button="#331d14", hover="#44261a", accent="#c0552b",
                     select="#5e2515", select_text="#fff0e6", heading="#1c110d", trough="#190f0c",
                     error="#ff6f6f", warn="#f2c94c", ok="#93d36c", info="#7fb9e8"),
    "Gallente": dict(dark=True, bg="#111916", surface="#19231f", text="#dcebe4", muted="#86a096",
                     border="#2b3c35", button="#21302a", hover="#2b3d35", accent="#3fc79a",
                     select="#1b5240", select_text="#ffffff", heading="#151f1b", trough="#151e1a",
                     error="#ff6b6b", warn="#ffb454", ok="#8be28f", info="#7cc7ff"),
    "Triglavian": dict(dark=True, bg="#120e0e", surface="#1c1515", text="#eedede", muted="#9e8888",
                       border="#3e2828", button="#2a1c1c", hover="#382424", accent="#e0323e",
                       select="#5e1a20", select_text="#ffffff", heading="#181111", trough="#171111",
                       error="#ff8f8f", warn="#ffc15e", ok="#7fd99a", info="#8ab8ff"),
    "Light": dict(dark=False, bg="#f3f5f7", surface="#ffffff", text="#1d2733", muted="#66737f",
                  border="#c5ced6", button="#e4e9ee", hover="#d6dee5", accent="#1f7fb0",
                  select="#cfe5f2", select_text="#0d2233", heading="#e9eef2", trough="#e4e9ee",
                  error="#c62828", warn="#b35c00", ok="#2e7d32", info="#1f6fb0"),
    "Bubblegum": dict(dark=False, bg="#fff0f5", surface="#ffffff", text="#4a2c3a", muted="#a07a8c",
                      border="#f4b6cc", button="#ffd6e5", hover="#ffc2d8", accent="#e8467c",
                      select="#ffc2d8", select_text="#3a1a28", heading="#ffe3ee", trough="#ffe3ee",
                      error="#b00020", warn="#b35c00", ok="#2e7d32", info="#6a4fb5"),
}
DEFAULT_THEME = "Caldari"
SETTINGS_FILE = "ui_settings.json"      # in data/config

# Text colours, for labels whose colour carries meaning. apply() sets them from the theme.
_STATUS = ("error", "warn", "ok", "info", "muted")
ERROR, WARN, OK, INFO, MUTED = (THEMES[DEFAULT_THEME][k] for k in _STATUS)
TEXT = ""                 # the theme's own text colour

DANGER_BUTTON = "Danger.TButton"
HEADING_LABEL = "Heading.TLabel"
HINT_LABEL = "Hint.TLabel"
ON_LIST_LABEL = "OnList.TLabel"      # a message drawn over an empty list or tree

current_theme = None


def apply(root, theme: str = DEFAULT_THEME) -> ttk.Style:
    """Draws the whole GUI in a theme (an unknown name falls back to the default)."""
    global current_theme
    name = theme if theme in THEMES else DEFAULT_THEME
    p = THEMES[name]
    old_status = _status_colours()
    _set_status_colours(p)
    style = ttk.Style(root)
    style.theme_use("clam")
    _configure_ttk(style, p)
    _configure_option_database(root, p)
    remap = {old: new for old, new in zip(old_status, _status_colours()) if old}
    _recolour_open_windows(root, p, remap)
    if current_theme is None:
        # After idle: recolouring the frame the moment a window maps made Tk forget where the
        # window was placed (dialogs opened in the top-left corner instead of centred).
        root.bind_class("Toplevel", "<Map>", lambda e: e.widget.after_idle(_title_bar, e.widget), add="+")
    current_theme = name
    _title_bar(root)
    return style


def load_theme(config_dir: Path) -> str:
    """The saved theme name, or the default."""
    try:
        name = json.loads((Path(config_dir) / SETTINGS_FILE).read_text(encoding="utf-8")).get("theme")
    except (OSError, ValueError, AttributeError):
        return DEFAULT_THEME
    return name if name in THEMES else DEFAULT_THEME


def save_theme(config_dir: Path, theme: str) -> None:
    save_setting(config_dir, "theme", theme)


def load_setting(config_dir: Path, key: str, default=None):
    """One remembered UI setting (a switch, the theme) from ui_settings.json."""
    try:
        settings = json.loads((Path(config_dir) / SETTINGS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default
    return settings.get(key, default) if isinstance(settings, dict) else default


def save_setting(config_dir: Path, key: str, value) -> None:
    path = Path(config_dir) / SETTINGS_FILE
    try:
        settings = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        if not isinstance(settings, dict):
            settings = {}
    except (OSError, ValueError):
        settings = {}
    settings[key] = value
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    except OSError as e:
        logger.warning(f"Couldn't save the setting {key} to {path}: {e}")


class ScrolledText(tk.Text):
    """tkinter.scrolledtext.ScrolledText with a ttk scrollbar, which follows the theme
    (the classic scrollbar is drawn by Windows). Geometry methods go to the frame."""

    def __init__(self, master=None, **kw):
        self.frame = ttk.Frame(master)
        self.vbar = ttk.Scrollbar(self.frame, orient=tk.VERTICAL)
        self.vbar.pack(side=tk.RIGHT, fill=tk.Y)
        kw["yscrollcommand"] = self.vbar.set
        tk.Text.__init__(self, self.frame, **kw)
        self.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.vbar["command"] = self.yview
        geometry = vars(tk.Pack).keys() | vars(tk.Grid).keys() | vars(tk.Place).keys()
        for method in geometry - vars(tk.Text).keys():
            if not method.startswith("_") and method not in ("config", "configure"):
                setattr(self, method, getattr(self.frame, method))

    def __str__(self):
        return str(self.frame)


# --- internals ----------------------------------------------------------------

def _status_colours() -> list:
    return [ERROR, WARN, OK, INFO, MUTED]


def _set_status_colours(p) -> None:
    global ERROR, WARN, OK, INFO, MUTED
    ERROR, WARN, OK, INFO, MUTED = (p[k] for k in _STATUS)


def _configure_ttk(s: ttk.Style, p) -> None:
    head = p.get("heading_text", p["text"])
    s.configure(".", background=p["bg"], foreground=p["text"], fieldbackground=p["surface"],
                bordercolor=p["border"], darkcolor=p["bg"], lightcolor=p["bg"], troughcolor=p["trough"],
                focuscolor=p["accent"], selectbackground=p["select"], selectforeground=p["select_text"],
                insertcolor=p["text"], arrowcolor=p["text"])
    s.map(".", foreground=[("disabled", p["muted"])])
    s.configure("TButton", background=p["button"], padding=(10, 4), relief="flat",
                lightcolor=p["button"], darkcolor=p["button"])
    # A disabled button keeps its shape, on the dimmer heading colour, with muted text.
    s.map("TButton", background=[("disabled", p["heading"]), ("pressed", p["select"]), ("active", p["hover"])],
          foreground=[("disabled", p["muted"])],
          lightcolor=[("disabled", p["heading"]), ("active", p["hover"])],
          darkcolor=[("disabled", p["heading"]), ("active", p["hover"])])
    s.configure("TEntry", fieldbackground=p["surface"], foreground=p["text"])
    s.map("TEntry", fieldbackground=[("disabled", p["bg"]), ("readonly", p["bg"])])
    s.configure("TCombobox", fieldbackground=p["surface"], background=p["button"], foreground=p["text"])
    s.map("TCombobox", fieldbackground=[("disabled", p["bg"]), ("readonly", p["surface"])],
          foreground=[("disabled", p["muted"]), ("readonly", p["text"])],
          background=[("active", p["hover"])],
          selectbackground=[("readonly", p["surface"])], selectforeground=[("readonly", p["text"])])
    s.configure("TNotebook", background=p["bg"], bordercolor=p["border"])
    # Unselected tab names halfway between muted and full text: readable, but quieter than the selected tab.
    s.configure("TNotebook.Tab", background=p["button"], foreground=_blend(p["muted"], p["text"], 0.5), padding=(12, 4),
                lightcolor=p["button"], bordercolor=p["border"])
    s.map("TNotebook.Tab", background=[("selected", p["bg"]), ("active", p["hover"])],
          foreground=[("selected", head)], lightcolor=[("selected", p["bg"])])
    s.configure("Treeview", background=p["surface"], fieldbackground=p["surface"], foreground=p["text"],
                bordercolor=p["border"], rowheight=22)
    s.map("Treeview", background=[("selected", p["select"])], foreground=[("selected", p["select_text"])])
    s.configure("Treeview.Heading", background=p["heading"], foreground=head, relief="flat",
                lightcolor=p["heading"], darkcolor=p["heading"], bordercolor=p["border"])
    s.map("Treeview.Heading", background=[("active", p["hover"])])
    for widget in ("TCheckbutton", "TRadiobutton"):
        s.configure(widget, background=p["bg"], foreground=p["text"], indicatorbackground=p["surface"],
                    indicatorforeground=p["accent"], upperbordercolor=p["border"], lowerbordercolor=p["border"])
        s.map(widget, background=[("active", p["bg"])], indicatorbackground=[("selected", p["surface"])])
    s.configure("TLabelframe", background=p["bg"], bordercolor=p["border"], lightcolor=p["bg"], darkcolor=p["bg"])
    s.configure("TLabelframe.Label", background=p["bg"], foreground=head)
    s.configure("TScrollbar", background=p["button"], troughcolor=p["trough"], bordercolor=p["trough"],
                arrowcolor=p["text"], lightcolor=p["button"], darkcolor=p["button"])
    s.map("TScrollbar", background=[("active", p["hover"])])
    s.configure("TProgressbar", background=p["accent"], troughcolor=p["trough"], bordercolor=p["border"],
                lightcolor=p["accent"], darkcolor=p["accent"])
    s.configure("Horizontal.TProgressbar", background=p["accent"])
    s.configure("TSeparator", background=p["border"])
    # Shared styles
    s.configure(DANGER_BUTTON, foreground=p["error"])
    s.configure(HEADING_LABEL, font=(FONT_FAMILY, 12, "bold"), foreground=head)
    s.configure(HINT_LABEL, foreground=p["muted"])
    s.configure(ON_LIST_LABEL, foreground=p["muted"], background=p["surface"])


def _blend(a: str, b: str, t: float) -> str:
    """The colour t of the way from a to b (#rrggbb)."""
    ca, cb = (tuple(int(c[i:i + 2], 16) for i in (1, 3, 5)) for c in (a, b))
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(ca, cb))


def _classic_options(p) -> dict:
    """Option values for each classic Tk widget class."""
    text_box = dict(background=p["surface"], foreground=p["text"], selectbackground=p["select"],
                    selectforeground=p["select_text"], highlightbackground=p["border"], highlightcolor=p["accent"],
                    highlightthickness=1, relief="flat", borderwidth=0)
    return {
        "Listbox": {**text_box, "disabledforeground": p["muted"]},
        "Text": {**text_box, "insertbackground": p["text"]},
        "Entry": {**text_box, "insertbackground": p["text"], "disabledbackground": p["bg"],
                  "readonlybackground": p["bg"]},
        "Panedwindow": dict(background=p["bg"], sashrelief="flat"),
        "Menu": dict(background=p["surface"], foreground=p["text"], activebackground=p["select"],
                     activeforeground=p["select_text"], disabledforeground=p["muted"], relief="flat",
                     activeborderwidth=0),
        "Toplevel": dict(background=p["bg"]),
        "Tk": dict(background=p["bg"]),
        "Frame": dict(background=p["bg"]),
        "Label": dict(background=p["bg"], foreground=p["text"]),
        "Button": dict(background=p["button"], foreground=p["text"], activebackground=p["hover"],
                       activeforeground=p["text"], relief="flat", highlightbackground=p["bg"]),
        "Canvas": dict(background=p["bg"], highlightbackground=p["bg"]),
    }


_OPTION_NAMES = {      # configure option -> Tk option-database name, where they differ
    "selectbackground": "selectBackground", "selectforeground": "selectForeground",
    "highlightbackground": "highlightBackground", "highlightcolor": "highlightColor",
    "highlightthickness": "highlightThickness", "borderwidth": "borderWidth",
    "disabledforeground": "disabledForeground", "insertbackground": "insertBackground",
    "disabledbackground": "disabledBackground", "readonlybackground": "readonlyBackground",
    "sashrelief": "sashRelief", "activebackground": "activeBackground",
    "activeforeground": "activeForeground", "activeborderwidth": "activeBorderWidth",
}


def _option_name(option: str) -> str:
    return _OPTION_NAMES.get(option, option)


def _configure_option_database(root, p) -> None:
    """Colours for classic widgets created from now on (dialogs, simpledialog, drop-down lists)."""
    for cls, options in _classic_options(p).items():
        for option, value in options.items():
            root.option_add(f"*{cls}.{_option_name(option)}", value)
    for option, value in (("background", p["surface"]), ("foreground", p["text"]),
                          ("selectBackground", p["select"]), ("selectForeground", p["select_text"])):
        root.option_add(f"*TCombobox*Listbox.{option}", value)


def _all_widgets(tk_app, path="."):
    """Every widget path below path, including ones tkinter didn't create (combobox drop-downs)."""
    for child in tk_app.splitlist(tk_app.call("winfo", "children", path)):
        yield str(child)
        yield from _all_widgets(tk_app, str(child))


def _recolour_open_windows(root, p, remap: dict) -> None:
    tk_app = root.tk
    classic = _classic_options(p)
    for path in [".", *_all_widgets(tk_app)]:
        try:
            cls = str(tk_app.call("winfo", "class", path))
            if path == "." or cls == "Tk":
                options = classic["Tk"]
            else:
                options = classic.get(cls)
            if options:
                args = []
                for option, value in options.items():
                    args += [f"-{option}", value]
                tk_app.call(path, "configure", *args)
            elif cls == "TLabel" and remap:
                # A label showing a status colour: the same status in the new theme.
                current = str(tk_app.call(path, "cget", "-foreground")).lower()
                if current in remap:
                    tk_app.call(path, "configure", "-foreground", remap[current])
        except tk.TclError:
            continue


def _title_bar(window) -> None:
    """Dark or light title bar to match the theme (Windows 10 20H1 and later; ignored elsewhere)."""
    if current_theme is None:
        return
    try:
        import ctypes
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        value = ctypes.c_int(1 if THEMES[current_theme]["dark"] else 0)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(value), ctypes.sizeof(value))
        # Repaint the frame: SWP_NOSIZE | SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED
        ctypes.windll.user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x1 | 0x2 | 0x4 | 0x10 | 0x20)
    except (AttributeError, OSError, tk.TclError):
        pass
