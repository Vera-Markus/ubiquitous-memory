"""
Message boxes and the name prompt drawn in the app's colour theme, in place of Windows'
own (always white) ones. The same calls as tkinter.messagebox and simpledialog.askstring,
so a module imports this as either:

    from app.gui import themed_dialogs as messagebox
    messagebox.askyesno("Delete Fitting", "Delete it?")      # True / False
    from app.gui import themed_dialogs as simpledialog
    simpledialog.askstring("Rename", "New name:", initialvalue=old)   # text, or None

Each opens centred on the app, waits for an answer and returns it like the originals:
Return presses the highlighted button, Escape or the close box cancels, Ctrl+C copies
the message. With no Tk window yet, the Windows boxes are used.

link=(text, url) adds a clickable line under the message that opens the page in the
browser (1.7.2 plan, 33.4: the patch notes); the Windows boxes get the address instead.
"""
import tkinter as tk
import tkinter.messagebox as _native
import tkinter.simpledialog as _native_simple
from tkinter import ttk
from typing import Any, List, Optional, Sequence, Tuple

from app.gui import style as ui_style
from app.gui.window_placement import place_window

WRAP = 460          # px: longer lines wrap

# The symbol beside the message, and its theme colour (read at the call: it follows the theme).
_ICONS = {"info": ("i", "INFO"), "warning": ("!", "WARN"), "error": ("×", "ERROR"), "question": ("?", "INFO")}


def _owner(parent: Optional[tk.Misc]) -> Optional[tk.Misc]:
    owner = parent if parent is not None else getattr(tk, "_default_root", None)
    try:
        return owner.winfo_toplevel() if owner is not None else None
    except tk.TclError:
        return None


class _Dialog:
    """One themed box: an icon, the message, optional entry, and buttons (label, value)."""

    def __init__(self, owner: tk.Misc, title: str, message: str, icon: str,
                 buttons: Sequence[Tuple[str, Any]], default: Any, cancel: Any,
                 entry_text: Optional[str] = None, link: Optional[Tuple[str, str]] = None):
        self.value = cancel
        self.cancel = cancel
        self.message = message
        self.window = window = tk.Toplevel(owner)
        window.title(title)
        window.resizable(False, False)
        if owner.winfo_viewable():
            window.transient(owner)          # not on a hidden window: the box would be hidden too
        body = ttk.Frame(window, padding=(16, 14))
        body.pack(fill=tk.BOTH, expand=True)
        symbol, colour = _ICONS.get(icon, _ICONS["info"])
        ttk.Label(body, text=symbol, foreground=getattr(ui_style, colour), width=2, anchor=tk.CENTER,
                  font=(ui_style.FONT_FAMILY, 20, "bold")).grid(row=0, column=0, rowspan=2, sticky=tk.N, padx=(0, 12))
        ttk.Label(body, text=message, wraplength=WRAP, justify=tk.LEFT).grid(row=0, column=1, sticky=tk.W)
        self.link = None
        if link is not None:
            text, url = link
            self.link = ttk.Label(body, text=text, foreground=ui_style.INFO, cursor="hand2",
                                  font=(ui_style.FONT_FAMILY, 9, "underline"))
            self.link.grid(row=1, column=1, sticky=tk.W, pady=(8, 0))
            self.link.bind("<Button-1>", lambda e: _open(url))
            self.message = f"{message}\n\n{text}: {url}"
        self.entry = None
        if entry_text is not None:
            self.entry = ttk.Entry(body, width=44)
            self.entry.insert(0, entry_text)
            self.entry.select_range(0, tk.END)
            self.entry.grid(row=1, column=1, sticky="ew", pady=(8, 0))
        row = ttk.Frame(body)
        row.grid(row=2, column=0, columnspan=2, sticky=tk.E, pady=(14, 0))
        self.buttons = {}
        default_button = None
        for label, value in buttons:
            button = ttk.Button(row, text=label, command=lambda v=value: self.close(v))
            button.pack(side=tk.LEFT, padx=(6, 0))
            self.buttons[label] = button
            if value == default:
                default_button = button
        window.bind("<Return>", lambda e: self.close(default))
        window.bind("<Escape>", lambda e: self.close(cancel))
        window.bind("<Control-c>", self._copy)
        window.protocol("WM_DELETE_WINDOW", lambda: self.close(cancel))
        place_window(window)
        (self.entry or default_button or window).focus_set()

    def _copy(self, _event=None):
        if self.entry is not None and self.entry.selection_present():
            return None          # the entry copies its own selection
        self.window.clipboard_clear()
        self.window.clipboard_append(self.message)
        return "break"

    def close(self, value: Any) -> None:
        if value is not self.cancel and self.entry is not None:
            value = self.entry.get()
        self.value = value
        self.window.destroy()

    def run(self) -> Any:
        try:
            self.window.grab_set()
        except tk.TclError:
            pass             # another window holds the grab; the box still waits for its answer
        self.window.wait_window()
        return self.value


def _open(url: str) -> None:
    import webbrowser
    webbrowser.open(url)


def _ask(title, message, icon, buttons: List[Tuple[str, Any]], default, cancel, native, parent=None,
         link: Optional[Tuple[str, str]] = None, **kw):
    owner = _owner(parent)
    if owner is None:
        if link is not None:
            message = f"{message or ''}\n\n{link[0]}: {link[1]}"
        return native(title, message, **({"parent": parent} if parent is not None else {}), **kw)
    # Silent: no system sound as a box opens.
    return _Dialog(owner, title or "", message or "", icon, buttons, default, cancel, link=link).run()


def showinfo(title=None, message=None, **options) -> str:
    return _ask(title, message, "info", [("OK", "ok")], "ok", "ok", _native.showinfo, **options)


def showwarning(title=None, message=None, **options) -> str:
    return _ask(title, message, "warning", [("OK", "ok")], "ok", "ok", _native.showwarning, **options)


def showerror(title=None, message=None, **options) -> str:
    return _ask(title, message, "error", [("OK", "ok")], "ok", "ok", _native.showerror, **options)


def askyesno(title=None, message=None, **options) -> bool:
    default = options.pop("default", "yes") == "yes"
    return _ask(title, message, options.pop("icon", "question"), [("Yes", True), ("No", False)],
                default, False, _native.askyesno, **options)


def askokcancel(title=None, message=None, **options) -> bool:
    default = options.pop("default", "ok") == "ok"
    return _ask(title, message, options.pop("icon", "question"), [("OK", True), ("Cancel", False)],
                default, False, _native.askokcancel, **options)


def askyesnocancel(title=None, message=None, **options) -> Optional[bool]:
    default = {"yes": True, "no": False, "cancel": None}.get(options.pop("default", "yes"), True)
    return _ask(title, message, options.pop("icon", "question"),
                [("Yes", True), ("No", False), ("Cancel", None)], default, None, _native.askyesnocancel, **options)


def askstring(title, prompt, initialvalue=None, parent=None, **_options) -> Optional[str]:
    """The typed text (OK or Return), or None (Cancel, Escape or the close box)."""
    owner = _owner(parent)
    if owner is None:
        return _native_simple.askstring(title, prompt, initialvalue=initialvalue)
    return _Dialog(owner, title or "", prompt or "", "question", [("OK", "ok"), ("Cancel", None)],
                   "ok", None, entry_text=initialvalue or "").run()
