"""
The Capital Contract Search window (ESI features plan 28.6, O3; C1-C3).

A capital hull is never bought on the market: this window looks for it in contracts within
five capital jumps of a centre. The centre is a system dropdown, starting where the chosen
character is (C2); changing either searches again. Alliance contracts come first, then public
ones, nearest first, filling in as their items are read. The jumps slider (1 to 5) narrows the
list at once, with no new calls. Open in Game opens the selected contract in the chosen
character's client, one at a time, with Open Next through the rest (28.5).
"""
import threading
import tkinter as tk
from tkinter import ttk
from typing import List, Optional

from app.gui import style as ui_style
from app.gui.dialogs.send_to_client_dialog import run_async
from app.gui.type_ahead import TypeAhead
from app.gui.window_placement import place_window
from app.services import esi_features, prices
from app.services.capitals import MAX_JUMPS, Found, search

COLUMNS = (("match", "Match", 190, tk.W), ("price", "Price", 90, tk.E), ("jumps", "Jumps", 55, tk.E),
           ("ly", "Light-years", 80, tk.E), ("system", "System", 110, tk.W), ("where", "Where", 260, tk.W),
           ("source", "Contract", 70, tk.W))


class CapitalSearchWindow:
    def __init__(self, app, hull_type_id: int, fitting: Optional[dict] = None, pilot: Optional[int] = None,
                 centre: Optional[int] = None):
        self.app = app
        self.galaxy = app.galaxy
        self.hull = int(hull_type_id)
        self.fitting = fitting
        self.found: List[Found] = []
        self.shown: List[Found] = []
        self._search_id = 0
        self._closed = False
        name = app.evedb_loader.get_type_name(self.hull)
        self.window = tk.Toplevel(app.root)
        self.window.title(f"Find a Hull: {name}")
        self.window.transient(app.root)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        body = ttk.Frame(self.window, padding=(12, 10))
        body.pack(fill=tk.BOTH, expand=True)

        reach = self.galaxy.jump_range(self.hull)
        ttk.Label(body, text=(f"{name}" + (f" for {fitting.get('fit_name')}" if fitting else "")
                              + f" · {reach:.1f} ly a jump (Jump Drive Calibration V)"),
                  font=(ui_style.FONT_FAMILY, 10, "bold")).pack(anchor=tk.W)
        self.public = esi_features.enabled("public_contracts")
        ttk.Label(body, text=("Contracts within 5 jumps: alliance first, then public. " if self.public else
                              "Alliance contracts within 5 jumps (public ones: Options ▸ ESI Features). ")
                  + "Jumps are a straight-line estimate.", style=ui_style.HINT_LABEL).pack(anchor=tk.W, pady=(0, 6))

        row = ttk.Frame(body)
        row.pack(fill=tk.X)
        ttk.Label(row, text="Character:").pack(side=tk.LEFT)
        self.characters = {app.auth_service.index.get(str(c), f"Character {c}"): str(c) for c in app.auth_service.profiles}
        self.char_combo = ttk.Combobox(row, values=list(self.characters), state="readonly", width=22)
        start = next((n for n, c in self.characters.items() if c == str(pilot)), next(iter(self.characters), ""))
        self.char_combo.set(start)
        self.char_combo.pack(side=tk.LEFT, padx=(4, 12))
        self.char_combo.bind("<<ComboboxSelected>>", lambda e: self._centre_on_character())
        ttk.Label(row, text="Centre:").pack(side=tk.LEFT)
        self.centre_combo = ttk.Combobox(row, width=20)
        self.centre_combo.pack(side=tk.LEFT, padx=4)
        self._centre_ahead = TypeAhead(self.centre_combo, lambda name: self._start_search(), noun="system")
        self._systems = {v[0]: k for k, v in self.galaxy.systems.items()}
        self._centre_ahead.set_choices(sorted(self._systems, key=str.casefold))
        ttk.Button(row, text="Search", command=self._start_search).pack(side=tk.LEFT, padx=6)

        slider_row = ttk.Frame(body)
        slider_row.pack(fill=tk.X, pady=(8, 2))
        ttk.Label(slider_row, text="Within:").pack(side=tk.LEFT)
        self.slider = ttk.Scale(slider_row, from_=1, to=MAX_JUMPS, orient=tk.HORIZONTAL, length=200)
        self.slider.set(MAX_JUMPS)
        self.slider.configure(command=self._on_slider)      # after set: the label and list don't exist yet
        self.slider.pack(side=tk.LEFT, padx=6)
        self.lbl_jumps = ttk.Label(slider_row, text="")
        self.lbl_jumps.pack(side=tk.LEFT)
        self.lbl_status = ttk.Label(body, text="", style=ui_style.HINT_LABEL)
        self.lbl_status.pack(anchor=tk.W, pady=(4, 4))

        frame = ttk.Frame(body)
        frame.pack(fill=tk.BOTH, expand=True)
        self.tree = ttk.Treeview(frame, columns=[c[0] for c in COLUMNS], show="headings", height=14,
                                 selectmode="browse")
        for key, text, width, anchor in COLUMNS:
            self.tree.heading(key, text=text, anchor=anchor)
            self.tree.column(key, width=width, anchor=anchor, stretch=key == "where")
        scroll = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.bind("<Double-1>", lambda e: self._open_in_game())

        buttons = ttk.Frame(body)
        buttons.pack(anchor=tk.E, pady=(8, 0))
        self.btn_open = ttk.Button(buttons, text="Open in Game…", command=self._open_in_game)
        self.btn_open.pack(side=tk.LEFT, padx=5)
        ttk.Button(buttons, text="Close", command=self.close).pack(side=tk.LEFT, padx=5)
        self._update_jumps_label()
        place_window(self.window)
        if centre is not None and centre in self.galaxy.systems:
            self._set_centre(centre)
        else:
            self._centre_on_character()

    # --- the centre (C2) ---------------------------------------------------------------------------

    def _later(self, func, *args):
        if self._closed:
            return
        try:
            self.window.after(0, lambda: (not self._closed) and func(*args))
        except (tk.TclError, RuntimeError):
            pass

    def _centre_on_character(self):
        """Centres on where the chosen character is (asked of ESI), then searches."""
        char_id = self.characters.get(self.char_combo.get())
        if not char_id:
            self.lbl_status.config(text="Pick a centre system, then Search.")
            return
        allowed, why = self.app._may_call_ccp()
        if not allowed:
            self.lbl_status.config(text=why)
            return
        self.lbl_status.config(text=f"Asking where {self.char_combo.get()} is…")
        run_async(self.app.game_client.location(char_id), lambda system: self._later(self._located, system))

    def _located(self, system):
        if isinstance(system, int) and system in self.galaxy.systems:
            self._set_centre(system)
            return
        self.lbl_status.config(text=f"Couldn't tell where {self.char_combo.get()} is (log in again for "
                                    "location, or pick a centre system), then Search.")

    def _set_centre(self, system_id: int):
        self._centre_ahead.set_choices(self._centre_ahead.choices, self.galaxy.name(system_id))
        self._start_search()

    # --- searching ---------------------------------------------------------------------------------

    def _centre(self) -> Optional[int]:
        typed = self.centre_combo.get().strip()
        return self._systems.get(self._centre_ahead.exact(typed) or typed)

    def _start_search(self):
        centre = self._centre()
        if centre is None:
            self.lbl_status.config(text="Pick a centre system from the list.")
            return
        allowed, why = self.app._may_call_ccp()
        if not allowed:
            self.lbl_status.config(text=why)
            return
        self._search_id += 1
        search_id = self._search_id
        self.found = []
        self._show()
        audit = self.app.audit_view
        book = audit._book() or None
        alliance = list(book.alliance) if book is not None else []
        others = [f for f in self.app.fitting_manager.list_fittings() if f.get("hull_type_id") == self.hull]
        is_module = lambda t: self.app.evedb_loader.get_type_category(t) == 7       # noqa: E731

        def stopped():
            return self._closed or search_id != self._search_id

        def work():
            try:
                found = search(self.galaxy, self.hull, centre, alliance, self.app.public_contracts, self.fitting,
                               others, is_module,
                               progress=lambda text: self._later(self._progress, search_id, text),
                               on_found=lambda f: self._later(self._update, search_id, f), stopped=stopped,
                               include_public=self.public)
                self._later(self._done, search_id, found, "")
            except Exception as e:
                self._later(self._done, search_id, None, f"The search stopped: {e}")
        threading.Thread(target=work, daemon=True).start()

    def _progress(self, search_id, text):
        if search_id == self._search_id:
            self.lbl_status.config(text=text)

    def _update(self, search_id, found):
        if search_id == self._search_id:
            self.found = found
            self._show()

    def _done(self, search_id, found, error):
        if search_id != self._search_id:
            return
        if found is not None:
            self.found = found
        self._show()
        n = len(self.found)
        self.lbl_status.config(text=error or (f"{n} contract{'s' if n != 1 else ''} within {MAX_JUMPS} jumps of "
                                              f"{self.galaxy.name(self._centre())}."
                                              if n else f"No contract holds this hull within {MAX_JUMPS} jumps."))

    # --- the list ----------------------------------------------------------------------------------

    def _limit(self) -> int:
        return max(1, min(MAX_JUMPS, int(round(float(self.slider.get())))))

    def _update_jumps_label(self):
        limit = self._limit()
        self.lbl_jumps.config(text=f"{limit} jump{'s' if limit != 1 else ''} "
                                   f"({limit * self.galaxy.jump_range(self.hull):.1f} ly)")

    def _on_slider(self, value=None):
        self._update_jumps_label()
        self._show()

    def _show(self):
        """The results within the slider's jumps (structures the app can't place, last)."""
        limit = self._limit()
        self.shown = [f for f in self.found if f.jumps is None or f.jumps <= limit]
        self.tree.delete(*self.tree.get_children())
        for f in self.shown:
            self.tree.insert("", tk.END, values=self.row(f))
        self.btn_open.config(state=tk.NORMAL if self.shown else tk.DISABLED)

    def row(self, f: Found):
        name = self.app.evedb_loader.get_type_name
        label = f.label
        if f.missing:
            label += " (missing " + ", ".join(name(t) for t in f.missing) + ")"
        c = f.contract
        return (label, prices.isk(c.price), "?" if f.jumps is None else f.jumps,
                "?" if f.light_years is None else f"{f.light_years:.1f}", self.galaxy.name(c.system_id),
                self.app.evedb_loader.location_label(c.location_id), c.source)

    def _open_in_game(self):
        if not self.shown:
            return
        selected = self.tree.selection()
        start = self.tree.index(selected[0]) if selected else 0
        steps = [(" · ".join(str(v) for v in self.row(f)[:5]),
                  (lambda cid: lambda client, char_id: client.open_contract(char_id, cid))(f.contract.contract_id))
                 for f in self.shown]
        self.app.open_in_game("Open Contract", steps[start][0], steps[start][1],
                              self.characters.get(self.char_combo.get()), steps=steps, start=start)

    def describe(self) -> dict:
        """What the window shows, for the GUI harness."""
        return {"title": self.window.title(), "character": self.char_combo.get(), "centre": self.centre_combo.get(),
                "jumps": self.lbl_jumps.cget("text"), "status": self.lbl_status.cget("text"),
                "rows": [list(map(str, self.tree.item(i, "values"))) for i in self.tree.get_children()]}

    def close(self):
        self._closed = True
        self.window.destroy()
