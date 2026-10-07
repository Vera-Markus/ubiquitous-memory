"""
Onboard Ships Here and Adopt Ships Here (homes and priorities plan, step 24.2): the previews
the user confirms. Nothing changes until Apply (Q1); the work is in app/services/onboarding.py.

- Onboard: one row per new ship, a fitting picker (the fittings required here for its hull,
  Skip and <Personal>) and how the ship would audit with the chosen fitting. Next (when there
  are any): hulls with no saved fitting at all, ticked, to mark <Personal> (D2).
- Adopt: one row per ship with a fitting and a Home elsewhere (or none), ticked; ships whose
  fitting nothing here uses are shown but can't be ticked (D3). The warning under the list
  says which systems would be left short, and follows the ticks.
"""
import tkinter as tk
from tkinter import ttk
from typing import Callable, Dict, Iterable

from app.gui import style as ui_style
from app.gui.window_placement import place_window
from app.services.onboarding import PERSONAL, SKIP, SystemShips

ONBOARD_TEXT = ("New ships in {system} with no fitting. Each gets the fitting you choose, its owner, and "
                "{system} as its Home. Skip leaves a ship as it is; <Personal> means the audit never looks at it.")
PERSONAL_TEXT = ("Ships in {system} whose hull has no saved fitting at all. Ticked ones are marked <Personal>: "
                 "the audit never looks at them, and Ships ▸ Assigned only hides them.")
ADOPT_TEXT = ("Ships in {system} whose Home is somewhere else, or not set. Ticked ones get {system} as their "
              "Home; their fitting doesn't change. Ships whose fitting nothing in {system} uses can't be adopted.")


class _Rows:
    """A scrolling area for rows of widgets."""

    def __init__(self, parent, height=320):
        p = ui_style.THEMES.get(ui_style.current_theme or ui_style.DEFAULT_THEME, ui_style.THEMES[ui_style.DEFAULT_THEME])
        outer = ttk.Frame(parent)
        outer.pack(fill=tk.BOTH, expand=True, pady=10)
        self.canvas = tk.Canvas(outer, height=height, highlightthickness=0, background=p["bg"])
        bar = ttk.Scrollbar(outer, orient=tk.VERTICAL, command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=bar.set)
        bar.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.frame = ttk.Frame(self.canvas)
        self.canvas.create_window((0, 0), window=self.frame, anchor=tk.NW)
        self.frame.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))


class _Dialog:
    def __init__(self, app, title: str):
        self.app = app
        self.window = tk.Toplevel(app.root)
        self.window.title(title)
        self.window.transient(app.root)
        self.window.minsize(720, 300)
        self.body = ttk.Frame(self.window, padding=(10, 10))
        self.body.pack(fill=tk.BOTH, expand=True)

    def _show(self):
        try:
            self.window.grab_set()
        except tk.TclError:
            pass
        place_window(self.window)

    def close(self):
        self.window.destroy()


class OnboardDialog(_Dialog):
    def __init__(self, app, plan: SystemShips, on_apply: Callable[[Dict[int, str], Iterable[int]], None]):
        super().__init__(app, f"Onboard ships in {plan.system_name}")
        self.plan = plan
        self.on_apply = on_apply
        self.choices: Dict[int, tk.StringVar] = {}
        self.previews: Dict[int, ttk.Label] = {}
        self.personal: Dict[int, tk.BooleanVar] = {}
        self.page = 1

        self.page1 = ttk.Frame(self.body)
        ttk.Label(self.page1, justify=tk.LEFT, wraplength=700,
                  text=ONBOARD_TEXT.format(system=plan.system_name)).pack(fill=tk.X)
        rows = _Rows(self.page1).frame
        if not plan.onboard:
            ttk.Label(rows, text="No new ships here.", style=ui_style.HINT_LABEL).grid(row=0, column=0, sticky=tk.W)
        for index, ship in enumerate(plan.onboard):
            ttk.Label(rows, text=ship.label()).grid(row=index, column=0, sticky=tk.W, padx=(0, 10), pady=2)
            var = tk.StringVar(value=ship.default)
            self.choices[ship.item_id] = var
            box = ttk.Combobox(rows, state="readonly", width=28, textvariable=var,
                               values=[name for _, name in ship.fittings] + [SKIP, PERSONAL])
            box.grid(row=index, column=1, sticky=tk.W, padx=(0, 10))
            preview = ttk.Label(rows, text="", style=ui_style.HINT_LABEL)
            preview.grid(row=index, column=2, sticky=tk.W)
            self.previews[ship.item_id] = preview
            var.trace_add("write", lambda *_, s=ship: self._show_preview(s))
            self._show_preview(ship)

        self.page2 = ttk.Frame(self.body)
        ttk.Label(self.page2, justify=tk.LEFT, wraplength=700,
                  text=PERSONAL_TEXT.format(system=plan.system_name)).pack(fill=tk.X)
        rows = _Rows(self.page2).frame
        for index, ship in enumerate(plan.personal):
            var = tk.BooleanVar(value=True)
            self.personal[ship.item_id] = var
            ttk.Checkbutton(rows, text=ship.label(), variable=var).grid(row=index, column=0, sticky=tk.W, pady=2)

        self.buttons = ttk.Frame(self.body)
        self.btn_cancel = ttk.Button(self.buttons, text="Cancel", command=self.close)
        self.btn_back = ttk.Button(self.buttons, text="Back", command=lambda: self._go(1))
        self.btn_next = ttk.Button(self.buttons, text="Next", command=lambda: self._go(2))
        self.btn_apply = ttk.Button(self.buttons, text="Apply", command=self.apply)
        self._go(1)
        self._show()

    def _show_preview(self, ship):
        name = self.choices[ship.item_id].get()
        uid = next((u for u, n in ship.fittings if n == name), None)
        self.previews[ship.item_id].config(text=ship.previews.get(uid, "") if uid is not None else
                                           {PERSONAL: "never audited", SKIP: "left as it is"}.get(name, "choose a fitting"))

    def _go(self, page: int):
        self.page = page
        for frame in (self.page1, self.page2, self.buttons):
            frame.pack_forget()
        (self.page1 if page == 1 else self.page2).pack(fill=tk.BOTH, expand=True)
        self.buttons.pack(anchor=tk.E)
        for button in (self.btn_cancel, self.btn_back, self.btn_next, self.btn_apply):
            button.pack_forget()
        shown = [self.btn_cancel]
        if page == 2:
            shown.append(self.btn_back)
        shown.append(self.btn_next if page == 1 and self.plan.personal else self.btn_apply)
        for button in shown:
            button.pack(side=tk.LEFT, padx=5)

    def chosen(self):
        """(item ID -> fitting name, SKIP or PERSONAL; item IDs to mark Personal on the Next page)."""
        return ({i: v.get() or SKIP for i, v in self.choices.items()},
                [i for i, v in self.personal.items() if v.get()])

    def apply(self):
        choices, personal = self.chosen()
        self.close()
        self.on_apply(choices, personal)

    def describe(self) -> dict:
        choices, personal = self.chosen()
        return {"title": self.window.title(), "page": self.page,
                "rows": [{"ship": s.label(), "offered": [n for _, n in s.fittings] + [SKIP, PERSONAL],
                          "chosen": choices[s.item_id], "preview": self.previews[s.item_id]["text"]}
                         for s in self.plan.onboard],
                "personal": [{"ship": s.label(), "ticked": s.item_id in personal} for s in self.plan.personal]}


class AdoptDialog(_Dialog):
    def __init__(self, app, plan: SystemShips, warnings: Callable[[Iterable[int]], list],
                 on_apply: Callable[[Iterable[int]], None]):
        super().__init__(app, f"Adopt ships in {plan.system_name}")
        self.plan = plan
        self.warnings = warnings
        self.on_apply = on_apply
        self.ticks: Dict[int, tk.BooleanVar] = {}
        ttk.Label(self.body, justify=tk.LEFT, wraplength=700,
                  text=ADOPT_TEXT.format(system=plan.system_name)).pack(fill=tk.X)
        rows = _Rows(self.body, height=260).frame
        if not plan.adopt:
            ttk.Label(rows, text="No ships to adopt here.", style=ui_style.HINT_LABEL).grid(row=0, column=0, sticky=tk.W)
        for index, ship in enumerate(plan.adopt):
            var = tk.BooleanVar(value=ship.required)
            self.ticks[ship.item_id] = var
            home = f"Home {ship.home_name}" if ship.home_name else "no Home"
            check = ttk.Checkbutton(rows, text=f"{ship.label()} · {ship.fit_name} · {home}", variable=var,
                                    command=self._refresh)
            check.grid(row=index, column=0, sticky=tk.W, pady=2)
            if not ship.required:
                check.state(["disabled"])
                ttk.Label(rows, text=f"nothing in {plan.system_name} uses {ship.fit_name}",
                          style=ui_style.HINT_LABEL).grid(row=index, column=1, sticky=tk.W, padx=(10, 0))
        self.lbl_warning = ttk.Label(self.body, justify=tk.LEFT, wraplength=700, foreground=ui_style.WARN)
        self.lbl_warning.pack(fill=tk.X, pady=(0, 10))
        buttons = ttk.Frame(self.body)
        buttons.pack(anchor=tk.E)
        ttk.Button(buttons, text="Cancel", command=self.close).pack(side=tk.LEFT, padx=5)
        self.btn_apply = ttk.Button(buttons, text="Apply", command=self.apply)
        self.btn_apply.pack(side=tk.LEFT, padx=5)
        self._refresh()
        self._show()

    def chosen(self):
        allowed = {s.item_id for s in self.plan.adopt if s.required}
        return [i for i, v in self.ticks.items() if v.get() and i in allowed]

    def _refresh(self):
        lines = self.warnings(self.chosen())
        self.lbl_warning.config(text=("Adopting these leaves other systems short:\n" + "\n".join(f"• {l}" for l in lines))
                                if lines else "")
        self.btn_apply.config(state=tk.NORMAL if self.chosen() else tk.DISABLED)

    def toggle(self, item_id: int):
        var = self.ticks[item_id]
        var.set(not var.get())
        self._refresh()

    def apply(self):
        chosen = self.chosen()
        self.close()
        self.on_apply(chosen)

    def describe(self) -> dict:
        chosen = set(self.chosen())
        return {"title": self.window.title(),
                "rows": [{"ship": s.label(), "fitting": s.fit_name, "home": s.home_name, "can_adopt": s.required,
                          "ticked": s.item_id in chosen} for s in self.plan.adopt],
                "warning": self.lbl_warning["text"]}
