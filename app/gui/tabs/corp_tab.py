"""
Ships tab (tracked items plan, Phase 16; renamed from Corp Ships in UI thoughts plan 18.1).

Left: every ship of one holder, grouped by where it is. A holder is a linked character,
or a corporation a linked Director pulled with full read access, which acts like a
character. Right: give the selected ships a fitting (their designation), and see how the
selected ship audits against it. Designations are kept by item ID, so they follow a ship
through contracts. The data is read again each time the tab is opened (after Pull All,
for example).
"""
import logging
import tkinter as tk
from datetime import datetime, timezone
from tkinter import ttk
from app.gui import themed_dialogs as messagebox

from app import paths
from app.asset_handling.corp_pull import load_corporations
from app.gui import style as ui_style
from app.gui import eft_presenter as eft
from app.gui.audit_presenter import ICONS, Node, ship_node
from app.gui.status_icons import StatusIcons
from app.gui.type_ahead import TypeAhead
from app.loaders.ship_designations import ANYWHERE, home_system, is_anywhere
from app.gui.toggle_switch import ToggleSwitch
from app.models.audit_models import RequirementStatus
from app.services.fleet_ships import bay_of, hangar_of, holders, ship_rows
from app.services.tracking import TrackingContext

logger = logging.getLogger(__name__)

NO_FITTING = "⚪"
ASSIGNED_ONLY_SETTING = "ships_assigned_only"
EXPECTED_SETTING = "ships_eft_expected"
EFT_LEGEND = {False: "Green: in place · orange: in the wrong place · struck through: take it off",
              True: "Green: aboard · orange: aboard, in the wrong place · red: missing"}
SHIPS_SHARE = 0.75          # the ship list's share of the tab's width, at first
SHIP_COLUMNS = ("fitting", "home", "status", "owner")
PERSONAL = "<Personal>"     # first in the Fitting list: the audit never sees the ship (S5)
ANYWHERE_LABEL = "Anywhere"
STATUS_TEXT = {RequirementStatus.PASS: "Ready", RequirementStatus.WARN: "Ready, needs attention",
               RequirementStatus.FAIL: "Not ready"}


def ship_label(row, nested: bool = False) -> str:
    """'✅ Devoter "Old Faithful"'; a carried ship says where aboard: '⚪ Heron · ship maintenance bay'
    under its carrier (nested), or "· ship maintenance bay of 'Big Brother'" when it isn't listed."""
    status = row.result.status if row.result is not None else None
    icon = ICONS[status] if status is not None else NO_FITTING
    name = f'{row.hull} "{row.custom_name}"' if row.custom_name else row.hull
    aboard = ""
    if row.carrier_item_id is not None:
        aboard = bay_of(row.aboard) if nested else row.aboard.split(" / ")[-1]
    return f"{icon} {name}" + (f" · {aboard}" if aboard else "")


def counted(text: str, count: int) -> str:
    return f"{text} ({count})"


def owner_text(row) -> str:
    """The owner's name; "↩ M-M-F" when someone else holds the ship (give it back)."""
    if row.owner is None:
        return ""
    return f"↩ {row.owner_name}" if row.away_from_owner else row.owner_name


def home_text(row, system_name) -> str:
    """The Home column: the system ("↩ Jita" when the ship is elsewhere), "Anywhere", "—" for none (H7)."""
    if row.personal or row.fit_uid is None:
        return ""
    if is_anywhere(row.home):
        return ANYWHERE_LABEL
    system = home_system(row.home)
    if system is None:
        return "—"
    name = system_name(system)
    return name if row.system_id == system else f"↩ {name}"


def status_text(row) -> str:
    if row.personal:
        return "Personal"
    if row.fit_uid is None:
        return "No fitting"
    if row.result is None:
        return row.note or "Not audited"
    return STATUS_TEXT[row.result.status]


class CorpTab:
    def __init__(self, app, frame):
        self.app = app
        self.frame = frame
        self._holders = {}              # label -> holder
        self._rows = {}                 # tree item -> ShipRow
        self._fit_choices = {}          # Fitting dropdown label -> fit_uid (PERSONAL -> None)
        self._systems = {}              # solar system name -> ID, for the Home box
        self._system_names = {}         # ID -> name
        self._context = None
        self._setup()

    def _log(self, message: str):
        self.app._log(message)

    # --- layout ---------------------------------------------------------------------------

    def _setup(self):
        top = ttk.Frame(self.frame)
        top.pack(fill=tk.X, padx=10, pady=10)
        ttk.Label(top, text="Ships of:").pack(side=tk.LEFT, padx=5)
        self.holder_combo = ttk.Combobox(top, state="readonly", width=40)
        self.holder_combo.pack(side=tk.LEFT, padx=5)
        self.holder_combo.bind("<<ComboboxSelected>>", lambda e: self._load_ships())
        ttk.Label(top, text="A corporation is listed when a linked Director can read its hangars.",
                  style=ui_style.HINT_LABEL).pack(side=tk.LEFT, padx=15)

        paned = tk.PanedWindow(self.frame, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        # Ships 75%, the fitting and audit 25%, the first time the tab is shown; the divider can be dragged.
        self._split_placed = False
        paned.bind("<Configure>", lambda e: self._place_split(paned, e.width))

        left = ttk.LabelFrame(paned, text="Ships", padding=(5, 5))
        paned.add(left)
        # Hide ships with no fitting (UI thoughts 9, plan 17.5); remembered between sessions.
        switch_row = ttk.Frame(left)
        switch_row.pack(fill=tk.X, pady=(0, 4))
        self.assigned_only = ToggleSwitch(
            switch_row, "Assigned only",
            variable=tk.BooleanVar(value=bool(ui_style.load_setting(paths.CONFIG_DIR, ASSIGNED_ONLY_SETTING, False))),
            command=self._on_assigned_only)
        self.assigned_only.pack(side=tk.RIGHT)
        tree_frame = ttk.Frame(left)
        tree_frame.pack(fill=tk.BOTH, expand=True)
        self.ship_tree = ttk.Treeview(tree_frame, columns=SHIP_COLUMNS, selectmode="extended")
        self.ship_tree.heading("#0", text="Ship", anchor=tk.W)
        self.ship_tree.heading("fitting", text="Fitting", anchor=tk.W)
        self.ship_tree.heading("home", text="Home", anchor=tk.W)
        self.ship_tree.heading("status", text="Status", anchor=tk.W)
        self.ship_tree.heading("owner", text="Owner", anchor=tk.W)
        self.ship_tree.column("#0", width=300, stretch=True)
        self.ship_tree.column("fitting", width=160, stretch=False)
        self.ship_tree.column("home", width=100, stretch=False)
        self.ship_tree.column("status", width=140, stretch=False)
        self.ship_tree.column("owner", width=140, stretch=False)
        scroll = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.ship_tree.yview)
        self.ship_tree.configure(yscrollcommand=scroll.set)
        self.ship_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.ship_tree.bind("<<TreeviewSelect>>", lambda e: self._on_selection())
        self.ship_tree.status_icons = StatusIcons(self.ship_tree)
        self.lbl_ships_empty = ttk.Label(self.ship_tree, text="", style=ui_style.ON_LIST_LABEL)

        right = ttk.Frame(paned)
        paned.add(right)
        assign = ttk.LabelFrame(right, text="Fitting", padding=(10, 10))
        assign.pack(fill=tk.X, padx=10, pady=(0, 10))
        # Sized for a narrow pane: the text wraps to the box's width, and the dropdowns fill it.
        self.lbl_selection = ttk.Label(assign, text="Select ships on the left.", wraplength=380, justify=tk.LEFT)
        self.lbl_selection.pack(anchor=tk.W, fill=tk.X, pady=(0, 5))
        self.lbl_selection.bind("<Configure>", lambda e: self.lbl_selection.config(wraplength=max(e.width, 100)))
        self.fit_combo = ttk.Combobox(assign, state="readonly", width=20)
        self.fit_combo.pack(anchor=tk.W, fill=tk.X, pady=5)
        buttons = ttk.Frame(assign)
        buttons.pack(anchor=tk.W, pady=(5, 0))
        self.btn_assign = ttk.Button(buttons, text="Assign Fitting", command=self._handle_assign, state=tk.DISABLED)
        self.btn_assign.pack(side=tk.LEFT, padx=(0, 10))
        self.btn_clear = ttk.Button(buttons, text="Clear Fitting", command=self._handle_clear, state=tk.DISABLED)
        self.btn_clear.pack(side=tk.LEFT)
        # Who the ships belong to (plan 18.2): set to the holder on first assignment, changed here.
        owner_row = ttk.Frame(assign)
        owner_row.pack(fill=tk.X, pady=(10, 0))
        ttk.Label(owner_row, text="Owner:").pack(side=tk.LEFT, padx=(0, 5))
        self.owner_combo = ttk.Combobox(owner_row, state="disabled", width=20)
        self.owner_combo.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.owner_combo.bind("<<ComboboxSelected>>", lambda e: self._handle_owner())
        # Where the ships live (homes and priorities plan, H1): a system, or Anywhere. Never a station.
        home_row = ttk.Frame(assign)
        home_row.pack(fill=tk.X, pady=(5, 0))
        ttk.Label(home_row, text="Home:").pack(side=tk.LEFT, padx=(0, 9))
        self.home_combo = ttk.Combobox(home_row, width=20)
        self.home_combo.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._home_ahead = TypeAhead(self.home_combo, lambda name: self._handle_home(name), noun="system",
                                     pinned=[ANYWHERE_LABEL])
        self.home_combo.config(state="disabled")

        # The selected ship: its audit, and the same result in EFT layout (plan 19.2).
        self.ship_view = ttk.Notebook(right)
        self.ship_view.pack(fill=tk.BOTH, expand=True, padx=10)
        audit = ttk.Frame(self.ship_view, padding=(5, 5))
        self.ship_view.add(audit, text="Audit")
        self.audit_tree = ttk.Treeview(audit, show="tree")
        self.audit_tree.pack(fill=tk.BOTH, expand=True)
        self.audit_tree.status_icons = StatusIcons(self.audit_tree)
        eft_frame = ttk.Frame(self.ship_view, padding=(5, 5))
        self.ship_view.add(eft_frame, text="EFT")
        self.eft_switch = ToggleSwitch(
            eft_frame, "Current", text_after="Expected",
            variable=tk.BooleanVar(value=bool(ui_style.load_setting(paths.CONFIG_DIR, EXPECTED_SETTING, False))),
            command=self._on_eft_view)
        self.eft_switch.pack(anchor=tk.W, pady=(0, 4))
        self.lbl_eft_legend = ttk.Label(eft_frame, text="", style=ui_style.HINT_LABEL)
        self.lbl_eft_legend.pack(anchor=tk.W, pady=(0, 4))
        self.lbl_eft_legend.bind("<<ThemeChanged>>", lambda e: self.after_idle_eft(), add="+")
        text_frame = ttk.Frame(eft_frame)
        text_frame.pack(fill=tk.BOTH, expand=True)
        self.eft_text = tk.Text(text_frame, wrap=tk.NONE, height=10, borderwidth=0, padx=8, pady=6,
                                font=(ui_style.FONT_FAMILY, 10))
        eft_scroll = ttk.Scrollbar(text_frame, orient=tk.VERTICAL, command=self.eft_text.yview)
        self.eft_text.configure(yscrollcommand=eft_scroll.set, state=tk.DISABLED)
        self.eft_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        eft_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self._eft_row = None
        self.eft_lines = []

        # Read the data again whenever the tab is opened (Pull All, new characters, library changes).
        self.app.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed, add="+")

    def _place_split(self, paned, width):
        if self._split_placed or width < 100:       # not laid out yet
            return
        self._split_placed = True
        paned.sash_place(0, int(width * SHIPS_SHARE), 0)

    def _on_assigned_only(self):
        ui_style.save_setting(paths.CONFIG_DIR, ASSIGNED_ONLY_SETTING, self.assigned_only.get())
        self._load_ships(keep=[r.item_id for r in self._selected_rows()])

    def _on_eft_view(self):
        ui_style.save_setting(paths.CONFIG_DIR, EXPECTED_SETTING, self.eft_switch.get())
        self._show_eft(self._eft_row)

    def after_idle_eft(self):
        self.frame.after_idle(lambda: self._show_eft(self._eft_row))

    def _on_tab_changed(self, event=None):
        if self.app.notebook.select() == str(self.frame):
            self.refresh()

    # --- loading ----------------------------------------------------------------------------

    def refresh(self):
        """Holders, the context (assets and corporation pulls) and the chosen holder's ships."""
        names = {str(k): v for k, v in (getattr(self.app.auth_service, "index", {}) or {}).items()}
        corporations = load_corporations(paths.CORP_DIR)
        generated = self.app.audit_collection_service.generated_dir
        self.app.ship_designations.prune(f["fit_uid"] for f in self.app.fitting_manager.list_fittings())
        try:
            self._context = TrackingContext.load(generated, corporations, self.app.evedb_loader, None, names)
        except Exception as e:
            self._context = None
            self._log(f"[WARNING] The Ships tab couldn't read the asset data: {e}")
        if not self._systems:
            self._load_systems()
        self._holders = {h["label"]: h for h in holders(names, corporations)}
        labels = list(self._holders)
        current = self.holder_combo.get()
        self.holder_combo["values"] = labels
        self.holder_combo.set(current if current in labels else (labels[0] if labels else ""))
        self._load_ships()

    def _load_ships(self, keep=()):
        """Fills the ship tree for the chosen holder; keep: item IDs to select again."""
        self.ship_tree.delete(*self.ship_tree.get_children())
        self._rows = {}
        holder = self._holders.get(self.holder_combo.get())
        rows = []
        if holder is not None and self._context is not None:
            names = {(h["kind"], h["id"]): h["name"] for h in self._holders.values()}
            rows = ship_rows(self._context, holder, self.app.ship_designations, self.app.fitting_manager,
                             self.app.audit_engine, names)
        total = len(rows)
        if self.assigned_only.get():
            rows = [r for r in rows if r.fit_uid is not None]
        # Place, then hangar (personal hangar, corp division, deliveries...), then ship; a ship carried
        # in another listed ship nests under it (plan 19.1).
        listed = {r.item_id for r in rows}
        carriers = {}               # carrier item ID -> its rows, carried ships after their carriers
        top = []
        for row in rows:
            if row.carrier_item_id in listed:
                carriers.setdefault(row.carrier_item_id, []).append(row)
            else:
                top.append(row)
        places, hangars, counts = {}, {}, {}

        def add(row, parent, nested):
            item = self.ship_tree.status_icons.insert(parent, tk.END, text=ship_label(row, nested), open=True,
                                                      values=(PERSONAL if row.personal else row.fit_name or "—",
                                                              home_text(row, self._system_name), status_text(row),
                                                              owner_text(row)))
            self._rows[item] = row
            n = 1
            for carried in carriers.get(row.item_id, []):
                n += add(carried, item, True)
            return n

        for row in top:
            place = row.location or "Unknown location"
            if place not in places:
                places[place] = self.ship_tree.insert("", tk.END, text=place, open=True)
            hangar = (place, hangar_of(row.aboard))
            if hangar not in hangars:
                hangars[hangar] = self.ship_tree.insert(places[place], tk.END, text=hangar[1], open=True)
            n = add(row, hangars[hangar], False)
            counts[places[place]] = counts.get(places[place], 0) + n
            counts[hangars[hangar]] = counts.get(hangars[hangar], 0) + n
        for item, count in counts.items():
            self.ship_tree.item(item, text=counted(self.ship_tree.item(item, "text"), count))
        ui_style.fit_columns(self.ship_tree, SHIP_COLUMNS)
        if rows:
            self.lbl_ships_empty.place_forget()
        else:
            if holder is None:
                text = "No characters yet: add one from Characters ▸ Add Character."
            elif total:
                text = f"None of the {total} ships has a fitting assigned: turn off Assigned only to see them."
            else:
                text = "No ships."
            self.lbl_ships_empty.config(text=text)
            self.lbl_ships_empty.place(relx=0.5, rely=0.5, anchor=tk.CENTER)
        reselect = [item for item, row in self._rows.items() if row.item_id in set(keep)]
        if reselect:
            self.ship_tree.selection_set(reselect)
            self.ship_tree.see(reselect[0])
        self._on_selection()

    # --- selection, assigning ---------------------------------------------------------------------

    def _selected_rows(self):
        return [self._rows[i] for i in self.ship_tree.selection() if i in self._rows]

    def _on_selection(self):
        rows = self._selected_rows()
        hulls = {r.type_id for r in rows}
        self.audit_tree.delete(*self.audit_tree.get_children())
        self._fit_choices = {}
        if not rows:
            self.lbl_selection.config(text="Select ships on the left.")
        elif len(hulls) > 1:
            self.lbl_selection.config(text=f"{len(rows)} ships of different hulls selected: "
                                           "select ships of one hull to give them a fitting.")
        else:
            hull = rows[0].hull
            self.lbl_selection.config(text=(f"{len(rows)} {hull}s selected" if len(rows) > 1 else
                                            ship_label(rows[0])[2:]))
            fittings = sorted((f for f in self.app.fitting_manager.list_fittings()
                               if f.get("hull_type_id") == rows[0].type_id),
                              key=lambda f: (f.get("fit_name") or "").casefold())
            self._fit_choices = {f"{f.get('fit_name')} ({f['fit_uid']})": f["fit_uid"] for f in fittings}
        if rows:
            self._fit_choices = {PERSONAL: None, **self._fit_choices}       # for any hulls (S5)
        labels = list(self._fit_choices)
        self.fit_combo["values"] = labels
        if rows and all(r.personal for r in rows):
            current = PERSONAL
        else:
            assigned = {r.fit_uid for r in rows}
            current = next((label for label, uid in self._fit_choices.items()
                            if uid is not None and assigned == {uid}), None)
        fittings_offered = [label for label in labels if label != PERSONAL]
        self.fit_combo.set(current or (fittings_offered[0] if fittings_offered else ""))
        if rows and len(hulls) == 1 and not fittings_offered:
            self.lbl_selection.config(text=self.lbl_selection["text"] + f"\nNo {rows[0].hull} fitting in the "
                                                                        "library yet: import one in Fittings.")
        self.btn_assign.config(state=tk.NORMAL if labels else tk.DISABLED)
        self.btn_clear.config(state=tk.NORMAL if any(r.fit_uid is not None or r.personal for r in rows)
                              else tk.DISABLED)
        fitted = [r for r in rows if r.fit_uid is not None]
        homes = {home_text(r, self._system_name).removeprefix("↩ ") for r in fitted}
        self._home_ahead.accepted = ""
        self.home_combo.config(state="normal" if fitted else "disabled")
        self.home_combo.set(homes.pop() if len(homes) == 1 and "—" not in homes else "")
        assigned = [r for r in rows if r.fit_uid is not None or r.personal]
        self.owner_combo["values"] = list(self._holders)
        owners = {(r.owner["kind"], r.owner["id"]) for r in assigned}
        common = next((label for label, h in self._holders.items() if owners == {(h["kind"], h["id"])}), "")
        self.owner_combo.set(common)
        self.owner_combo.config(state="readonly" if assigned else "disabled")
        if len(rows) == 1:
            self._show_audit(rows[0])
        self._show_eft(rows[0] if len(rows) == 1 else None)

    def _show_audit(self, row):
        if row.result is None:
            text = "No fitting assigned." if row.fit_uid is None else (row.note or "Not audited.")
            self.audit_tree.insert("", tk.END, text=text)
            return
        node = ship_node(row.result, {}, f" · {row.location}", fit_name=row.fit_name)
        node.open = True
        self._insert(node, "")

    def _show_eft(self, row):
        """The selected ship in EFT layout, Current or Expected, from its audit result (plan 19.2)."""
        self._eft_row = row
        expected = self.eft_switch.get()
        self.lbl_eft_legend.config(text=EFT_LEGEND[expected])
        if row is None:
            lines = [("Select one ship on the left.", eft.EXTRA)]
        elif row.result is None:
            lines = [("No fitting assigned." if row.fit_uid is None else (row.note or "Not audited."), eft.EXTRA)]
        elif expected:
            lines = eft.expected_lines(row.result, row.hull, row.fit_name)
        else:
            lines = eft.current_lines(row.result, row.hull, row.fit_name)
        self.eft_lines = lines
        p = ui_style.THEMES.get(ui_style.current_theme or ui_style.DEFAULT_THEME, ui_style.THEMES[ui_style.DEFAULT_THEME])
        text = self.eft_text
        text.tag_configure(eft.OK, foreground=p["ok"])
        text.tag_configure(eft.MOVED, foreground=p["warn"])
        text.tag_configure(eft.MISSING, foreground=p["error"])
        text.tag_configure(eft.REMOVE_TAG, foreground=p["muted"], overstrike=True)
        text.tag_configure(eft.HULL, font=(ui_style.FONT_FAMILY, 10, "bold"))
        text.configure(state=tk.NORMAL)
        text.delete("1.0", tk.END)
        for line, tag in lines:
            text.insert(tk.END, line + "\n", (tag,) if tag and tag != eft.EXTRA else ())
        text.configure(state=tk.DISABLED)

    def _insert(self, node: Node, parent: str):
        item = self.audit_tree.status_icons.insert(parent, tk.END, text=node.text, tone=node.tone, open=node.open)
        for child in node.children:
            self._insert(child, item)

    def _handle_assign(self):
        rows = self._selected_rows()
        choice = self.fit_combo.get()
        fit_uid = self._fit_choices.get(choice)
        holder = self._holders.get(self.holder_combo.get()) or {}
        when = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if rows and choice == PERSONAL:
            for row in rows:
                self.app.ship_designations.mark_personal(row.item_id, row.type_id, holder, row.custom_name, when)
            self._log(f"[INFO] Marked {len(rows)} ship(s) {PERSONAL}: the audit won't look at them.")
            self._load_ships(keep=[r.item_id for r in rows])
            return
        if not rows or fit_uid is None:
            messagebox.showinfo("Assign Fitting", "Select ships of one hull, and a fitting for them.")
            return
        for row in rows:
            self.app.ship_designations.assign(row.item_id, row.type_id, fit_uid, holder, row.custom_name, when,
                                              system_id=row.system_id)
        self._log(f"[INFO] Assigned {self.fit_combo.get()} to {len(rows)} ship(s).")
        self._load_ships(keep=[r.item_id for r in rows])

    def _handle_owner(self):
        """The Owner dropdown changed: the selected assigned ships belong to that holder now."""
        owner = self._holders.get(self.owner_combo.get())
        rows = [r for r in self._selected_rows() if r.fit_uid is not None or r.personal]
        if owner is None or not rows:
            return
        changed = self.app.ship_designations.set_owner((r.item_id for r in rows), owner)
        if changed:
            self._log(f"[INFO] {changed} ship(s) now belong to {owner['name']}.")
        self._load_ships(keep=[r.item_id for r in self._selected_rows()])

    def _load_systems(self):
        """Every solar system, for the Home box (as the Library's System box)."""
        try:
            systems = self.app.evedb_loader.get_all_solar_systems()
        except Exception as e:
            self._log(f"[WARNING] The Ships tab couldn't list the solar systems: {e}")
            return
        self._systems = {s["solarSystemName"]: s["solarSystemID"] for s in systems}
        self._system_names = {i: name for name, i in self._systems.items()}
        self._home_ahead.set_choices(sorted(self._systems))

    def _system_name(self, system_id) -> str:
        return self._system_names.get(system_id) or self.app.evedb_loader.get_system_name(system_id) or str(system_id)

    def _handle_home(self, name: str):
        """The Home box took a name: the selected ships with a fitting live there now (H1)."""
        rows = [r for r in self._selected_rows() if r.fit_uid is not None]
        if not rows:
            return
        if name == ANYWHERE_LABEL:
            home = ANYWHERE
        elif name in self._systems:
            home = {"system_id": self._systems[name]}
        else:
            return
        changed = self.app.ship_designations.set_home((r.item_id for r in rows), home)
        if changed:
            self._log(f"[INFO] {changed} ship(s) now have their Home in {name}.")
        self._load_ships(keep=[r.item_id for r in rows])

    def _handle_clear(self):
        rows = [r for r in self._selected_rows() if r.fit_uid is not None or r.personal]
        if not rows:
            return
        self.app.ship_designations.unassign(r.item_id for r in rows)
        self._log(f"[INFO] Cleared the fitting of {len(rows)} ship(s).")
        self._load_ships(keep=[r.item_id for r in rows])
