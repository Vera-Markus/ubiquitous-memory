"""
Ships tab ▸ right-click ▸ Onboard These Ships… (1.7.2 plan, 32.1).

Several ships of one hull, shown as one line ("12 × Ferox · Vera · Jita IV-4"), get one
fitting, one owner and one Home in a single Apply. Any saved fitting of the hull can be
chosen (or <Personal>); no doctrine or requirement has to exist. Cancel changes nothing.
The work is app/services/onboarding.onboard_ships.
"""
import tkinter as tk
from tkinter import ttk
from typing import Callable, Dict, List, Optional

from app.gui import style as ui_style
from app.gui.type_ahead import TypeAhead
from app.gui.window_placement import place_window

EACH_OWN_SYSTEM = "Leave Home empty to keep each ship's Home, or give it the system it's in."


class OnboardShipsDialog:
    def __init__(self, app, summary: str, fittings: Dict[str, Optional[int]], fitting: str,
                 owners: Dict[str, dict], owner: str, systems: List[str], home: str, anywhere: str,
                 on_apply: Callable[[Optional[int], dict, str], None]):
        """
        fittings: label -> fit_uid (None for <Personal>); owners: label -> {kind, id, name, label};
        systems: every system name. fitting, owner and home are what's shown first; home "" means
        each ship's own. on_apply(fit_uid, owner, home name or "").
        """
        self.app = app
        self.fittings = fittings
        self.owners = owners
        self.anywhere = anywhere
        self.on_apply = on_apply
        self.window = tk.Toplevel(app.root)
        self.window.title("Onboard These Ships")
        self.window.transient(app.root)
        self.window.minsize(460, 0)
        body = ttk.Frame(self.window, padding=(12, 12))
        body.pack(fill=tk.BOTH, expand=True)

        self.lbl_summary = ttk.Label(body, text=summary, font=(ui_style.FONT_FAMILY, 10, "bold"))
        self.lbl_summary.grid(row=0, column=0, columnspan=2, sticky=tk.W, pady=(0, 10))

        ttk.Label(body, text="Fitting:").grid(row=1, column=0, sticky=tk.W, padx=(0, 8), pady=3)
        self.fit_combo = ttk.Combobox(body, state="readonly", width=40, values=list(fittings))
        self.fit_combo.set(fitting)
        self.fit_combo.grid(row=1, column=1, sticky=tk.EW, pady=3)
        self.fit_combo.bind("<<ComboboxSelected>>", lambda e: self._refresh())

        ttk.Label(body, text="Owner:").grid(row=2, column=0, sticky=tk.W, padx=(0, 8), pady=3)
        self.owner_combo = ttk.Combobox(body, state="readonly", width=40, values=list(owners))
        self.owner_combo.set(owner)
        self.owner_combo.grid(row=2, column=1, sticky=tk.EW, pady=3)

        ttk.Label(body, text="Home:").grid(row=3, column=0, sticky=tk.W, padx=(0, 8), pady=3)
        self.home_combo = ttk.Combobox(body, width=40)
        self.home_combo.grid(row=3, column=1, sticky=tk.EW, pady=3)
        # Two labels: the type-ahead's match count, and what an empty Home means (or a typing mistake).
        self.lbl_matches = ttk.Label(body, text="", style=ui_style.HINT_LABEL)
        self.lbl_matches.grid(row=4, column=1, sticky=tk.W)
        self.lbl_home = ttk.Label(body, text=EACH_OWN_SYSTEM, style=ui_style.HINT_LABEL, wraplength=380,
                                  justify=tk.LEFT)
        self.lbl_home.grid(row=5, column=1, sticky=tk.W)
        self.home_ahead = TypeAhead(self.home_combo, lambda name: None, noun="system", hint=self.lbl_matches,
                                    pinned=[anywhere])
        self.home_ahead.set_choices([anywhere] + list(systems), current=home or None)
        self.home_combo.set(home)
        body.columnconfigure(1, weight=1)

        buttons = ttk.Frame(body)
        buttons.grid(row=6, column=0, columnspan=2, sticky=tk.E, pady=(12, 0))
        ttk.Button(buttons, text="Cancel", command=self.close).pack(side=tk.LEFT, padx=5)
        self.btn_apply = ttk.Button(buttons, text="Apply", command=self.apply)
        self.btn_apply.pack(side=tk.LEFT, padx=5)
        self._refresh()
        try:
            self.window.grab_set()
        except tk.TclError:
            pass
        place_window(self.window)

    def _personal(self) -> bool:
        return self.fit_combo.get() in self.fittings and self.fittings[self.fit_combo.get()] is None

    def _refresh(self):
        """<Personal> ships have no Home: the box is greyed out for them."""
        self.home_combo.config(state="disabled" if self._personal() else "normal")

    def home_name(self) -> Optional[str]:
        """The Home typed: a listed name, "" for each ship's own, or None when it matches nothing."""
        text = self.home_combo.get().strip()
        if not text or self._personal():
            return ""
        return self.home_ahead.exact(text)

    def apply(self):
        if self.fit_combo.get() not in self.fittings or self.owner_combo.get() not in self.owners:
            return
        home = self.home_name()
        if home is None:
            self.lbl_home.config(text=f"No system called {self.home_combo.get().strip()!r}.",
                                 foreground=ui_style.ERROR)
            return
        fit_uid = self.fittings[self.fit_combo.get()]
        owner = self.owners[self.owner_combo.get()]
        self.close()
        self.on_apply(fit_uid, owner, home)

    def close(self):
        self.window.destroy()

    def describe(self) -> dict:
        return {"title": self.window.title(), "summary": self.lbl_summary["text"],
                "fittings": list(self.fit_combo["values"]), "fitting": self.fit_combo.get(),
                "owners": list(self.owner_combo["values"]), "owner": self.owner_combo.get(),
                "home": self.home_combo.get(), "home_enabled": str(self.home_combo["state"]) != "disabled"}
