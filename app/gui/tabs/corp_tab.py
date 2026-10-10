"""
Ships tab (tracked items plan, Phase 16; renamed from Corp Ships in UI thoughts plan 18.1).

Left: every ship of one holder, grouped by where it is. A holder is a linked character,
or a linked character's corporation, which acts like a character (its hangars show
once a linked Director pulls them). Right: give the selected ships a fitting (their designation), and see how the
selected ship audits against it. Designations are kept by item ID, so they follow a ship
through contracts. The data is read again each time the tab is opened (after Pull All,
for example).
"""
import logging
import threading
import tkinter as tk
from datetime import datetime, timezone
from tkinter import ttk
from app.gui import themed_dialogs as messagebox

from app import paths
from app.asset_handling.corp_pull import load_corporations, load_memberships
from app.gui import style as ui_style
from app.gui import eft_presenter as eft
from app.gui.audit_presenter import ICONS, Node, ship_node
from app.gui.dialogs.onboard_ships_dialog import OnboardShipsDialog
from app.gui.dialogs.paste_contents_dialog import TITLE as PASTE_TITLE, PasteContentsDialog
from app.gui.spinner import Spinner
from app.gui.status_icons import SIZE, StatusIcons
from app.gui.type_ahead import TypeAhead
from app.loaders.manual_contents import ManualContents, parse_inventory_paste
from app.loaders.ship_designations import ANYWHERE, home_system, is_anywhere
from app.gui.toggle_switch import ToggleSwitch
from app.models.audit_models import RequirementStatus
from app.asset_handling.skill_pull import load_levels
from app.services import esi_features
from app.services.fleet_ships import bay_of, container_items, container_rows, hangar_of, owner_choices, ship_rows
from app.services.shopping_list_service import ShoppingListService, items_text
from app.models.audit_models import ItemShortfall
from app.services.audit.inventory import items_aboard
from app.services.onboarding import onboard_ships
from app.services.tracking import TrackingContext

logger = logging.getLogger(__name__)

NO_FITTING = "⚪"
ASSIGNED_ONLY_SETTING = "ships_assigned_only"
EXPECTED_SETTING = "ships_eft_expected"
EFT_LEGEND = {False: "Green: in place · orange: in the wrong place · struck through: take it off",
              True: "Green: aboard · orange: aboard, in the wrong place · red: missing"}
AS_FITTED_LEGEND = "No fitting assigned: the ship as it's fitted now"
AS_PACKED_LEGEND = "No fitting assigned: what's in the container now"
CONTAINER_LEGEND = {False: "Green: in the container · struck through: not in the fit",
                    True: "Green: in the container · red: missing"}
VIEWS = ("ships", "implants", "containers")     # the sub-tabs, in order
POLL_MS = 50                # how often a background load is checked on
SHIPS_SHARE = 0.75          # the ship list's share of the tab's width, at first
SHIP_COLUMNS = ("name", "fitting", "home", "status", "owner")
# Column widths the user dragged (1.7.2 plan, 32.3): one set for every holder, kept across restarts.
# The last column (Owner) isn't saved: it takes whatever room is left, down to LAST_COLUMN_MIN.
COLUMN_WIDTHS_SETTING = "ships_column_widths"
LAST_COLUMN_MIN = 80
PERSONAL = "<Personal>"     # first in the Fitting list: the audit never sees the ship (S5)
ANYWHERE_LABEL = "Anywhere"
COPY_MISSING = "Copy Missing Items"
ONBOARD_THESE = "Onboard These Ships…"
PASTE_CONTENTS = "Update Contents from Game…"
DISCARD_PASTED = "Discard Pasted Contents"
STATUS_TEXT = {RequirementStatus.PASS: "Ready", RequirementStatus.WARN: "Ready, needs attention",
               RequirementStatus.FAIL: "Not ready"}


def ship_label(row, nested: bool = False, with_name: bool = True) -> str:
    """'✅ Devoter "Old Faithful"'; a carried ship says where aboard: '⚪ Heron · ship maintenance bay'
    under its carrier (nested), or "· ship maintenance bay of 'Big Brother'" when it isn't listed.
    with_name=False leaves the custom name out: the ship list shows it in its own Name column (32.2)."""
    status = row.result.status if row.result is not None else None
    icon = ICONS[status] if status is not None else NO_FITTING
    name = f'{row.hull} "{row.custom_name}"' if row.custom_name and with_name else row.hull
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
    text = _status_text(row)
    return f"{text} · unverified" if getattr(row, "unverified", None) else text


def _status_text(row) -> str:
    if row.personal:
        return "Personal"
    if row.fit_uid is None:
        return "No fitting"
    if row.loss:
        return ("Lost " if row.loss["state"] == "lost" else "Possibly lost ") + row.loss["date"]
    if row.result is None:
        return row.note or "Not audited"
    return STATUS_TEXT[row.result.status]


class CorpTab:
    def __init__(self, app, frame):
        self.app = app
        self.frame = frame
        self._holders = {}              # label -> holder
        self._owners = {}               # label -> a possible Owner: the holders, and every character's corporation
        self._rows = {}                 # tree item -> ShipRow
        self._fit_choices = {}          # Fitting dropdown label -> fit_uid (PERSONAL -> None)
        self._systems = {}              # solar system name -> ID, for the Home box
        self._system_names = {}         # ID -> name
        self._context = None
        self.onboard_dialog = None      # Onboard These Ships (32.1), while open
        # Loading in the background (1.7.4): the tab shows at once, with a spinner over the list.
        self.loading = False
        self._load_generation = 0       # a newer load (or a direct one) makes an older one's result stale
        self._setup()

    def _log(self, message: str):
        self.app._log(message)

    # --- layout ---------------------------------------------------------------------------

    def _setup(self):
        top = ttk.Frame(self.frame)
        top.pack(fill=tk.X, padx=10, pady=10)
        ttk.Label(top, text="Assets of:").pack(side=tk.LEFT, padx=5)
        self.holder_combo = ttk.Combobox(top, state="readonly", width=40)
        self.holder_combo.pack(side=tk.LEFT, padx=5)
        self.holder_combo.bind("<<ComboboxSelected>>", lambda e: self._load_view(background=True))
        ttk.Label(top, text="Each linked character's corporation is listed; its hangars show once a linked Director pulls them.",
                  style=ui_style.HINT_LABEL).pack(side=tk.LEFT, padx=15)

        # Ships, the holder's clones and implants, or containers (1.7.4): tabs, as By Doctrine and By System are.
        # Containers share the ship list and its Fitting panel: a container is given a fitting, an owner and a Home.
        self.view_tabs = ttk.Notebook(self.frame)
        for text in ("Ships", "Implants", "Containers"):
            self.view_tabs.add(ttk.Frame(self.view_tabs, height=1), text=text)
        self.view_tabs.pack(fill=tk.X, padx=10)
        self.view_tabs.bind("<<NotebookTabChanged>>", lambda e: self._on_view_changed())
        self._setup_implants()

        paned = tk.PanedWindow(self.frame, orient=tk.HORIZONTAL)
        self._paned = paned
        paned.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        # Ships 75%, the fitting and audit 25%, the first time the tab is shown; the divider can be dragged.
        self._split_placed = False
        paned.bind("<Configure>", lambda e: self._place_split(paned, e.width))

        left = ttk.LabelFrame(paned, text="Ships", padding=(5, 5))
        self._list_frame = left
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
        self.ship_tree.heading("name", text="Name", anchor=tk.W)
        self.ship_tree.heading("fitting", text="Fitting", anchor=tk.W)
        self.ship_tree.heading("home", text="Home", anchor=tk.W)
        self.ship_tree.heading("status", text="Status", anchor=tk.W)
        self.ship_tree.heading("owner", text="Owner", anchor=tk.W)
        # Only the last column stretches: it gains the room when the window widens or another
        # column narrows, and gives it back down to LAST_COLUMN_MIN (32.3).
        self.ship_tree.column("#0", width=300, stretch=False)
        self.ship_tree.column("name", width=160, stretch=False)
        self.ship_tree.column("fitting", width=160, stretch=False)
        self.ship_tree.column("home", width=100, stretch=False)
        self.ship_tree.column("status", width=140, stretch=False)
        self.ship_tree.column("owner", width=140, stretch=True, minwidth=LAST_COLUMN_MIN)
        self._widths_at_press = None
        self.ship_tree.bind("<ButtonPress-1>", self._on_tree_press, add="+")
        self.ship_tree.bind("<ButtonRelease-1>", self._on_tree_release, add="+")
        scroll = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.ship_tree.yview)
        self.ship_tree.configure(yscrollcommand=scroll.set)
        self.ship_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.ship_tree.bind("<<TreeviewSelect>>", lambda e: self._on_selection())
        # Right-click a ship: can its pilot fly it as it's fitted? (ESI features plan 26.6), and its loss.
        # Built on each right-click with only what applies: no disabled entries (hard to read on Windows).
        self.ship_menu = tk.Menu(self.ship_tree, tearoff=0)
        self.ship_tree.bind("<Button-3>", self._on_ship_right_click)
        self.ship_tree.status_icons = StatusIcons(self.ship_tree)
        self.lbl_ships_empty = ttk.Label(self.ship_tree, text="", style=ui_style.ON_LIST_LABEL)
        self.spinner = Spinner(tree_frame)

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
        # Long lines scroll sideways rather than being cut off (1.7.4).
        audit.rowconfigure(0, weight=1)
        audit.columnconfigure(0, weight=1)
        self.audit_tree = ttk.Treeview(audit, show="tree")
        audit_yscroll = ttk.Scrollbar(audit, orient=tk.VERTICAL, command=self.audit_tree.yview)
        audit_xscroll = ttk.Scrollbar(audit, orient=tk.HORIZONTAL, command=self.audit_tree.xview)
        self.audit_tree.configure(yscrollcommand=audit_yscroll.set, xscrollcommand=audit_xscroll.set)
        self.audit_tree.grid(row=0, column=0, sticky="nsew")
        audit_yscroll.grid(row=0, column=1, sticky="ns")
        audit_xscroll.grid(row=1, column=0, sticky="ew")
        self.audit_tree.column("#0", stretch=False)
        self._audit_text_width = 0      # what the widest audit line needs, in pixels
        self.audit_tree.bind("<Configure>", lambda e: self._size_audit_column(), add="+")
        self.audit_tree.status_icons = StatusIcons(self.audit_tree)
        # Right-click the audit: copy what the ship lacks, or show a missing item in the game's market.
        self._audit_data = {}           # audit tree item -> the ItemShortfall behind a missing item line
        self.audit_menu = tk.Menu(self.audit_tree, tearoff=0)
        self.audit_tree.bind("<Button-3>", self._on_audit_right_click)
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
        eft_xscroll = ttk.Scrollbar(text_frame, orient=tk.HORIZONTAL, command=self.eft_text.xview)
        self.eft_text.configure(yscrollcommand=eft_scroll.set, xscrollcommand=eft_xscroll.set, state=tk.DISABLED)
        text_frame.rowconfigure(0, weight=1)
        text_frame.columnconfigure(0, weight=1)
        self.eft_text.grid(row=0, column=0, sticky="nsew")
        eft_scroll.grid(row=0, column=1, sticky="ns")
        eft_xscroll.grid(row=1, column=0, sticky="ew")
        self._eft_row = None
        self.eft_lines = []

        # Read the data again whenever the tab is opened (Pull All, new characters, library changes).
        self.app.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed, add="+")

    # --- Implants (the Ships tab's second view) -------------------------------------------------------

    def _setup_implants(self):
        """The chosen character's clones: the active one and each jump clone, their implants by slot,
        and which of the library's implant sets each carries."""
        self.implants_frame = ttk.Frame(self.frame)     # packed when the Implants tab is chosen
        self.lbl_clones = ttk.Label(self.implants_frame, text="", style=ui_style.HINT_LABEL)
        self.lbl_clones.pack(anchor=tk.W, pady=(0, 4))
        tree_frame = ttk.Frame(self.implants_frame)
        tree_frame.pack(fill=tk.BOTH, expand=True)
        self.implant_tree = ttk.Treeview(tree_frame, columns=("implants", "sets"), selectmode="browse")
        self.implant_tree.heading("#0", text="Clone", anchor=tk.W)
        self.implant_tree.heading("implants", text="Implants", anchor=tk.W)
        self.implant_tree.heading("sets", text="Implant sets it carries", anchor=tk.W)
        self.implant_tree.column("#0", width=380, stretch=True)
        self.implant_tree.column("implants", width=90, stretch=False)
        self.implant_tree.column("sets", width=320, stretch=True)
        scroll = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.implant_tree.yview)
        self.implant_tree.configure(yscrollcommand=scroll.set)
        self.implant_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

    def view(self) -> str:
        return VIEWS[self.view_tabs.index(self.view_tabs.select())]

    def containers(self) -> bool:
        """The Containers view: the ship list shows containers instead (1.7.4)."""
        return self.view() == "containers"

    def _designations(self):
        """Where the listed items' fittings, owners and Homes are kept: ships' or containers'."""
        return self.app.container_designations if self.containers() else self.app.ship_designations

    def _on_view_changed(self):
        if self.view() == "implants":
            self._paned.pack_forget()
            self.implants_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        else:
            self.implants_frame.pack_forget()
            self._paned.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
            noun = "Container" if self.containers() else "Ship"
            self._list_frame.config(text=noun + "s")
            self.ship_tree.heading("#0", text=noun)
            self.ship_tree.selection_set(())
        self._load_view(background=True)

    def _load_view(self, background: bool = False):
        if self.view() == "implants":
            self._load_implants()
        elif background:
            self._load_in_background()
        else:
            self._load_ships()

    def _load_implants(self):
        from app.services.implant_audit import clones_of
        from app.services.implant_rules import check_set, is_implant_set, listed_implants, set_name
        from app.models.audit_models import RequirementStatus as Status
        import json
        tree = self.implant_tree
        tree.delete(*tree.get_children())
        holder = self._holders.get(self.holder_combo.get())
        if holder is None or holder.get("kind") != "character":
            self.lbl_clones.config(text="Clones belong to characters: choose a character above.")
            return
        try:
            record = json.loads((paths.CLONES_DIR / f"{int(holder['id'])}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            record = None
        if not record:
            self.lbl_clones.config(text="No clone data yet: Pull All (with a login that includes clones).")
            return
        rules = self.app.audit_engine.rules
        name = self.app.evedb_loader.get_type_name
        sets = [f for f in self.app.fitting_manager.list_fittings() if is_implant_set(f)]
        listed = [(set_name(f), listed_implants(f, rules.implant_slot, name)) for f in sets]
        home = (record.get("home_location") or {}).get("location_id")
        jumped = (record.get("last_clone_jump_date") or "")[:10]
        self.lbl_clones.config(text=f"Home station: {self.app.evedb_loader.location_label(home) if home else '—'}"
                                    + (f" · last clone jump {jumped}" if jumped else ""))
        for clone in clones_of(record):
            carries = [n for n, slots in listed
                       if slots and check_set(slots, clone.implants, rules.implant_slot, name)[0] != Status.FAIL]
            where = "Active clone" if clone.active else \
                f"Jump clone · {self.app.evedb_loader.location_label(clone.location_id)}"
            row = tree.insert("", tk.END, text=where, open=True,
                              values=(f"{len(clone.implants)}", ", ".join(carries) if carries else "—"))
            slotted = sorted(((rules.implant_slot(t) or 99), name(t)) for t in clone.implants)
            for slot, implant in slotted:
                tree.insert(row, tk.END, text=(f"Slot {slot}: " if slot != 99 else "") + implant, values=("", ""))

    def describe_implants(self) -> dict:
        """What the Implants view shows, for the Ships harness."""
        def dump(parent=""):
            return [{"text": self.implant_tree.item(i, "text"),
                     "values": [str(v) for v in self.implant_tree.item(i, "values")],
                     "children": dump(i)} for i in self.implant_tree.get_children(parent)]
        return {"note": self.lbl_clones.cget("text"), "clones": dump()}

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
            self.refresh(background=True)

    # --- loading ----------------------------------------------------------------------------

    def refresh(self, background: bool = False, keep=()):
        """
        Holders, the context (assets and corporation pulls) and the chosen holder's ships.
        background: read the assets and audit the ships on a worker thread, with a spinner (1.7.4).
        """
        names = {str(k): v for k, v in (getattr(self.app.auth_service, "index", {}) or {}).items()}
        corporations = load_corporations(paths.CORP_DIR)
        generated = self.app.audit_collection_service.generated_dir
        uids = [f["fit_uid"] for f in self.app.fitting_manager.list_fittings()]
        self.app.ship_designations.prune(uids)
        self.app.container_designations.prune(uids)

        def load_context():
            return TrackingContext.load(generated, corporations, self.app.evedb_loader, None, names)
        if not background:
            try:
                self._context = load_context()
            except Exception as e:
                self._context = None
                self._log(f"[WARNING] The Assets tab couldn't read the asset data: {e}")
        if not self._systems:
            self._load_systems()
        # Every linked character's corporation is listed (and an Owner choice) even with no Director
        # to pull its hangars: such a corporation shows only the ships the tool already knows of.
        self._owners = {h["label"]: h for h in owner_choices(names, corporations, load_memberships(paths.CORP_DIR))}
        self._holders = dict(self._owners)
        labels = list(self._holders)
        current = self.holder_combo.get()
        self.holder_combo["values"] = labels
        self.holder_combo.set(current if current in labels else (labels[0] if labels else ""))
        if background and self.view() != "implants":
            self._load_in_background(load_context, keep)
        else:
            self._load_view()

    def _rows_for(self, context, holder, containers):
        """The holder's ships, or containers, audited. Any thread."""
        if holder is None or context is None:
            return []
        names = {(h["kind"], h["id"]): h["name"] for h in self._owners.values()}
        listing, designations = ((container_rows, self.app.container_designations) if containers
                                 else (ship_rows, self.app.ship_designations))
        return listing(context, holder, designations, self.app.fitting_manager, self.app.audit_engine, names)

    def _load_in_background(self, load_context=None, keep=()):
        """
        Reads the assets (load_context, when given) and audits the chosen holder's ships on a worker
        thread; the list is emptied and a spinner shown until they're ready (1.7.4).
        """
        self._load_generation += 1
        generation = self._load_generation
        holder_label, containers = self.holder_combo.get(), self.containers()
        holder = self._holders.get(holder_label)
        context = self._context
        self.ship_tree.delete(*self.ship_tree.get_children())
        self._rows = {}
        self.lbl_ships_empty.place_forget()
        self._on_selection()
        self.loading = True
        self.spinner.show(over=self.ship_tree, text="Loading containers…" if containers else "Loading ships…")

        done = []           # the worker's result; never touches Tk itself

        def work():
            error, rows, loaded = None, None, context
            try:
                if load_context is not None:
                    loaded = load_context()
                rows = self._rows_for(loaded, holder, containers)
            except Exception as e:
                error = e
                logger.exception("The Assets tab couldn't load")
            done.append((loaded, rows, error))

        def poll():
            if generation != self._load_generation:
                return      # stale: a newer load or a direct fill took over
            if not done:
                self.frame.after(POLL_MS, poll)
                return
            loaded, rows, error = done[0]
            self._loaded(generation, holder_label, containers, loaded, rows, error, load_context is not None, keep)

        threading.Thread(target=work, daemon=True).start()
        self.frame.after(POLL_MS, poll)

    def _loaded(self, generation, holder_label, containers, context, rows, error, new_context, keep=()):
        if generation != self._load_generation:
            return          # a newer load has started, or the list was filled directly since
        self.loading = False
        self.spinner.hide()
        if new_context:
            self._context = context if error is None else None
        if error is not None:
            self._log(f"[WARNING] The Assets tab couldn't read the asset data: {error}")
            rows = None
        if holder_label != self.holder_combo.get() or containers != self.containers():
            rows = None     # the choice changed while loading: list what's chosen now
        self._load_ships(keep=keep, rows=rows)

    def _load_ships(self, keep=(), rows=None):
        """Fills the ship tree for the chosen holder; keep: item IDs to select again. rows: already audited."""
        if rows is None and self.loading:
            self._load_generation += 1      # filled directly: a background load's result is stale now
            self.loading = False
            self.spinner.hide()
        self.ship_tree.delete(*self.ship_tree.get_children())
        self._rows = {}
        holder = self._holders.get(self.holder_combo.get())
        if rows is None:
            rows = self._rows_for(self._context, holder, self.containers())
        rows = list(rows)
        if not self.containers():
            rows += self._lost_rows(holder)
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
            item = self.ship_tree.status_icons.insert(parent, tk.END, text=ship_label(row, nested, with_name=False),
                                                      open=True, tags=("lost",) if row.loss else (),
                                                      values=(row.custom_name,
                                                              PERSONAL if row.personal else row.fit_name or "—",
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
        self._apply_column_widths()
        if rows:
            self.lbl_ships_empty.place_forget()
        else:
            if holder is None:
                text = "No characters yet: add one from Characters ▸ Add Character."
            elif total:
                noun = "containers" if self.containers() else "ships"
                text = f"None of the {total} {noun} has a fitting assigned: turn off Assigned only to see them."
            else:
                text = "No containers." if self.containers() else "No ships."
            self.lbl_ships_empty.config(text=text)
            self.lbl_ships_empty.place(relx=0.5, rely=0.5, anchor=tk.CENTER)
        reselect = [item for item, row in self._rows.items() if row.item_id in set(keep)]
        if reselect:
            self.ship_tree.selection_set(reselect)
            self.ship_tree.see(reselect[0])
        self._on_selection()

    # --- column widths (1.7.2 plan, 32.3) -----------------------------------------------------------

    def _sized_columns(self):
        """The columns whose widths are remembered: all but the last, which takes the slack."""
        return ("#0",) + SHIP_COLUMNS[:-1]

    def _apply_column_widths(self):
        """The widths the user dragged, or (until they drag one) each column sized to what it shows."""
        saved = ui_style.load_setting(paths.CONFIG_DIR, COLUMN_WIDTHS_SETTING)
        if not isinstance(saved, dict) or not saved:
            ui_style.fit_columns(self.ship_tree, SHIP_COLUMNS[:-1])
            return
        for column in self._sized_columns():
            try:
                width = int(saved.get(column, 0))
            except (TypeError, ValueError):
                continue
            if width > 0:
                self.ship_tree.column(column, width=width)

    def column_widths(self) -> dict:
        return {column: int(self.ship_tree.column(column, "width")) for column in self._sized_columns()}

    def _on_tree_press(self, event):
        self._widths_at_press = (self.column_widths() if self.ship_tree.identify_region(event.x, event.y) == "separator"
                                 else None)

    def _on_tree_release(self, event=None):
        """A column edge was dragged: keep the last column at least LAST_COLUMN_MIN wide, then remember the widths."""
        before, self._widths_at_press = self._widths_at_press, None
        if before is None:
            return
        self.settle_column_widths(before)

    def settle_column_widths(self, before: dict, available: int = None):
        """
        After a drag: if the remembered columns leave the last one less than LAST_COLUMN_MIN, the
        column that grew gives the difference back (so its edge can still be reached). Saves the widths.
        """
        widths = self.column_widths()
        available = available if available is not None else self.ship_tree.winfo_width()
        excess = sum(widths.values()) + LAST_COLUMN_MIN - available
        if excess > 0 and available > LAST_COLUMN_MIN:
            grown = max(widths, key=lambda c: widths[c] - before.get(c, widths[c]))
            widths[grown] = max(int(self.ship_tree.column(grown, "minwidth") or 20), widths[grown] - excess)
            self.ship_tree.column(grown, width=widths[grown])
        ui_style.save_setting(paths.CONFIG_DIR, COLUMN_WIDTHS_SETTING, widths)

    # --- selection, assigning ---------------------------------------------------------------------

    def _selected_rows(self):
        return [self._rows[i] for i in self.ship_tree.selection() if i in self._rows]

    def _on_selection(self):
        rows = self._selected_rows()
        hulls = {r.type_id for r in rows}
        self.audit_tree.delete(*self.audit_tree.get_children())
        self._audit_data = {}
        self._fit_choices = {}
        if self.containers():
            self._container_choices(rows)
        elif not rows:
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
        if rows and not self.containers():
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
        if rows and len(hulls) == 1 and not fittings_offered and not self.containers():
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
        self.owner_combo["values"] = list(self._owners)
        owners = {(r.owner["kind"], r.owner["id"]) for r in assigned}
        common = next((label for label, h in self._owners.items() if owners == {(h["kind"], h["id"])}), "")
        self.owner_combo.set(common)
        self.owner_combo.config(state="readonly" if assigned else "disabled")
        if len(rows) == 1:
            self._show_audit(rows[0])
        self._audit_text_width = ui_style.tree_text_width(self.audit_tree, image=SIZE + 4)
        self._size_audit_column()
        self._show_eft(rows[0] if len(rows) == 1 else None)

    def _size_audit_column(self):
        """The audit's one column: as wide as its widest line, or the view if that's wider (1.7.4)."""
        self.audit_tree.column("#0", width=max(self._audit_text_width, self.audit_tree.winfo_width()))

    def _container_choices(self, rows):
        """Containers take any fitting (1.7.4): what it lists is expected inside, as cargo."""
        if not rows:
            self.lbl_selection.config(text="Select containers on the left.")
            return
        self.lbl_selection.config(text=f"{len(rows)} containers selected" if len(rows) > 1 else ship_label(rows[0])[2:])
        fittings = sorted(self.app.fitting_manager.list_fittings(),
                          key=lambda f: ((f.get("fit_name") or "").casefold(), (f.get("hull") or "").casefold()))
        self._fit_choices = {f"{f.get('fit_name')} · {f.get('hull')} ({f['fit_uid']})": f["fit_uid"] for f in fittings}
        if not fittings:
            self.lbl_selection.config(text=self.lbl_selection["text"] + "\nNo fittings in the library yet: "
                                                                        "import one in Fittings.")

    # --- Can <pilot> Fly This? (ESI features plan 26.6) -----------------------------------------------

    def _pilot_of(self, row):
        """(character ID, name) who'd fly the ship: its owner, else the character holding it; None for a corporation."""
        owner = row.owner
        if owner is None:
            holder = self._holders.get(self.holder_combo.get())
            owner = {"kind": holder["kind"], "id": holder["id"]} if holder else None
        if not owner or owner.get("kind") != "character":
            return None
        cid = str(owner["id"])
        return cid, self.app.auth_service.index.get(cid, f"Character {cid}")

    def _lost_rows(self, holder):
        """Losses (plan 30.3): the holder's ships a killmail matched, greyed, "Lost <date>", under Lost."""
        from app.services.fleet_ships import ShipRow
        if holder is None or not esi_features.enabled("losses"):
            return []
        here = {"kind": holder["kind"], "id": int(holder["id"])}
        rows = []
        for item_id, d in self.app.ship_designations.designations.items():
            owner = d.get("owner") or d.get("holder") or {}
            if {"kind": owner.get("kind"), "id": int(owner.get("id") or 0)} != here:
                continue
            loss = self.app.loss_book.info(item_id)
            if loss is None:
                continue
            fitting = self.app.fitting_manager.get_fitting(d.get("fit_uid")) or {}
            rows.append(ShipRow(item_id=int(item_id), type_id=int(d.get("type_id") or 0),
                                hull=self.app.evedb_loader.get_type_name(int(d.get("type_id") or 0)),
                                custom_name=d.get("custom_name") or "", location_id=None, location="Lost ships",
                                aboard="lost", fit_uid=d.get("fit_uid"), fit_name=fitting.get("fit_name", ""),
                                owner=here, owner_name=holder.get("name", ""), home=d.get("home"), loss=loss,
                                system_id=(d.get("home") or {}).get("system_id"),
                                note=("Lost " if loss["state"] == "lost" else "Possibly lost ") + loss["date"]))
        self.ship_tree.tag_configure("lost", foreground=ui_style.MUTED)
        return rows

    def _handle_this_one_lost(self, row):
        self.app.loss_book.settle(row.loss["killmail_id"], row.item_id)
        self._log(f"[INFO] {row.custom_name or row.hull} marked as the ship lost on {row.loss['date']}.")
        self._load_ships()

    def _handle_not_this_one(self, row):
        self.app.loss_book.rule_out(row.loss["killmail_id"], row.item_id)
        self._log(f"[INFO] {row.custom_name or row.hull} wasn't the ship lost on {row.loss['date']}.")
        self._load_ships()

    def _on_ship_right_click(self, event):
        item = self.ship_tree.identify_row(event.y)
        if item not in self._rows:
            return
        if item not in self.ship_tree.selection():
            self.ship_tree.selection_set(item)
        self._menu_row = self._rows[item]
        if self.fill_ship_menu(self._menu_row):
            self.ship_menu.post(event.x_root, event.y_root)

    def fill_ship_menu(self, row) -> list:
        """The ship's right-click entries: Can <pilot> Fly This? (a pilot's ship, with the skill check on),
        then its loss's. Returns the labels; an empty menu isn't shown."""
        menu = self.ship_menu
        if menu.index(tk.END) is not None:
            menu.delete(0, tk.END)
        if self.containers():
            return []
        if self._onboard_rows():
            menu.add_command(label=ONBOARD_THESE, command=self._handle_onboard_these)
            menu.add_separator()
        if not row.loss and len(self._selected_rows()) <= 1:
            menu.add_command(label=PASTE_CONTENTS, command=lambda: self._handle_paste_contents(row))
            if row.unverified:
                menu.add_command(label=DISCARD_PASTED, command=lambda: self._handle_discard_pasted(row))
            menu.add_separator()
        pilot = self._pilot_of(row)
        if esi_features.enabled("skills") and pilot is not None:
            menu.add_command(label=f"Can {pilot[1]} Fly This?", command=self._handle_can_fly)
        if row.loss:
            if menu.index(tk.END) is not None:
                menu.add_separator()
            if row.loss["state"] == "possibly":
                self.ship_menu.add_command(label="This One Was Lost", command=lambda: self._handle_this_one_lost(row))
            self.ship_menu.add_command(label="It Wasn't This One", command=lambda: self._handle_not_this_one(row))
            if esi_features.enabled("losses_srp"):
                self.ship_menu.add_command(label="Copy SRP Items",
                                           command=lambda: self.app.audit_view._handle_copy_srp(row.loss["killmail_id"]))
        end = menu.index(tk.END)
        if end is not None and menu.type(end) == "separator":
            menu.delete(end)
        end = menu.index(tk.END)
        return [] if end is None else [menu.entrycget(i, "label") for i in range(end + 1)
                                       if menu.type(i) != "separator"]

    # --- Update Contents from Game (1.7.4) -----------------------------------------------------------

    def _manual_contents(self) -> ManualContents:
        """Read afresh each time: Pull All drops the pastes it replaces on its own thread."""
        return ManualContents(self.app.audit_collection_service.generated_dir / "manual_contents.json")

    def _handle_paste_contents(self, row):
        name = ship_label(row)[2:] + (f' "{row.custom_name}"' if row.custom_name else "")
        text = PasteContentsDialog(self.frame.winfo_toplevel(), name).ask()
        if text is not None:
            self.apply_pasted_contents(row, text)

    def apply_pasted_contents(self, row, text: str) -> bool:
        """The pasted contents become the ship's until the next pull (marked unverified). Returns whether kept."""
        pasted = parse_inventory_paste(text, self.app.evedb_loader)
        if not pasted.items:
            unknown = f"\n\nNot recognised: {', '.join(dict.fromkeys(pasted.unknown_names))[:400]}" \
                if pasted.unknown_names else ""
            messagebox.showwarning(PASTE_TITLE, "Nothing in the paste is an item the tool knows. Copy the ship's "
                                                "contents in the game (Name, Group, Location, Quantity)." + unknown)
            return False
        notes = []
        if pasted.unknown_names:
            notes.append("Not recognised, left out: " + ", ".join(dict.fromkeys(pasted.unknown_names)))
        if pasted.unknown_places:
            notes.append("Places the tool doesn't know, counted as cargo: " + ", ".join(dict.fromkeys(pasted.unknown_places)))
        if notes and not messagebox.askyesno(PASTE_TITLE, "\n\n".join(notes) + "\n\nUse the rest?"):
            return False
        holder = self._holders.get(self.holder_combo.get()) or {}
        when = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self._manual_contents().set(row.item_id, holder, pasted.items, when)
        skipped = f"; ships aboard left as pulled ({', '.join(pasted.ships_skipped)})" if pasted.ships_skipped else ""
        self._log(f"[INFO] {row.custom_name or row.hull}: contents pasted from the game ({len(pasted.items)} item(s)), "
                  f"unverified until the next pull{skipped}.")
        self.refresh(background=True, keep=[row.item_id])
        return True

    def _handle_discard_pasted(self, row):
        if self._manual_contents().remove([row.item_id]):
            self._log(f"[INFO] {row.custom_name or row.hull}: the pasted contents were discarded; back to the last pull.")
        self.refresh(background=True, keep=[row.item_id])

    # --- Onboard These Ships (1.7.2 plan, 32.1) ------------------------------------------------------

    def _onboard_rows(self):
        """The selected ships, when there are several and all of one hull (lost ships aside); else []."""
        rows = [r for r in self._selected_rows() if not r.loss]
        return rows if len(rows) > 1 and len({r.type_id for r in rows}) == 1 else []

    def _handle_onboard_these(self):
        rows = self._onboard_rows()
        holder_label = self.holder_combo.get()
        holder = self._holders.get(holder_label)
        if not rows or holder is None:
            return
        hull = rows[0].hull
        places = {r.location for r in rows}
        where = places.pop() if len(places) == 1 else f"{len(places)} places"
        summary = f"{len(rows)} × {hull} · {holder['name']} · {where}"
        fittings = sorted((f for f in self.app.fitting_manager.list_fittings() if f.get("hull_type_id") == rows[0].type_id),
                          key=lambda f: (f.get("fit_name") or "").casefold())
        choices = {PERSONAL: None, **{f"{f.get('fit_name')} ({f['fit_uid']})": f["fit_uid"] for f in fittings}}
        assigned = {r.fit_uid for r in rows}
        if all(r.personal for r in rows):
            fitting = PERSONAL
        else:
            fitting = next((label for label, uid in choices.items() if uid is not None and assigned == {uid}),
                           next((label for label, uid in choices.items() if uid is not None), PERSONAL))
        designated = [r for r in rows if r.fit_uid is not None or r.personal]
        owners = {(r.owner["kind"], r.owner["id"]) for r in designated if r.owner}
        owner = next((label for label, o in self._owners.items()
                      if len(designated) == len(rows) and owners == {(o["kind"], o["id"])}), holder_label)
        homes = {home_text(r, self._system_name).removeprefix("↩ ") for r in rows if r.fit_uid is not None}
        systems = {r.system_id for r in rows}
        if len(homes) == 1 and len(designated) == len(rows) and "—" not in homes:
            home = homes.pop()
        elif not designated and len(systems) == 1 and None not in systems:
            home = self._system_name(systems.pop())
        else:
            home = ""
        self.onboard_dialog = OnboardShipsDialog(
            self.app, summary, choices, fitting, self._owners, owner, sorted(self._systems), home, ANYWHERE_LABEL,
            lambda fit_uid, chosen_owner, home_name: self._apply_onboard_these(rows, holder, fit_uid, chosen_owner,
                                                                               home_name))

    def _apply_onboard_these(self, rows, holder, fit_uid, owner, home_name):
        if home_name == ANYWHERE_LABEL:
            home = ANYWHERE
        elif home_name in self._systems:
            home = {"system_id": self._systems[home_name]}
        else:
            home = None
        count = onboard_ships(self.app.ship_designations, rows, fit_uid, holder, owner, home)
        what = PERSONAL if fit_uid is None else next(
            (f.get("fit_name") for f in self.app.fitting_manager.list_fittings() if f["fit_uid"] == fit_uid), str(fit_uid))
        self._log(f"[INFO] Onboarded {count} {rows[0].hull}(s): {what}, owner {owner['name']}"
                  + (f", Home {home_name}" if home and fit_uid is not None else "") + ".")
        self._load_ships(keep=[r.item_id for r in rows])

    def _ship_asset(self, row):
        """The ship as it is (its fitted modules and bays), from its holder's assets."""
        holder = self._holders.get(self.holder_combo.get())
        if holder is None or self._context is None:
            return None
        for sighting in self._context.universe.ships_held_by({"kind": holder["kind"], "id": int(holder["id"])}):
            if sighting.item_id == row.item_id:
                return self._context.ships_of(sighting)[0]
        return None

    def can_fly_text(self, row):
        """(title, message, check) for Can <pilot> Fly This?; check is None when there's nothing to copy."""
        pilot = self._pilot_of(row)
        title = f"Can {pilot[1]} Fly This?" if pilot else "Can … Fly This?"
        if pilot is None:
            return title, "This ship belongs to a corporation: give it a character as its owner first.", None
        levels = load_levels(pilot[0])
        if levels is None:
            return title, (f"{pilot[1]}'s skills haven't been pulled yet: pull with a login that includes skills "
                           "(Characters ▸ Add Character again if it's older)."), None
        ship = self._ship_asset(row)
        if ship is None:
            return title, "This ship wasn't found in the last pull.", None
        check = self.app.audit_engine.skill_requirements().check_ship(ship, levels)
        name = ship_label(row)[2:]
        if check.ok:
            return title, f"Yes: {pilot[1]} can fly {name} as it's fitted, with everything aboard.", None
        lines = []
        if check.hard:
            lines += [f"No: {pilot[1]} can't fly {name} as it's fitted. Missing for the hull and fitted modules:"]
            lines += [f"  • {m.text}" for m in check.hard]
        else:
            lines += [f"Yes, as it's fitted: {pilot[1]} can fly {name}."]
        if check.soft:
            lines += ["", "Missing for drones, fighters, charges or cargo:"] + [f"  • {m.text}" for m in check.soft]
        return title, "\n".join(lines), check

    def _handle_can_fly(self):
        row = getattr(self, "_menu_row", None)
        if row is None:
            return
        title, message, check = self.can_fly_text(row)
        if check is None:
            messagebox.showinfo(title, message)
            return
        if messagebox.askyesno(title, message + "\n\nCopy a skill plan for the missing skills?"):
            lines = self.app.audit_engine.skill_requirements().plan(check)
            self.frame.clipboard_clear()
            self.frame.clipboard_append("\n".join(lines))
            self._log(f"[INFO] Skill plan copied: {len(lines)} level(s). Paste it into the game's skill planner.")

    # --- the selected ship's audit: Copy Missing Items, Show in Market ---------------------------------

    def _audit_row(self):
        rows = self._selected_rows()
        return rows[0] if len(rows) == 1 and rows[0].result is not None else None

    def missing_items(self, row):
        """What the ship lacks, as Multibuy lines (as Add Missing Items puts it on the shopping list)."""
        service = ShoppingListService(self.app.audit_engine.rules, self.app.evedb_loader.get_type_name)
        return service.items_for_ship(row.result)

    def _on_audit_right_click(self, event):
        row = self._audit_row()
        if row is None:
            return
        item = self.audit_tree.identify_row(event.y)
        if item:
            self.audit_tree.selection_set(item)
        if self._fill_audit_menu(row, item):
            self.audit_menu.post(event.x_root, event.y_root)

    def _fill_audit_menu(self, row, item):
        menu = self.audit_menu
        if menu.index(tk.END) is not None:
            menu.delete(0, tk.END)
        if self.missing_items(row):
            menu.add_command(label=COPY_MISSING, command=self._handle_copy_missing)
        shortfall = self._audit_data.get(item)
        if esi_features.enabled("client") and shortfall:
            menu.add_command(label=f"Show {shortfall.name} in Market…",
                             command=lambda: self._handle_show_market(row, shortfall))
        end = menu.index(tk.END)
        return [] if end is None else [menu.entrycget(i, "label") for i in range(end + 1)]

    def _handle_copy_missing(self):
        row = self._audit_row()
        if row is None:
            return
        items = self.missing_items(row)
        self.frame.clipboard_clear()
        self.frame.clipboard_append(items_text(items))
        self._log(f"[INFO] Missing items for {ship_label(row)[2:]} copied: {len(items)} item type(s), "
                  "ready for the game's Multibuy.")

    def _handle_show_market(self, row, shortfall: ItemShortfall):
        pilot = self._pilot_of(row)
        self.app.open_in_game("Show in Market", shortfall.name,
                              lambda client, char_id: client.show_market(char_id, shortfall.type_id),
                              pilot[0] if pilot else None)

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
        self.lbl_eft_legend.config(text=(CONTAINER_LEGEND if self.containers() else EFT_LEGEND)[expected])
        if row is None:
            lines = [("Select one ship on the left.", eft.EXTRA)]
        elif row.fit_uid is None and not expected and not row.loss:
            lines = self._as_fitted(row)       # 32.4: no fitting, so Current shows the ship as it stands
            self.lbl_eft_legend.config(text=AS_PACKED_LEGEND if self.containers() else AS_FITTED_LEGEND)
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

    def _as_fitted(self, row):
        """EFT lines for a ship with no fitting: what's aboard now, all green (1.7.2 plan, 32.4)."""
        if self.containers():
            sighting = self._context.universe.find(row.item_id) if self._context is not None else None
            if sighting is None:
                return [("The container isn't in the last pull.", eft.EXTRA)]
            return eft.fitted_lines(container_items(self._context, sighting, self.app.audit_engine.rules),
                                    row.hull, row.custom_name)
        ship = self._ship_asset(row)
        if ship is None:
            return [("No fitting assigned, and the ship isn't in the last pull.", eft.EXTRA)]
        return eft.fitted_lines(items_aboard(ship, self.app.audit_engine.rules), row.hull, row.custom_name)

    def _insert(self, node: Node, parent: str):
        item = self.audit_tree.status_icons.insert(parent, tk.END, text=node.text, tone=node.tone, open=node.open)
        if isinstance(node.data, ItemShortfall):
            self._audit_data[item] = node.data
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
                self._designations().mark_personal(row.item_id, row.type_id, holder, row.custom_name, when)
            self._log(f"[INFO] Marked {len(rows)} ship(s) {PERSONAL}: the audit won't look at them.")
            self._load_ships(keep=[r.item_id for r in rows])
            return
        if not rows or fit_uid is None:
            messagebox.showinfo("Assign Fitting", "Select containers, and a fitting for them." if self.containers()
                                else "Select ships of one hull, and a fitting for them.")
            return
        for row in rows:
            self._designations().assign(row.item_id, row.type_id, fit_uid, holder, row.custom_name, when,
                                              system_id=row.system_id)
        self._log(f"[INFO] Assigned {self.fit_combo.get()} to {len(rows)} {self._noun()}(s).")
        self._load_ships(keep=[r.item_id for r in rows])

    def _handle_owner(self):
        """The Owner dropdown changed: the selected assigned ships belong to that holder now."""
        owner = self._owners.get(self.owner_combo.get())
        rows = [r for r in self._selected_rows() if r.fit_uid is not None or r.personal]
        if owner is None or not rows:
            return
        changed = self._designations().set_owner((r.item_id for r in rows), owner)
        if changed:
            self._log(f"[INFO] {changed} {self._noun()}(s) now belong to {owner['name']}.")
        self._load_ships(keep=[r.item_id for r in self._selected_rows()])

    def _load_systems(self):
        """Every solar system, for the Home box (as the Library's System box)."""
        try:
            systems = self.app.evedb_loader.get_all_solar_systems()
        except Exception as e:
            self._log(f"[WARNING] The Assets tab couldn't list the solar systems: {e}")
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
        changed = self._designations().set_home((r.item_id for r in rows), home)
        if changed:
            self._log(f"[INFO] {changed} {self._noun()}(s) now have their Home in {name}.")
        self._load_ships(keep=[r.item_id for r in rows])

    def _noun(self) -> str:
        return "container" if self.containers() else "ship"

    def _handle_clear(self):
        rows = [r for r in self._selected_rows() if r.fit_uid is not None or r.personal]
        if not rows:
            return
        self._designations().unassign(r.item_id for r in rows)
        self._log(f"[INFO] Cleared the fitting of {len(rows)} {self._noun()}(s).")
        self._load_ships(keep=[r.item_id for r in rows])
