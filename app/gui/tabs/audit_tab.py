import asyncio
import logging
import threading
import tkinter as tk
from datetime import datetime, timezone
from tkinter import filedialog, ttk
from app.gui import themed_dialogs as messagebox

from app.gui.audit_presenter import (MISSING_ITEMS, Node, carried_by_item, character_icon, fuel_summary_node,
                                     packed_node, requirement_node)
from app import paths
from app.asset_handling.corp_pull import load_corporations
from app.loaders.role_manager import fitting_in_use
from app.services.implant_rules import is_implant_set, set_name
from app.services.tracking import TrackingContext

ASSIGN_LABEL = "Assign the Fitting to"
ADOPT_LABEL = "Make the System It's In Its Home"
from app.models.audit_models import AuditResult, RequirementResult, ShipRequirementResult
from app.gui.type_ahead import TypeAhead
from app.gui.dialogs.onboard_dialog import AdoptDialog, OnboardDialog
from app.services.onboarding import PERSONAL, adoption_warnings, apply_adoption, apply_onboarding, plan_system
from app.services.shopping_list_service import ShoppingListService, item_key, items_text, merge_items, without_items
from app.services.stock import StockIndex, list_text, merge_pulls
from app.gui import style as ui_style
from app.gui.status_icons import StatusIcons, mark, shown_text, unmark

logger = logging.getLogger(__name__)


class AuditTab:
    """
    Audit tab: doctrine selector, audit tree and shopping list.

    Extracted from EVEFleetGUI (step 1.8). Shared managers/services, the root
    window and the cross-tab character map are owned by the app and read
    through it. What the tree says comes from app/gui/audit_presenter.py.
    """

    def __init__(self, app, frame):
        self.app = app
        self.frame = frame

        # Audit-only state
        self.shopping_items = []        # [ShoppingListItem], built from the audit's structured shortfalls (F9)
        # The list is always the merge of these shares (step 6.1), so removing items can tell
        # which ships no longer have anything on it and may be added again.
        self._contributions = {}        # share -> [ShoppingListItem]: one ship's items, or "audit" for Add Every...
        self._added_ships = {}          # ship key on the list -> the share it's in
        # Pull from stock before buying (plan 21): whose stock and which system each share draws on,
        # in the order shares were added; the list's Pull lines, and what's left to buy.
        self._share_places = {}         # share -> (holder {kind, id} or None, system ID or None)
        self.pull_lines = []            # [PullLine]
        self.buy_items = []             # [ShoppingListItem]
        self.last_tracking = None       # the last audit's TrackingContext (its universe is the stock)
        self._shopping_rows = {}        # shopping list row -> item_key
        self._node_data = {}            # tree item -> the ShipRequirementResult behind a ship row
        self._row_priority = {}         # ship row -> its requirement's priority ("hard" or "soft")
        self._audit_running = False
        self._last_clicked_item = None  # the audit tree row last right-clicked
        self.last_audit_results = []
        self.last_packed_ships = {}     # character ID -> [PackedShipWarning]
        self.last_carried_ships = {}    # character ID -> [CarriedShip]

        self._setup_audit_tab()

    # --- Shared state owned by EVEFleetGUI ---------------------------------

    @property
    def root(self):
        return self.app.root

    @property
    def role_manager(self):
        return self.app.role_manager

    @property
    def doctrine_manager(self):
        return self.app.doctrine_manager

    @property
    def fitting_manager(self):
        return self.app.fitting_manager

    @property
    def relationship_tree_service(self):
        return self.app.relationship_tree_service

    @property
    def audit_collection_service(self):
        return self.app.audit_collection_service

    @property
    def audit_engine(self):
        return self.app.audit_engine

    @property
    def library_char_id_map(self):
        # Written by the Options tab, read here and by the Library tab.
        return self.app.library_char_id_map

    def _log(self, message: str):
        self.app._log(message)


    def _setup_audit_tab(self):
        """Sets up the Audit tab with support for empty states."""
        # --- Top: Controls ---
        top_frame = ttk.Frame(self.frame)
        top_frame.pack(fill=tk.X, padx=10, pady=10)

        # By Doctrine: a doctrine and its roles. By System (homes and priorities plan, 24.1): every
        # character's requirements in one system. The two tabs above the tree swap these controls.
        self.doctrine_controls = ttk.Frame(top_frame)
        self.doctrine_controls.pack(side=tk.LEFT)
        ttk.Label(self.doctrine_controls, text="Doctrine:").pack(side=tk.LEFT, padx=5)
        
        self.audit_doctrine_combo = ttk.Combobox(self.doctrine_controls, state="readonly")
        self.audit_doctrine_combo.pack(side=tk.LEFT, padx=5)

        self.btn_run_doctrine_audit = ttk.Button(
            self.doctrine_controls,
            text="Run Doctrine Audit", 
            command=self._handle_run_doctrine_audit,
        )
        self.btn_run_doctrine_audit.pack(side=tk.LEFT, padx=20)
        self._populate_audit_doctrine_combo()           # also enables the button when there's a doctrine

        self.system_controls = ttk.Frame(top_frame)       # packed when By System is chosen
        ttk.Label(self.system_controls, text="System:").pack(side=tk.LEFT, padx=5)
        self.audit_system_combo = ttk.Combobox(self.system_controls, width=24)
        self.audit_system_combo.pack(side=tk.LEFT, padx=5)
        self._system_ahead = TypeAhead(self.audit_system_combo, lambda name: None, noun="system")
        self.btn_run_system_audit = ttk.Button(self.system_controls, text="Audit System",
                                               command=self._handle_run_system_audit)
        self.btn_run_system_audit.pack(side=tk.LEFT, padx=20)
        # 24.2: previews the user confirms; nothing changes before Apply.
        ttk.Button(self.system_controls, text="Onboard Ships Here…", command=self._handle_onboard_system).pack(
            side=tk.LEFT, padx=5)
        ttk.Button(self.system_controls, text="Adopt Ships Here…", command=self._handle_adopt_system).pack(
            side=tk.LEFT, padx=5)
        self.onboard_dialog = self.adopt_dialog = None
        self._audit_systems = {}                          # solar system name -> ID, read when By System opens
        self.audit_mode = "doctrine"

        # --- Main Layout: Split into two panes ---
        main_paned_window = tk.PanedWindow(self.frame, orient=tk.HORIZONTAL)
        main_paned_window.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        # --- LEFT SIDE: Doctrine Audit Tree ---
        left_pane = ttk.LabelFrame(main_paned_window, text="Doctrine Audit Tree", padding=(5, 5))
        self.audit_tree_frame = left_pane
        main_paned_window.add(left_pane, width=500)

        self.audit_mode_tabs = ttk.Notebook(left_pane)
        for text in ("By Doctrine", "By System"):
            self.audit_mode_tabs.add(ttk.Frame(self.audit_mode_tabs, height=1), text=text)
        self.audit_mode_tabs.pack(fill=tk.X)
        self.audit_mode_tabs.bind("<<NotebookTabChanged>>", self._on_audit_mode_changed)

        self.audit_tree = ttk.Treeview(left_pane)
        self.audit_tree.pack(fill=tk.BOTH, expand=True)
        self.audit_tree.status_icons = StatusIcons(self.audit_tree)     # coloured status icons and tints (D6)

        # Empty State Label for Tree
        self.lbl_audit_empty = ttk.Label(left_pane, text="No audit data loaded.", font=(ui_style.FONT_FAMILY, 12, "italic"),
                                         style=ui_style.ON_LIST_LABEL)
        self.lbl_audit_empty.place(relx=0.5, rely=0.5, anchor=tk.CENTER)

        self.audit_tree.bind("<Button-3>", self._on_audit_tree_right_click)

        # Context menu for audit tree
        self.audit_tree_context_menu = tk.Menu(self.audit_tree, tearoff=0)
        self.audit_tree_context_menu.add_command(
            label="Add Missing Items to Shopping List", 
            command=self._handle_add_missing_items_to_list
        )
        self.audit_tree_context_menu.add_command(
            label="Add Every Missing Item in This Audit",
            command=self._handle_add_all_missing_items
        )
        self.audit_tree_context_menu.add_separator()
        self.audit_tree_context_menu.add_command(
            label="Clear Shopping List",
            command=self._handle_clear_shopping_list
        )
        # A NOT CHECKED requirement's hulls: give them its fitting (UI thoughts plan 18.4).
        self.audit_tree_context_menu.add_separator()
        self.assign_menu = tk.Menu(self.audit_tree_context_menu, tearoff=0)
        self.audit_tree_context_menu.add_cascade(label=ASSIGN_LABEL, menu=self.assign_menu)
        # A ship with no Home (assigned before Homes): Adopt makes the system it's in its Home (23.4).
        self.audit_tree_context_menu.add_command(label=ADOPT_LABEL, command=self._handle_adopt, state=tk.DISABLED)
        self._adopt_index = self.audit_tree_context_menu.index(tk.END)

        # --- RIGHT SIDE: Shopping List Panel ---
        right_pane = ttk.Frame(main_paned_window)
        main_paned_window.add(right_pane)

        # Shopping List: one row per item, which can be removed (step 6.1)
        ttk.Label(right_pane, text="Shopping List", font=(ui_style.FONT_FAMILY, 12, "bold")).pack(pady=(10, 5), padx=10, anchor=tk.W)

        list_frame = ttk.Frame(right_pane)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        self.shopping_tree = ttk.Treeview(list_frame, columns=("item", "qty"), show="headings", selectmode="extended")
        self.shopping_tree.heading("item", text="Item", anchor=tk.W)
        self.shopping_tree.heading("qty", text="Quantity", anchor=tk.E)
        self.shopping_tree.column("item", anchor=tk.W, stretch=True, width=320)
        self.shopping_tree.column("qty", anchor=tk.E, stretch=False, width=80)
        shopping_scroll = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.shopping_tree.yview)
        self.shopping_tree.configure(yscrollcommand=shopping_scroll.set)
        self.shopping_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        shopping_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.lbl_shopping_empty = ttk.Label(self.shopping_tree, text="No items selected.", style=ui_style.ON_LIST_LABEL)

        self.shopping_menu = tk.Menu(self.shopping_tree, tearoff=0)
        self.shopping_menu.add_command(label="Remove", command=self._handle_remove_shopping_items)
        self.shopping_tree.bind("<Button-3>", self._on_shopping_right_click)
        self.shopping_tree.bind("<Delete>", lambda e: self._handle_remove_shopping_items())
        
        # Fleet Totals Section
        self.fleet_totals_frame = ttk.LabelFrame(right_pane, text="Fleet Totals", padding=(10, 10))
        self.fleet_totals_frame.pack(fill=tk.X, pady=10, padx=10)
        
        self.lbl_fleet_totals = ttk.Label(self.fleet_totals_frame, text="", justify=tk.LEFT, font=("Consolas", 10))
        self.lbl_fleet_totals.pack(anchor=tk.W)

        # Bottom Buttons
        button_frame = ttk.Frame(right_pane)
        button_frame.pack(fill=tk.X, side=tk.BOTTOM, pady=10)

        self.btn_copy_shopping_list = ttk.Button(
            button_frame, 
            text="Copy Shopping List", 
            command=self._handle_copy_shopping_list,
            state=tk.DISABLED
        )
        self.btn_copy_shopping_list.pack(side=tk.LEFT, expand=True, padx=10)

        self.btn_export_shopping_list = ttk.Button(
            button_frame,
            text="Export Shopping List",
            command=self._handle_export_shopping_list,
            state=tk.DISABLED
        )
        self.btn_export_shopping_list.pack(side=tk.LEFT, expand=True, padx=10)

        # Plan 21: only what has to be bought, for the game's Multibuy window.
        self.btn_copy_multibuy = ttk.Button(button_frame, text="Copy Multibuy", command=self._handle_copy_multibuy,
                                            state=tk.DISABLED)
        self.btn_copy_multibuy.pack(side=tk.LEFT, expand=True, padx=10)

        self._update_shopping_list_ui()


    def _update_audit_tree_ui(self):
        """Updates the visibility of the audit empty state label based on tree content."""
        if self.audit_tree.get_children():
            self.lbl_audit_empty.place_forget()
        else:
            self.lbl_audit_empty.place(relx=0.5, rely=0.5, anchor=tk.CENTER)

    def _on_audit_tree_right_click(self, event):
        """Handles right-click on the audit tree."""
        item_id = self.audit_tree.identify_row(event.y)
        if not item_id:
            return

        # Store the clicked item for the context menu actions
        self._last_clicked_item = item_id
        target = self._assign_target(item_id)
        self._fill_assign_menu(target)
        self.audit_tree_context_menu.entryconfigure(ASSIGN_LABEL, state=tk.NORMAL if target else tk.DISABLED)
        ship = self._node_data.get(item_id)
        adopt = getattr(ship, "adopt_system_id", None) if isinstance(ship, ShipRequirementResult) else None
        self.audit_tree_context_menu.entryconfigure(
            self._adopt_index, state=tk.NORMAL if adopt else tk.DISABLED,
            label=f"Make {self.app.evedb_loader.get_system_name(adopt)} Its Home" if adopt else ADOPT_LABEL)

        # Show context menu at the cursor position
        self.audit_tree_context_menu.post(event.x_root, event.y_root)

    # --- assigning a hull from a NOT CHECKED requirement (UI thoughts plan 18.4) -----------------

    def _assign_target(self, item_id):
        """(character ID, requirement result) for a NOT CHECKED requirement at or above the row, else None."""
        while item_id:
            data = self._node_data.get(item_id)
            if isinstance(data, RequirementResult) and data.unassigned_hulls:
                character = self.audit_tree.item(self.audit_tree.parent(item_id), "values")
                return (str(character[0]), data) if character else None
            item_id = self.audit_tree.parent(item_id)
        return None

    def _fill_assign_menu(self, target):
        self.assign_menu.delete(0, tk.END)
        if target is None:
            return
        character_id, result = target
        hulls = result.unassigned_hulls
        fit_name = hulls[0]["fit_name"]
        for hull in hulls:
            name = f'"{hull["custom_name"]}"' if hull["custom_name"] else f"(item {hull['item_id']})"
            self.assign_menu.add_command(label=f"{name} as {fit_name}",
                                         command=lambda h=hull: self._assign_hulls(character_id, [h]))
        if len(hulls) > 1:
            self.assign_menu.add_separator()
            self.assign_menu.add_command(label=f"All {len(hulls)} as {fit_name}",
                                         command=lambda: self._assign_hulls(character_id, hulls))

    def _handle_adopt(self):
        """Adopt (23.4): the right-clicked ship's Home becomes the system it's in, then the audit runs again."""
        ship = self._node_data.get(self._last_clicked_item)
        if not isinstance(ship, ShipRequirementResult) or not ship.adopt_system_id or ship.ship_item_id is None:
            return
        name = self.app.evedb_loader.get_system_name(ship.adopt_system_id)
        if self.app.ship_designations.set_home([ship.ship_item_id], {"system_id": ship.adopt_system_id}):
            self._log(f"[INFO] {ship.custom_name or ship.ship_name}'s Home is {name} now; auditing again.")
            self._handle_run_doctrine_audit()

    def _assign_hulls(self, character_id, hulls):
        """Gives the hulls the requirement's fitting, owned by the character, then audits again."""
        when = datetime.now(timezone.utc).isoformat(timespec="seconds")
        owner = {"kind": "character", "id": int(character_id)}
        for hull in hulls:
            self.app.ship_designations.assign(hull["item_id"], hull["type_id"], hull["fit_uid"], owner,
                                              hull["custom_name"], when, system_id=hull.get("system_id"))
        self._log(f"[INFO] Assigned {hulls[0]['fit_name']} to {len(hulls)} ship(s); auditing again.")
        self._handle_run_doctrine_audit()

    def _shopping_service(self) -> ShoppingListService:
        return ShoppingListService(self.audit_engine.rules, self.app.evedb_loader.get_type_name)

    def _ship_behind(self, item_id):
        """
        The ship row (or the row of a requirement with no ship) at or above a
        tree item, and its result.
        """
        while item_id:
            if isinstance(self._node_data.get(item_id), (ShipRequirementResult, RequirementResult)):
                return item_id, self._node_data[item_id]
            item_id = self.audit_tree.parent(item_id)
        return None, None

    def _shopping_key(self, tree_item, result):
        """What "already on the list" is judged by: the ship, or a missing hull per character and requirement."""
        if isinstance(result, ShipRequirementResult):
            return result.ship_item_id if result.ship_item_id is not None else tree_item
        character = self.audit_tree.item(self.audit_tree.parent(tree_item), "values")
        return ("hull", str(character[0]) if character else "", result.req_uid)

    def _share_place(self, tree_item, result):
        """(the character as holder, the system) a ship or missing hull draws stock from (plan 21):
        where the ship is; for a missing ship or hull, the requirement's system."""
        item, requirement, holder = tree_item, None, None
        while item and holder is None:
            if requirement is None and isinstance(self._node_data.get(item), RequirementResult):
                requirement = self._node_data[item]
            values = self.audit_tree.item(item, "values")
            if len(values) == 2 and str(values[0]).isdigit():          # a character row: (character, role)
                holder = {"kind": "character", "id": int(values[0])}
            item = self.audit_tree.parent(item)
        system = None
        if isinstance(result, ShipRequirementResult) and result.ship_item_id is not None and self.last_tracking:
            sighting = self.last_tracking.universe.find(result.ship_item_id)
            system = sighting.system_id if sighting is not None else None
        if system is None and requirement is not None:
            details = requirement.requirement_details or {}
            system = details.get("system_id") or (self.app.evedb_loader.system_of(details["location_id"])
                                                  if details.get("location_id") else None)
        return holder, system

    def _handle_add_missing_items_to_list(self):
        """
        Adds what the right-clicked ship is missing, in the real quantities, to the
        shopping list; on a requirement with no ship at its location, a whole
        replacement ship (hull, fit and metadata).
        """
        item_id = self._last_clicked_item or next(iter(self.audit_tree.selection()), None)
        row, result = self._ship_behind(item_id)
        if result is None:
            messagebox.showinfo("Info", "Please select a ship, or a requirement with no ship, to add what it's missing.")
            return
        row_text = shown_text(self.audit_tree, row)
        key = self._shopping_key(row, result)
        if key in self._added_ships:
            self._log(f"[INFO] Already on the shopping list: {row_text}")
            return
        service = self._shopping_service()
        if isinstance(result, ShipRequirementResult):
            items = service.items_for_ship(result)
        else:
            items = service.items_for_requirement(result)
        if not items:
            messagebox.showinfo("Info", "Nothing to buy here. Refits, substitutes and implant sets kept "
                                        "in another clone need nothing bought.")
            return
        self._contributions[key] = items
        self._added_ships[key] = key
        self._share_places[key] = self._share_place(row, result)
        self._rebuild_shopping_items()
        self._mark_added(row)
        self._update_shopping_list_ui()
        self._log(f"[INFO] Added {len(items)} missing item type(s) for {row_text} to the shopping list.")

    def _handle_add_all_missing_items(self):
        """
        Adds what every ship in this audit is missing to the list (user decision,
        2026-10-04): every ship shown, not just each requirement's best one, and
        added to what's already there. Each ship counts once, as if added by hand:
        a ship already on the list is skipped, and a ship shown under several
        requirements is bought for once (the most of each item, D14). Replacements
        for missing ships are left to their own requirement rows. Only ships a hard
        requirement needs are added; a ship listed only under soft ones is added by hand (P7).
        """
        service = self._shopping_service()
        needs_by_ship, rows_by_ship, hard = {}, {}, set()
        for tree_item, row_result in self._node_data.items():
            if not isinstance(row_result, ShipRequirementResult):
                continue
            if row_result.placement in ("AWAY", "IN_SYSTEM", "DEPLOYED", "MISSING"):
                continue        # away: refitted where it is (D4); missing: a replacement only by hand (D10)
            key = self._shopping_key(tree_item, row_result)
            if key in self._added_ships:
                continue
            merged = needs_by_ship.setdefault(key, {})
            for type_id, (name, quantity) in service.ship_needs(row_result).items():
                merged[type_id] = (name, max(merged.get(type_id, (name, 0))[1], quantity))
            rows_by_ship.setdefault(key, []).append(tree_item)
            self._share_places.setdefault(key, self._share_place(tree_item, row_result))
            if self._row_priority.get(tree_item, "hard") == "hard":
                hard.add(key)
        added, soft_only = 0, 0
        for key, needs in needs_by_ship.items():
            if not needs:
                continue
            if key not in hard:
                soft_only += 1
                continue
            self._contributions[key] = service.items(needs)
            self._added_ships[key] = key
            for tree_item in rows_by_ship[key]:
                self._mark_added(tree_item)
            added += 1
        self._rebuild_shopping_items()
        self._update_shopping_list_ui()
        self._log(f"[INFO] Added what {added} ship(s) in this audit are missing to the shopping list: "
                  f"{len(self.shopping_items)} item type(s) on it now.")
        if soft_only:
            self._log(f"[INFO] Left out {soft_only} ship(s) needed only by soft requirements: right-click to add them.")

    def _handle_clear_shopping_list(self):
        for tree_item in self._rows_on_list(self._added_ships):
            self._unmark_added(tree_item)
        self.shopping_items = []
        self.pull_lines, self.buy_items = [], []
        self._contributions = {}
        self._added_ships = {}
        self._share_places = {}
        self._update_shopping_list_ui()
        self._log("[INFO] Shopping list cleared.")

    def _rebuild_shopping_items(self):
        """
        The list is the merge of every share, summed by type and sorted by name. Each share,
        in the order added, first takes what its character has in its system (plan 21);
        the rest is bought.
        """
        items = []
        for share in self._contributions.values():
            items = merge_items(items, share)
        self.shopping_items = items
        universe = getattr(self.last_tracking, "universe", None)
        if universe is None:
            self.pull_lines, self.buy_items = [], list(items)
            return
        rules = self.audit_engine.rules
        index = StockIndex(universe, self.app.evedb_loader.location_label, self.app.evedb_loader.get_type_name,
                           rules.equivalence_key if rules is not None else None)
        service = self._shopping_service()
        pulls, buy = [], []
        for key, share in self._contributions.items():
            holder, system = self._share_places.get(key, (None, None))
            needs = {item_key(i): (i.item_name, i.quantity) for i in share if i.type_id is not None}
            taken, left = index.allocate(holder, system, needs)
            pulls += taken
            buy = merge_items(buy, service.items(left) + [i for i in share if i.type_id is None])
        self.pull_lines, self.buy_items = merge_pulls(pulls), buy

    def _on_shopping_right_click(self, event):
        row = self.shopping_tree.identify_row(event.y)
        if not row:
            return
        if row not in self.shopping_tree.selection():
            self.shopping_tree.selection_set(row)
        self.shopping_menu.post(event.x_root, event.y_root)

    def _handle_remove_shopping_items(self):
        """
        Removes the selected items from the shopping list. A ship with nothing left on
        the list loses its [x] marks and can be added again.
        """
        keys = {self._shopping_rows[row] for row in self.shopping_tree.selection() if row in self._shopping_rows}
        if not keys:
            return
        names = [item.item_name for item in self.shopping_items if item_key(item) in keys]
        for share in list(self._contributions):
            remaining = without_items(self._contributions[share], keys)
            if remaining:
                self._contributions[share] = remaining
            else:
                del self._contributions[share]
                self._share_places.pop(share, None)
        freed = [key for key, share in self._added_ships.items() if share not in self._contributions]
        for key in freed:
            del self._added_ships[key]
        for tree_item in self._rows_on_list(freed):
            self._unmark_added(tree_item)
        self._rebuild_shopping_items()
        self._update_shopping_list_ui()
        self._log(f"[INFO] Removed from the shopping list: {', '.join(names)}."
                  + (f" {len(freed)} ship(s) can be added again." if freed else ""))

    def _rows_on_list(self, keys):
        """The tree's ship (and missing-hull) rows whose shopping key is in keys."""
        if not keys:
            return []
        return [tree_item for tree_item, result in self._node_data.items()
                if isinstance(result, (ShipRequirementResult, RequirementResult))
                and self._shopping_key(tree_item, result) in keys]

    def _remark_added_ships(self):
        """After the tree is redrawn, mark again the ships that are still on the shopping list."""
        for tree_item in self._rows_on_list(self._added_ships):
            self._mark_added(tree_item)

    def _unmark_added(self, ship_item):
        """Undo _mark_added: drop the "[x] " from a ship's (or missing hull's) lines."""
        if isinstance(self._node_data.get(ship_item), RequirementResult):
            lines = list(self.audit_tree.get_children(ship_item))[:1]     # the reason line, not the offers
        else:
            lines = [line for child in self.audit_tree.get_children(ship_item)
                     if tuple(self.audit_tree.item(child, "values")) == (MISSING_ITEMS,)
                     for line in self.audit_tree.get_children(child)]
        for line in lines:
            self.audit_tree.item(line, text=unmark(self.audit_tree.item(line, "text")))

    def shopping_list_text(self) -> str:
        """What Copy and Export produce: the Pull lines, then the Buy lines, each under its heading
        (just the items when there's nothing to pull)."""
        if not self.pull_lines:
            return items_text(self.buy_items)
        return list_text(self.pull_lines, [item.line() for item in self.buy_items])

    def multibuy_text(self) -> str:
        """What Copy Multibuy puts on the clipboard: only what has to be bought, ready for the game."""
        return items_text(self.buy_items)

    def _mark_added(self, ship_item):
        """Show "[x]" on the missing item lines of a ship (or the missing hull line) that's on the shopping list."""
        if isinstance(self._node_data.get(ship_item), RequirementResult):
            for line in list(self.audit_tree.get_children(ship_item))[:1]:     # the reason line, not the offers
                self.audit_tree.item(line, text=mark(self.audit_tree.item(line, "text")))
            return
        for child in self.audit_tree.get_children(ship_item):
            if tuple(self.audit_tree.item(child, "values")) == (MISSING_ITEMS,):
                for line in self.audit_tree.get_children(child):
                    self.audit_tree.item(line, text=mark(self.audit_tree.item(line, "text")))

    def _update_shopping_list_ui(self):
        """Shows the shopping list, its totals, and enables Copy and Export when there's something on it."""
        self.shopping_tree.delete(*self.shopping_tree.get_children())
        self._shopping_rows = {}
        # Plan 21: what's in stock nearby is pulled, under its own heading, before what's bought.
        if self.pull_lines:
            self.shopping_tree.insert("", tk.END, values=("Pull from stock", ""), tags=("heading",))
            for pull in self.pull_lines:
                row = self.shopping_tree.insert("", tk.END, values=(f"{pull.place} · {pull.where}: {pull.item_name}",
                                                                    f"{pull.quantity:,}"))
                self._shopping_rows[row] = pull.for_type_id
            if self.buy_items:
                self.shopping_tree.insert("", tk.END, values=("Buy", ""), tags=("heading",))
        for item in self.buy_items:
            name = item.item_name + (f" (or {', '.join(item.alternatives)})" if item.alternatives else "")
            row = self.shopping_tree.insert("", tk.END, values=(name, f"{item.quantity:,}"))
            self._shopping_rows[row] = item_key(item)
        self.shopping_tree.tag_configure("heading", font=(ui_style.FONT_FAMILY, 9, "bold"))
        if self.shopping_items:
            self.lbl_shopping_empty.place_forget()
            units = sum(item.quantity for item in self.buy_items)
            kinds = len(self.buy_items)
            text = f"{kinds} item type{'s' if kinds != 1 else ''} · {units:,} unit{'s' if units != 1 else ''} to buy"
            if self.pull_lines:
                pulled = sum(p.quantity for p in self.pull_lines)
                text = f"{pulled:,} unit{'s' if pulled != 1 else ''} to pull from stock\n" + text
            self.lbl_fleet_totals.config(text=text)
            self.fleet_totals_frame.pack(fill=tk.X, pady=10, padx=10)
            self.btn_copy_shopping_list.config(state=tk.NORMAL)
            self.btn_export_shopping_list.config(state=tk.NORMAL)
            self.btn_copy_multibuy.config(state=tk.NORMAL if self.buy_items else tk.DISABLED)
        else:
            self.lbl_shopping_empty.place(relx=0.5, rely=0.5, anchor=tk.CENTER)
            self.lbl_fleet_totals.config(text="")
            self.fleet_totals_frame.pack_forget()
            self.btn_copy_shopping_list.config(state=tk.DISABLED)
            self.btn_export_shopping_list.config(state=tk.DISABLED)
            self.btn_copy_multibuy.config(state=tk.DISABLED)

    def _handle_run_doctrine_audit(self):
        """Starts the doctrine audit on a worker thread (F8); the tree is drawn when it finishes."""
        if self._audit_running:
            return
        doctrine_name = self.audit_doctrine_combo.get()
        if not doctrine_name:
            messagebox.showwarning("Warning", "Please select a doctrine.")
            return

        self._log(f"[INFO] Doctrine selected: {doctrine_name}")

        # 1. Build hierarchy
        # Invert the char map for the service: {id: name}
        char_id_to_name = {cid: name for name, cid in self.library_char_id_map.items()}

        hierarchy = self.relationship_tree_service.build_hierarchy(
            doctrine_name,
            char_id_to_name
        )

        # 2. Clear existing tree
        self.audit_tree.delete(*self.audit_tree.get_children())

        # 3. Handle empty hierarchy
        if not hierarchy:
            self._log(f"[WARN] No hierarchy found for doctrine: {doctrine_name}")
            self._update_audit_tree_ui()
            return

        # 4. Get the doctrine data from the list
        doctrine_data = hierarchy[0]

        # 5. Check for Roles/Characters in hierarchy
        roles_data = doctrine_data.get('roles', [])
        self._log(f"[INFO] Roles found: {len(roles_data)}")

        if not roles_data:
            self._log(f"[INFO] No roles assigned to doctrine: {doctrine_name}")
            self._add_empty_state_node("No roles assigned to this doctrine.")
            self._update_audit_tree_ui()
            return

        # Each character is audited once: audit() covers all of their roles.
        characters = {}
        for role in roles_data:
            for char in role.get('characters', []):
                characters.setdefault(int(char['uid']), char['name'])
        self._log(f"[INFO] Characters found: {len(characters)}")

        # 6. Run the audit on a worker thread so the window stays responsive
        self._audit_running = True
        self.btn_run_doctrine_audit.config(state=tk.DISABLED)
        self._add_empty_state_node(f"Auditing {doctrine_name}...")
        self._update_audit_tree_ui()
        threading.Thread(target=self._audit_worker, args=(doctrine_name, doctrine_data, characters), daemon=True).start()

    def _audit_worker(self, doctrine_name: str, doctrine_data: dict, characters: dict):
        """Worker thread: collect each character's snapshot and audit it. Never touches widgets."""
        log = lambda message: self.root.after(0, self._log, message)
        results, packed, carried = [], {}, {}
        try:
            log(f"[INFO] Starting doctrine audit for: {doctrine_name}")
            tracking = self._tracking_context(log)
            self.last_tracking = tracking
            for char_id, char_name in characters.items():
                log(f"[INFO] Character ID being audited: {char_id} ({char_name})")
                try:
                    snapshot = asyncio.run(self.audit_collection_service.collect_audit_snapshot(char_id))
                    log(f"[INFO] Snapshot collected for {char_name}")
                    char_results = self.audit_engine.audit(snapshot, tracking)
                    results.extend(char_results)
                    packed[char_id] = self.audit_engine.check_carried_ships(snapshot, char_results)
                    carried[char_id] = list(snapshot.carried_ships)
                except Exception as char_err:
                    log(f"[ERROR] Failed to audit character {char_name}: {char_err}")
            self.root.after(0, self._finish_audit, doctrine_name, doctrine_data, results, packed, carried, None)
        except Exception as e:
            self.root.after(0, self._finish_audit, doctrine_name, doctrine_data, results, packed, carried, e)

    def _tracking_context(self, log):
        """Assigned ships for this audit run (UI thoughts plan 18.3); None (every hull at the place) if it can't be built."""
        try:
            names = dict(getattr(self.app.auth_service, "index", {}) or {})
            self.app.ship_designations.prune(f["fit_uid"] for f in self.fitting_manager.list_fittings())
            return TrackingContext.load(self.audit_collection_service.generated_dir, load_corporations(paths.CORP_DIR),
                                        self.app.evedb_loader, self.app.ship_designations, names)
        except Exception as e:
            log(f"[WARNING] Assigned ships couldn't be read; every ship of each hull is audited instead: {e}")
            return None

    def _finish_audit(self, doctrine_name, doctrine_data, results, packed, carried, error):
        """Main thread: store the results and draw the tree."""
        self._audit_running = False
        self.btn_run_doctrine_audit.config(state=tk.NORMAL)
        self.btn_run_system_audit.config(state=tk.NORMAL)
        self.audit_tree.delete(*self.audit_tree.get_children())
        self._node_data = {}
        self._row_priority = {}
        if error is not None:
            self._log(f"[ERROR] Doctrine audit failed: {error}")
            messagebox.showerror("Audit Error", f"An error occurred during the doctrine audit:\n{error}")
            self._update_audit_tree_ui()
            return
        self.last_audit_results = results
        self.last_packed_ships = packed
        self.last_carried_ships = carried
        if doctrine_data.get("system_id") is not None:
            self._populate_system_tree(doctrine_name, doctrine_data)
        else:
            self._populate_audit_tree(doctrine_name, doctrine_data)
        self._remark_added_ships()
        if self._contributions:             # stock may have moved since: pull and buy again (plan 21)
            self._rebuild_shopping_items()
            self._update_shopping_list_ui()
        self._update_audit_tree_ui()
        self._log(f"[SUCCESS] Audit complete for {doctrine_name}. AuditResult count: {len(results)}")

    def _add_empty_state_node(self, message: str):
        """Adds a single placeholder node to the tree to indicate an empty state."""
        self.audit_tree.insert('', 'end', text=message, values=("",))

    def _insert_node(self, parent: str, node: Node) -> str:
        item = self.audit_tree.status_icons.insert(parent, 'end', text=node.text, tone=node.tone, open=node.open,
                                                   values=node.values)
        if node.data is not None:
            self._node_data[item] = node.data
        if node.priority is not None:
            self._row_priority[item] = node.priority
        for child in node.children:
            self._insert_node(item, child)
        return item

    def _location_name(self, location_id) -> str:
        """Station, structure or system name for a location ID ("" when there's none)."""
        return self.app.evedb_loader.location_label(location_id)

    def _populate_audit_tree(self, doctrine_name: str, doctrine_data: dict):
        """Draws the audit tree: doctrine, packed ships, roles, characters, requirements, ships (design §10.1)."""
        # Root node: Doctrine
        root_id = self.audit_tree.insert('', 'end', text=doctrine_name, open=True)

        roles = doctrine_data.get('roles', [])
        if not roles:
            # This case is actually handled by the caller, but good for safety
            return

        logger.debug(f"Starting tree population. Roles: {len(roles)}")

        # Key by both character and role to support characters in multiple roles
        audit_lookup = {(result.character_id, result.role_uid): result for result in self.last_audit_results}
        packed = self.last_packed_ships
        carried = {}
        for ships in self.last_carried_ships.values():
            carried.update(carried_by_item(ships))

        # Packed ships are listed once per character, above the roles (design §9.5)
        listed = set()
        for role in roles:
            for char in role.get('characters', []):
                char_uid = int(char["uid"])
                if char_uid in listed or not packed.get(char_uid):
                    continue
                listed.add(char_uid)
                self._insert_node(root_id, packed_node(char["name"], packed[char_uid], self._location_name))

        # The doctrine's fuel summary: every listed ship of its roles with a fuel requirement (D20)
        role_uids = {int(r["uid"]) for r in roles if r.get("uid") is not None}
        fuel = fuel_summary_node(req for result in self.last_audit_results if result.role_uid in role_uids
                                 for req in result.requirement_results)
        if fuel is not None:
            self._insert_node(root_id, fuel)

        for role in roles:
            role_id = self.audit_tree.insert(root_id, 'end', text=role["name"], open=True)

            # Get requirements for this role to display under characters
            role_uid = role.get('uid')
            role_requirements = []
            if role_uid is not None:
                try:
                    # Ensure role_uid is an int as expected by RoleManager
                    r_uid_int = int(role_uid)
                    role_obj = self.role_manager.get_role(r_uid_int)
                    if role_obj:
                        role_requirements = role_obj.get('requirements', [])
                        logger.debug(f"Role {role['name']} has {len(role_requirements)} requirements.")
                except (ValueError, TypeError):
                    self._log(f"[WARN] Invalid role UID encountered: {role_uid}")

            characters = role.get('characters', [])
            if not characters:
                self.audit_tree.insert(role_id, 'end', text="[No characters assigned]", values=("",))
                continue

            logger.debug(f"Role {role['name']} has {len(characters)} characters.")

            for char in characters:
                char_name = char["name"]
                char_uid = char["uid"]
                audit_result = None
                if role_uid is not None:
                    audit_result = audit_lookup.get((int(char_uid), int(role_uid)))
                icon = character_icon(audit_result, packed.get(int(char_uid), []))
                char_id = self.audit_tree.status_icons.insert(role_id, 'end', text=f"{icon} {char_name}",
                                                              values=(char_uid, role_uid))

                # Add requirements as children of the character
                req_count = 0
                for req in role_requirements:
                    req_count += 1
                    matching_req_result = None
                    if audit_result:
                        matching_req_result = next((r for r in audit_result.requirement_results
                                                    if r.req_uid == req.get('req_uid')), None)
                    node = self._requirement_node(req, matching_req_result, carried)
                    if node is not None:
                        self._insert_node(char_id, node)

                logger.debug(f"Character {char_name} populated with {req_count} requirements.")

        self._log(f"[SUCCESS] Audit tree populated for {doctrine_name}")

    def _requirement_node(self, req: dict, result, carried) -> "Node | None":
        """One requirement's row and its ships, for either mode; None for a requirement with no fitting."""
        fit_uid = fitting_in_use(req)        # the pilot's replacement, if any (step 11.2)
        if fit_uid is None:
            return None
        fitting = self.fitting_manager.get_fitting(fit_uid)
        fit_name = fitting.get('fit_name', 'Unknown Fit') if fitting else 'Unknown Fit'
        hull = (fitting or {}).get('hull') or fit_name
        if is_implant_set(fitting):
            hull = f"Implants: {set_name(fitting)}"      # plan 15.3
        notes = ((fitting or {}).get("doctrine_metadata") or {}).get("notes", "")
        replacing = ""
        if fit_uid != req.get('fit_uid'):
            original = self.fitting_manager.get_fitting(req.get('fit_uid')) or {}
            replacing = original.get('hull') or original.get('fit_name') or f"fitting {req.get('fit_uid')}"
        return requirement_node(req, hull, result, carried,
                                lambda ship: self._location_name(ship.location_id), notes, fit_name,
                                place_label=self.app.evedb_loader.location_label, replacing=replacing)

    # --- By System (homes and priorities plan, 24.1) ----------------------------------------------

    def _on_audit_mode_changed(self, event=None):
        """The By Doctrine / By System tabs: swap the controls and start the tree afresh."""
        mode = "system" if self.audit_mode_tabs.index("current") == 1 else "doctrine"
        if mode == self.audit_mode:
            return
        if self._audit_running:                           # finish the running audit in the mode it started
            self.audit_mode_tabs.select(1 if self.audit_mode == "system" else 0)
            return
        self.audit_mode = mode
        if mode == "system":
            self.doctrine_controls.pack_forget()
            self.system_controls.pack(side=tk.LEFT)
            if not self._audit_systems:
                self._load_audit_systems()
        else:
            self.system_controls.pack_forget()
            self.doctrine_controls.pack(side=tk.LEFT)
        self.audit_tree.delete(*self.audit_tree.get_children())
        self._node_data, self._row_priority = {}, {}
        self.audit_tree_frame.config(text="System Audit Tree" if mode == "system" else "Doctrine Audit Tree")
        self.lbl_audit_empty.config(text="Pick a system and press Audit System." if mode == "system"
                                    else "No audit data loaded.")
        self._update_audit_tree_ui()

    def _load_audit_systems(self):
        try:
            systems = self.app.evedb_loader.get_all_solar_systems()
        except Exception as e:
            self._log(f"[WARNING] Couldn't list the solar systems: {e}")
            return
        self._audit_systems = {s["solarSystemName"]: s["solarSystemID"] for s in systems}
        self._system_ahead.set_choices(sorted(self._audit_systems))

    def _system_characters(self, system_id: int) -> dict:
        """
        Every linked character assigned to a role with a requirement in the system, across all
        doctrines (Q4). Any system requirements don't count (Q5). {character ID: name}.
        """
        roles = {role["role_uid"] for role in self.role_manager.list_roles()
                 if any(r.get("system_id") == system_id for r in role.get("requirements", []))}
        names = {}
        for name, cid in self.library_char_id_map.items():
            try:
                names[int(cid)] = name
            except (TypeError, ValueError):
                continue
        characters = {}
        for doctrine in self.doctrine_manager.list_doctrines():
            for role_key, assigned in (doctrine.get("character_assignments") or {}).items():
                if str(role_key).isdigit() and int(role_key) in roles:
                    for char in assigned:
                        if str(char).isdigit() and int(char) in names:
                            characters.setdefault(int(char), names[int(char)])
        return dict(sorted(characters.items(), key=lambda c: c[1].casefold()))

    def _handle_run_system_audit(self):
        """Audit System: every character with a requirement in the system, on a worker thread."""
        if self._audit_running:
            return
        chosen = self._chosen_system("Audit System")
        if chosen is None:
            return
        system_id, name = chosen
        self.audit_tree.delete(*self.audit_tree.get_children())
        self._node_data, self._row_priority = {}, {}
        characters = self._system_characters(system_id)
        self._log(f"[INFO] System selected: {name}; characters with requirements there: {len(characters)}")
        if not characters:
            self._add_empty_state_node(f"No character has a requirement in {name}.")
            self._update_audit_tree_ui()
            return
        self._audit_running = True
        self.btn_run_system_audit.config(state=tk.DISABLED)
        self.btn_run_doctrine_audit.config(state=tk.DISABLED)
        self._add_empty_state_node(f"Auditing {name}...")
        self._update_audit_tree_ui()
        threading.Thread(target=self._audit_worker, args=(name, {"system_id": system_id}, characters),
                         daemon=True).start()

    def _chosen_system(self, title: str):
        """(system ID, name) from the System box, or None after saying it isn't a system."""
        typed = self.audit_system_combo.get().strip()
        name = self._system_ahead.exact(typed) or typed
        system_id = self._audit_systems.get(name)
        if system_id is None:
            messagebox.showwarning(title, "Please pick a solar system from the list.")
            return None
        return system_id, name

    def _system_ships(self, title: str):
        """What Onboard and Adopt would offer in the chosen system: (plan, tracking), or None."""
        chosen = self._chosen_system(title)
        if chosen is None or self._audit_running:
            return None
        system_id, name = chosen
        characters = self._system_characters(system_id)
        if not characters:
            messagebox.showinfo(title, f"No character has a requirement in {name}.")
            return None
        tracking = self._tracking_context(self._log)
        if tracking is None or tracking.designations is None:
            messagebox.showerror(title, "The ships couldn't be read: run Pull All, then try again.")
            return None
        plan = plan_system(system_id, characters, tracking, self.role_manager, self.doctrine_manager,
                           self.fitting_manager, self.audit_engine)
        return plan, tracking

    def _handle_onboard_system(self):
        """Onboard Ships Here (24.2): new ships get a fitting and this Home, after the preview."""
        found = self._system_ships("Onboard Ships")
        if found is None:
            return
        plan, _ = found
        if not plan.onboard and not plan.personal:
            messagebox.showinfo("Onboard Ships", f"No new ships in {plan.system_name} to onboard.")
            return
        self.onboard_dialog = OnboardDialog(self.app, plan, lambda choices, personal:
                                            self._apply_onboarding(plan, choices, personal))

    def _apply_onboarding(self, plan, choices, personal):
        assigned, marked = apply_onboarding(plan, choices, personal, self.app.ship_designations)
        self._log(f"[INFO] Onboarded in {plan.system_name}: {assigned} ship(s) given a fitting and this Home, "
                  f"{marked} marked {PERSONAL}.")
        self._handle_run_system_audit()

    def _handle_adopt_system(self):
        """Adopt Ships Here (24.2): ships with a fitting take this system as Home, after the preview and its warning."""
        found = self._system_ships("Adopt Ships")
        if found is None:
            return
        plan, tracking = found
        if not plan.adopt:
            messagebox.showinfo("Adopt Ships", f"No ships in {plan.system_name} to adopt: every ship there with a "
                                               f"fitting already has it as its Home.")
            return
        self.adopt_dialog = AdoptDialog(
            self.app, plan, lambda chosen: adoption_warnings(plan, chosen, tracking, self.role_manager,
                                                             self.doctrine_manager),
            lambda chosen: self._apply_adoption(plan, chosen))

    def _apply_adoption(self, plan, chosen):
        changed = apply_adoption(plan, chosen, self.app.ship_designations)
        self._log(f"[INFO] Adopted {changed} ship(s): their Home is {plan.system_name} now.")
        self._handle_run_system_audit()

    def _populate_system_tree(self, system_name: str, data: dict):
        """
        The system's requirements: system ▸ station (any station first) ▸ character · role ▸
        requirement ▸ ships. The character row is each requirement's parent, as By Doctrine, so
        the right-click menus work the same.
        """
        system_id = data["system_id"]
        root_id = self.audit_tree.insert('', 'end', text=system_name, open=True)
        carried = {}
        for ships in self.last_carried_ships.values():
            carried.update(carried_by_item(ships))
        names = {}
        for name, cid in self.library_char_id_map.items():
            if str(cid).isdigit():
                names[int(cid)] = name
        stations = {}
        for result in self.last_audit_results:
            role = self.role_manager.get_role(result.role_uid) or {}
            by_uid = {r.req_uid: r for r in result.requirement_results}
            for req in role.get('requirements', []):
                if req.get('system_id') != system_id:
                    continue
                station = (self.app.evedb_loader.location_label(req['location_id'], req.get('location_name'))
                           if req.get('location_id') else "Any station")
                stations.setdefault(station, {}).setdefault((result.character_id, result.role_uid, role.get('role_name', '')),
                                                            []).append((req, by_uid.get(req['req_uid'])))
        if not stations:
            self.audit_tree.insert(root_id, 'end', text=f"No requirements in {system_name}.", values=("",))
        for station in sorted(stations, key=lambda s: (s != "Any station", s.casefold())):
            station_id = self.audit_tree.insert(root_id, 'end', text=station, open=True)
            entries = stations[station]
            for (char_id, role_uid, role_name) in sorted(
                    entries, key=lambda k: (names.get(k[0], str(k[0])).casefold(), k[2].casefold())):
                rows = entries[(char_id, role_uid, role_name)]
                shown = AuditResult(character_id=char_id, role_uid=role_uid, role_name=role_name,
                                    requirement_results=[r for _, r in rows if r is not None])
                icon = character_icon(shown, [])
                char_row = self.audit_tree.status_icons.insert(
                    station_id, 'end', text=f"{icon} {names.get(char_id, char_id)} · {role_name}",
                    values=(char_id, role_uid), open=True)
                for req, result in rows:
                    node = self._requirement_node(req, result, carried)
                    if node is not None:
                        self._insert_node(char_row, node)
        self._log(f"[SUCCESS] Audit tree populated for {system_name}")

    def _handle_copy_shopping_list(self):
        """Copies the shopping list to the clipboard."""
        content = self.shopping_list_text()
        if content:
            self.root.clipboard_clear()
            self.root.clipboard_append(content)
            self._log("[INFO] Shopping list copied to clipboard.")
            messagebox.showinfo("Success", "Shopping list copied to clipboard.")
        else:
            self._log("[WARN] Shopping list is empty.")

    def _handle_copy_multibuy(self):
        """Copies only the Buy lines, ready to paste into the game's Multibuy."""
        content = self.multibuy_text()
        if not content:
            self._log("[WARN] Nothing to buy: everything on the list can be pulled from stock.")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(content)
        self._log("[INFO] Buy lines copied to clipboard for Multibuy.")
        messagebox.showinfo("Success", "The Buy lines were copied to the clipboard, ready for Multibuy.")

    def _handle_export_shopping_list(self):
        """Saves the shopping list to a .txt file: the same text Copy puts on the clipboard."""
        content = self.shopping_list_text()
        if not content:
            self._log("[WARN] Shopping list is empty.")
            return
        path = filedialog.asksaveasfilename(
            title="Export Shopping List", defaultextension=".txt",
            initialfile=f"shopping-list-{datetime.now().strftime('%Y-%m-%d')}.txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")])
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content + "\n")
        except OSError as e:
            self._log(f"[ERROR] Failed to export the shopping list: {e}")
            messagebox.showerror("Export Shopping List", f"Failed to save the shopping list:\n{e}")
            return
        self._log(f"[INFO] Shopping list exported to {path}")
        messagebox.showinfo("Export Shopping List", f"Shopping list saved to:\n{path}")

    def _populate_audit_doctrine_combo(self):
        """Populates the Audit tab Doctrine Combobox."""
        try:
            doctrines = self.doctrine_manager.list_doctrines()
            doctrine_names = sorted([d['doctrine_name'] for d in doctrines])
            self.audit_doctrine_combo['values'] = doctrine_names
            if doctrine_names:
                self.audit_doctrine_combo.current(0)
            else:
                self.audit_doctrine_combo.set("")
            if not self._audit_running:     # a running audit re-enables it when it finishes
                self.btn_run_doctrine_audit.config(state=tk.NORMAL if doctrine_names else tk.DISABLED)
        except Exception as e:
            self._log(f"[ERROR] Failed to populate audit doctrines: {e}")
