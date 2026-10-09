"""
Onboard Ships Here and Adopt Ships Here (homes and priorities plan, step 24.2): the previews
the user confirms. Nothing changes until Apply (Q1); the work is in app/services/onboarding.py.

- Onboard: one row per new ship, a fitting picker (the fittings required here for its hull,
  Skip and <Personal>) and how the ship would audit with the chosen fitting. Next (when there
  are any): hulls with no saved fitting at all, ticked, to mark <Personal> (D2).
  **Owner: a corporation** (1.7.2 plan, 31): ticked, every ship onboarded here gets the chosen
  corporation as owner; each picker then offers every saved fitting of the hull, and ships
  whose hull nothing here requires (but has a saved fitting) join the list.
- Adopt: one row per ship with a fitting and a Home elsewhere (or none), ticked; ships whose
  fitting nothing here uses are shown but can't be ticked (D3). The warning under the list
  says which systems would be left short, and follows the ticks.
"""
import tkinter as tk
from tkinter import ttk
from typing import Callable, Dict, Iterable, List, Optional

from app.gui import style as ui_style
from app.gui.window_placement import place_window
from app.services.onboarding import PERSONAL, SKIP, SystemShips

ONBOARD_TEXT = ("New ships in {system} with no fitting. Each gets the fitting you choose, its owner, and "
                "{system} as its Home. Skip leaves a ship as it is; <Personal> means the audit never looks at it.")
CORPORATION_TEXT = "Owner: a corporation"
CORPORATION_HINT = ("Every ship onboarded here belongs to the corporation, and any saved fitting of its hull can be "
                    "chosen. Corporation ships don't count for a character's requirements.")
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
    def __init__(self, app, plan: SystemShips,
                 on_apply: Callable[[Dict[int, str], Iterable[int], Optional[dict]], None],
                 corporations: Optional[List[dict]] = None):
        """corporations: the owners the corporation toggle offers, each {kind, id, name, label}."""
        super().__init__(app, f"Onboard ships in {plan.system_name}")
        self.plan = plan
        self.on_apply = on_apply
        self.corporations = {c["label"]: c for c in corporations or []}
        self.choices: Dict[int, tk.StringVar] = {}
        self.boxes: Dict[int, ttk.Combobox] = {}
        self.previews: Dict[int, ttk.Label] = {}
        self.row_widgets: Dict[int, tuple] = {}
        self.personal: Dict[int, tk.BooleanVar] = {}
        self.page = 1

        self.page1 = ttk.Frame(self.body)
        ttk.Label(self.page1, justify=tk.LEFT, wraplength=700,
                  text=ONBOARD_TEXT.format(system=plan.system_name)).pack(fill=tk.X)
        owner_row = ttk.Frame(self.page1)
        owner_row.pack(fill=tk.X, pady=(8, 0))
        self.corporation_var = tk.BooleanVar(value=False)
        self.chk_corporation = ttk.Checkbutton(owner_row, text=CORPORATION_TEXT, variable=self.corporation_var,
                                               command=self._on_corporation_toggle)
        self.chk_corporation.pack(side=tk.LEFT)
        self.corporation_combo = ttk.Combobox(owner_row, state="disabled", width=36, values=list(self.corporations))
        self.corporation_combo.pack(side=tk.LEFT, padx=(8, 0))
        self.corporation_combo.bind("<<ComboboxSelected>>", lambda e: self._refresh_apply())
        if len(self.corporations) == 1:
            self.corporation_combo.set(next(iter(self.corporations)))
        if not self.corporations:
            self.chk_corporation.state(["disabled"])
        self.lbl_corporation = ttk.Label(self.page1, text="", justify=tk.LEFT, wraplength=700,
                                         style=ui_style.HINT_LABEL)
        self.lbl_corporation.pack(fill=tk.X)
        self.rows = _Rows(self.page1).frame
        self.lbl_none = ttk.Label(self.rows, text="No new ships here.", style=ui_style.HINT_LABEL)
        for index, ship in enumerate(plan.onboard + plan.other):
            label = ttk.Label(self.rows, text=ship.label())
            var = tk.StringVar(value=ship.default)
            self.choices[ship.item_id] = var
            box = ttk.Combobox(self.rows, state="readonly", width=28, textvariable=var)
            self.boxes[ship.item_id] = box
            preview = ttk.Label(self.rows, text="", style=ui_style.HINT_LABEL)
            self.previews[ship.item_id] = preview
            label.grid(row=index + 1, column=0, sticky=tk.W, padx=(0, 10), pady=2)
            box.grid(row=index + 1, column=1, sticky=tk.W, padx=(0, 10))
            preview.grid(row=index + 1, column=2, sticky=tk.W)
            self.row_widgets[ship.item_id] = (label, box, preview)
            var.trace_add("write", lambda *_, s=ship: self._show_preview(s))
        self._show_rows()

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
        self._refresh_apply()
        self._show()

    def owner(self) -> Optional[dict]:
        """The corporation every ship onboarded here goes to, or None (each ship's holder)."""
        return self.corporations.get(self.corporation_combo.get()) if self.corporation_var.get() else None

    def _on_corporation_toggle(self):
        on = self.corporation_var.get()
        self.corporation_combo.config(state="readonly" if on else "disabled")
        self.lbl_corporation.config(text=CORPORATION_HINT if on else "")
        self._show_rows()

    def _offered(self, ship):
        """(fit_uid, name) the ship's picker offers: every saved fitting with a corporation owner."""
        return ship.all_fittings if self.corporation_var.get() else ship.fittings

    def _show_rows(self):
        """The rows and their pickers follow the corporation toggle; plan.other's ships only show while it's on."""
        on = self.corporation_var.get()
        shown = 0
        for ship in self.plan.onboard + self.plan.other:
            widgets = self.row_widgets[ship.item_id]
            if ship in self.plan.other and not on:
                for widget in widgets:
                    widget.grid_remove()
                continue
            for widget in widgets:
                widget.grid()
            shown += 1
            offered = [name for _, name in self._offered(ship)] + [SKIP, PERSONAL]
            self.boxes[ship.item_id]["values"] = offered
            var = self.choices[ship.item_id]
            if var.get() and var.get() not in offered:
                var.set(ship.default)        # a fitting only the corporation could have, after switching back
            self._show_preview(ship)
        if shown:
            self.lbl_none.grid_remove()
        else:
            self.lbl_none.grid(row=0, column=0, sticky=tk.W)
        self._refresh_apply()

    def _refresh_apply(self):
        """With the corporation toggle on, Apply and Next wait for a corporation."""
        ready = not self.corporation_var.get() or self.owner() is not None
        for button in (getattr(self, "btn_apply", None), getattr(self, "btn_next", None)):
            if button is not None:
                button.config(state=tk.NORMAL if ready else tk.DISABLED)

    def _show_preview(self, ship):
        name = self.choices[ship.item_id].get()
        uid = next((u for u, n in self._offered(ship) if n == name), None)
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

    def _shown(self):
        """The ships listed: plan.other's only while a corporation is the owner."""
        return self.plan.onboard + (self.plan.other if self.corporation_var.get() else [])

    def chosen(self):
        """(item ID -> fitting name, SKIP or PERSONAL; item IDs to mark Personal on the Next page)."""
        return ({s.item_id: self.choices[s.item_id].get() or SKIP for s in self._shown()},
                [i for i, v in self.personal.items() if v.get()])

    def set_corporation(self, label: Optional[str]):
        """Ticks the toggle and picks the corporation; None unticks it (for the harness)."""
        self.corporation_var.set(label is not None)
        if label is not None:
            self.corporation_combo.set(label)
        self._on_corporation_toggle()

    def apply(self):
        if self.corporation_var.get() and self.owner() is None:
            return
        choices, personal = self.chosen()
        owner = self.owner()
        self.close()
        self.on_apply(choices, personal, owner)

    def describe(self) -> dict:
        choices, personal = self.chosen()
        owner = self.owner()
        return {"title": self.window.title(), "page": self.page,
                "corporation": {"offered": list(self.corporations), "ticked": self.corporation_var.get(),
                                "chosen": owner["label"] if owner else None},
                "rows": [{"ship": s.label(), "offered": list(self.boxes[s.item_id]["values"]),
                          "chosen": choices[s.item_id], "preview": self.previews[s.item_id]["text"]}
                         for s in self._shown()],
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
