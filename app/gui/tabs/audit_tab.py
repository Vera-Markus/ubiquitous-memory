import asyncio
import logging
from dataclasses import dataclass
import threading
import time
import tkinter as tk
from datetime import datetime, timezone
from tkinter import filedialog, ttk
from app.gui import themed_dialogs as messagebox

from app.gui.audit_presenter import (MISSING_ITEMS, Node, carried_by_item, character_icon, fuel_summary_node,
                                     packed_node, requirement_node, skills_note)
from app import paths
from app.asset_handling.corp_pull import load_corporations
from app.loaders.role_manager import fitting_in_use
from app.services.implant_rules import is_implant_set, set_name
from app.services.skill_requirements import SkillCheck
from app.services.tracking import TrackingContext

USE_CONTRACT, OPEN_CONTRACT, CANCEL_CONTRACT = "Use This Contract…", "Open in Game…", "Cancel Contract Choice"
FIND_A_HULL = "Find a Hull…"
SAVE_TO_GAME = "Save Fits to Game…"
THIS_ONE_LOST, NOT_THIS_ONE, COPY_SRP = "This One Was Lost", "It Wasn't This One", "Copy SRP Items"
CONTRACT_NOTE = "contract note"     # the data of "The chosen contract is gone" (not the requirement's reason line)
ASSIGN_LABEL = "Assign the Fitting to"
COPY_SKILL_PLAN = "Copy Skill Plan"
SET_DESTINATION, ADD_WAYPOINT, SHOW_IN_MARKET = "Set Destination…", "Add Waypoint…", "Show in Market…"
MAIL_LIST = "Mail Shopping List…"
from app.models.audit_models import (AuditResult, ItemShortfall, RequirementResult, RequirementStatus,
                                     ShipRequirementResult)
from app.gui.type_ahead import TypeAhead
from app.gui.dialogs.onboard_dialog import AdoptDialog, OnboardDialog
from app.services.onboarding import PERSONAL, adoption_warnings, apply_adoption, apply_onboarding, plan_system
from app.services import esi_features
from app.services import prices
from app.services.contracts import EXACT, ContractBook, ContractChoices, Offer, left_text
from app.services.shopping_list_service import ShoppingListService, item_key, items_text, merge_items, without_items
from app.services.stock import PullLine, StockIndex, list_text, merge_pulls
from app.gui import style as ui_style
from app.gui.status_icons import StatusIcons, mark, shown_text, unmark

logger = logging.getLogger(__name__)



@dataclass
class OfferRef:
    """An alliance contract line in the tree: the offer, whose requirement, and the list it's in (Open Next)."""
    offer: Offer
    character_id: int
    req_uid: int
    label: str
    offers: list
    index: int


@dataclass
class ChoiceRef:
    """A chosen contract's line: whose requirement, and the choice."""
    character_id: int
    req_uid: int
    choice: dict



@dataclass
class HullSearchRef:
    """A capital requirement's contracts line: Find a Hull… searches from its system, for its pilot."""
    hull_type_id: int
    fitting: dict
    character_id: int
    system_id: int


def separate(menu):
    """A separator, unless the menu is empty or already ends with one."""
    end = menu.index(tk.END)
    if end is not None and menu.type(end) != "separator":
        menu.add_separator()


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
        self._row_lines = {}            # shopping list row -> its PullLine or ShoppingListItem (plan 27.2)
        self._buy_pilots = {}           # item_key -> the character whose ship it's bought for (the first)
        self._hub_orders = {}           # (hub, type ID) -> its sell orders there, or None if unread (plan 28.1)
        self._hub_read = {}             # (hub, type ID) -> when they were read: older than the TTL, read again
        self._price_job = 0             # the latest pricing run; older ones' results are dropped
        self.price_status = ""          # "Pricing at Jita 4-4: 4 of 12…", or why there are no prices
        # Contracts (plan 28.3-28.5): the saved contracts, read once per tree drawn; the 2-hour choices.
        self._contract_book = None
        self.contract_choices = ContractChoices()
        self._choice_rows = {}          # tree item -> (character ID, req_uid): its countdown is redrawn
        self._node_data = {}            # tree item -> the ShipRequirementResult behind a ship row
        self._row_priority = {}         # ship row -> its requirement's priority ("hard" or "soft")
        self._audit_running = False
        self._last_clicked_item = None  # the audit tree row last right-clicked
        self.last_audit_results = []
        self.last_packed_ships = {}     # character ID -> [PackedShipWarning]
        self.last_carried_ships = {}    # character ID -> [CarriedShip]

        self._setup_audit_tab()
        self.root.after(60_000, self._tick_choices)     # the chosen contracts' countdowns (28.4)

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

        # Context menu for audit tree: built on each right-click from what applies to the row
        # (_build_audit_menu). No disabled entries: Windows draws them etched, hard to read on dark menus.
        self.audit_tree_context_menu = tk.Menu(self.audit_tree, tearoff=0)
        self.assign_menu = tk.Menu(self.audit_tree_context_menu, tearoff=0)

        # --- RIGHT SIDE: Shopping List Panel ---
        right_pane = ttk.Frame(main_paned_window)
        main_paned_window.add(right_pane)

        # Shopping List: one row per item, which can be removed (step 6.1)
        ttk.Label(right_pane, text="Shopping List", font=(ui_style.FONT_FAMILY, 12, "bold")).pack(pady=(10, 5), padx=10, anchor=tk.W)

        list_frame = ttk.Frame(right_pane)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        self.shopping_tree = ttk.Treeview(list_frame, columns=("item", "qty", "price"), show="headings",
                                          selectmode="extended")
        self.shopping_tree.heading("item", text="Item", anchor=tk.W)
        self.shopping_tree.heading("qty", text="Quantity", anchor=tk.E)
        self.shopping_tree.heading("price", text="Price", anchor=tk.E)
        self.shopping_tree.column("item", anchor=tk.W, stretch=True, width=320)
        self.shopping_tree.column("qty", anchor=tk.E, stretch=False, width=80)
        self.shopping_tree.column("price", anchor=tk.E, stretch=False, width=110)
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
        self._build_audit_menu(item_id)
        self.audit_tree_context_menu.post(event.x_root, event.y_root)

    def _build_audit_menu(self, item_id):
        """The right-click menu for this row: the shopping list's entries, then only what applies here."""
        menu = self.audit_tree_context_menu
        if menu.index(tk.END) is not None:
            menu.delete(0, tk.END)
        menu.add_command(label="Add Missing Items to Shopping List", command=self._handle_add_missing_items_to_list)
        menu.add_command(label="Add Every Missing Item in This Audit", command=self._handle_add_all_missing_items)
        menu.add_separator()
        menu.add_command(label="Clear Shopping List", command=self._handle_clear_shopping_list)
        menu.add_separator()
        # A NOT CHECKED requirement's hulls: give them its fitting (UI thoughts plan 18.4).
        target = self._assign_target(item_id)
        self._fill_assign_menu(target)
        if target:
            menu.add_cascade(label=ASSIGN_LABEL, menu=self.assign_menu)
        # A ship with no Home (assigned before Homes): Adopt makes the system it's in its Home (23.4).
        ship = self._node_data.get(item_id)
        adopt = getattr(ship, "adopt_system_id", None) if isinstance(ship, ShipRequirementResult) else None
        if adopt:
            menu.add_command(label=f"Make {self.app.evedb_loader.get_system_name(adopt)} Its Home",
                             command=self._handle_adopt)
        # A pilot missing skills: the missing levels for the game's skill planner (ESI features plan 26.7).
        if self._skill_check_at(item_id):
            menu.add_command(label=COPY_SKILL_PLAN, command=self._handle_copy_skill_plan)
        self._fill_market_entry(item_id)
        if menu.type(tk.END) == "separator":
            menu.delete(tk.END)

    def menu_entries(self, item_id) -> list:
        """The labels the row's right-click menu offers, for the harness."""
        self._build_audit_menu(item_id)
        menu = self.audit_tree_context_menu
        return [menu.entrycget(i, "label") for i in range(menu.index(tk.END) + 1) if menu.type(i) != "separator"]

    def _fill_market_entry(self, item_id):
        """
        After the fixed entries: on a lost ship, a pilot, a contract offer or a chosen contract, its
        entries (plans 28.4, 29.3, 30.3); then Show <item> in Market… on a missing item line. None of
        it while its feature's off.
        """
        menu = self.audit_tree_context_menu
        data = self._node_data.get(item_id)
        values = self.audit_tree.item(item_id, "values") if item_id else ()
        if isinstance(data, ShipRequirementResult) and data.loss and esi_features.enabled("losses"):
            separate(menu)
            if data.loss["state"] == "possibly":
                menu.add_command(label=THIS_ONE_LOST, command=lambda: self._handle_this_one_lost(data))
            menu.add_command(label=NOT_THIS_ONE, command=lambda: self._handle_not_this_one(data))
            if esi_features.enabled("losses_srp"):
                menu.add_command(label=COPY_SRP, command=lambda: self._handle_copy_srp(data.loss["killmail_id"]))
        if len(values) == 2 and str(values[0]).isdigit() and esi_features.enabled("fittings"):
            # A pilot's row: their doctrine fittings, saved to their in-game fittings (plan 29.3).
            separate(menu)
            menu.add_command(label=SAVE_TO_GAME, command=lambda: self._handle_save_to_game(str(values[0])))
            return
        if isinstance(data, HullSearchRef):
            separate(menu)
            menu.add_command(label=FIND_A_HULL, command=lambda: self.app.find_a_hull(
                data.hull_type_id, data.fitting, data.character_id, data.system_id))
            return
        if isinstance(data, OfferRef):
            separate(menu)
            menu.add_command(label=USE_CONTRACT, command=lambda: self._handle_use_contract(data))
            if esi_features.enabled("client"):
                menu.add_command(label=OPEN_CONTRACT, command=lambda: self._handle_open_contract(data))
        elif isinstance(data, ChoiceRef):
            separate(menu)
            menu.add_command(label=CANCEL_CONTRACT, command=lambda: self._handle_cancel_contract(data))
            if esi_features.enabled("client"):
                menu.add_command(label=OPEN_CONTRACT, command=lambda: self._handle_open_choice(data))
        if esi_features.enabled("client") and isinstance(data, ItemShortfall):
            separate(menu)
            menu.add_command(label=f"Show {data.name} in Market…",
                             command=lambda: self._handle_show_market_line(item_id, data))

    def pilot_fittings(self, character_id: str):
        """The fittings the pilot flies in this audit (each once, implant sets left out), by name."""
        seen, out = set(), []
        for result in self.last_audit_results:
            if str(result.character_id) != str(character_id):
                continue
            for req in result.requirement_results:
                fit_uid = fitting_in_use(req.requirement_details or {})
                fitting = self.fitting_manager.get_fitting(fit_uid) if fit_uid is not None else None
                if fitting and fit_uid not in seen and not is_implant_set(fitting):
                    seen.add(fit_uid)
                    out.append(fitting)
        return sorted(out, key=lambda f: (f.get("hull", ""), f["fit_name"]))

    def _handle_save_to_game(self, character_id: str):
        from app.gui.dialogs.game_fittings_dialogs import SaveToGameDialog
        fittings = self.pilot_fittings(character_id)
        if not fittings:
            messagebox.showinfo(SAVE_TO_GAME.rstrip("…"), "This pilot has no fittings in this audit.")
            return
        if self.app._guard(SAVE_TO_GAME.rstrip("…")):
            doctrine = self.audit_doctrine_combo.get() if self.audit_mode == "doctrine" else ""
            self.app.game_dialog = SaveToGameDialog(self.app, character_id, fittings, doctrine)

    def _pilot_at(self, item_id):
        """The character whose row is above the tree item (its pilot), or None."""
        while item_id:
            values = self.audit_tree.item(item_id, "values")
            if len(values) == 2 and str(values[0]).isdigit():
                return int(values[0])
            item_id = self.audit_tree.parent(item_id)
        return None

    def _handle_show_market_line(self, item_id, shortfall):
        self.app.open_in_game(SHOW_IN_MARKET.rstrip("…"), shortfall.name,
                              lambda client, char_id: client.show_market(char_id, shortfall.type_id),
                              self._pilot_at(item_id))

    # --- Copy Skill Plan (ESI features plan 26.7) -----------------------------------------------

    def _skill_check_at(self, item_id):
        """The skill check on the row, or on its requirement's skill line."""
        data = self._node_data.get(item_id)
        if isinstance(data, SkillCheck):
            return data
        for child in self.audit_tree.get_children(item_id):
            if isinstance(self._node_data.get(child), SkillCheck):
                return self._node_data[child]
        return None

    def _handle_copy_skill_plan(self):
        check = self._skill_check_at(getattr(self, "_last_clicked_item", None))
        if check is None:
            return
        lines = self.app.audit_engine.skill_requirements().plan(check)
        self.root.clipboard_clear()
        self.root.clipboard_append("\n".join(lines))
        self._log(f"[INFO] Skill plan copied: {len(lines)} level(s). Paste it into the game's skill planner.")

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
        character = key[1] if isinstance(key, tuple) and key[0] == "hull" else None
        choice = self.contract_choices.get(character, key[2]) if character and str(character).isdigit() else None
        if choice is not None and not messagebox.askyesno(
                "Add Missing Items", f"A contract is chosen for this ship ({left_text(self.contract_choices.left(choice))} "
                                     "left). Add a whole replacement to the shopping list anyway?"):
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
        self._buy_pilots = {}
        universe = getattr(self.last_tracking, "universe", None)
        if universe is None:
            self.pull_lines, self.buy_items = [], list(items)
            return
        rules = self.audit_engine.rules
        index = StockIndex(universe, self.app.evedb_loader.location_label, self.app.evedb_loader.get_type_name,
                           rules.equivalence_key if rules is not None else None)
        book = self._book()
        for character_id, contract in (book.own if book is not None else []):
            index.add_contract(character_id, contract.system_id, contract.location_id,
                               self.app.evedb_loader.location_label(contract.location_id), contract.contract_id,
                               contract.title, contract.items)
        service = self._shopping_service()
        pulls, buy = [], []
        for key, share in self._contributions.items():
            holder, system = self._share_places.get(key, (None, None))
            needs = {item_key(i): (i.item_name, i.quantity) for i in share if i.type_id is not None}
            taken, left = index.allocate(holder, system, needs)
            pulls += taken
            bought = service.items(left) + [i for i in share if i.type_id is None]
            if holder is not None and holder.get("kind") == "character":
                for item in bought:
                    self._buy_pilots.setdefault(item_key(item), holder["id"])
            buy = merge_items(buy, bought)
        self.pull_lines, self.buy_items = merge_pulls(pulls), buy

    def _on_shopping_right_click(self, event):
        row = self.shopping_tree.identify_row(event.y)
        if not row:
            return
        if row not in self.shopping_tree.selection():
            self.shopping_tree.selection_set(row)
        self._fill_shopping_menu(row)
        self.shopping_menu.post(event.x_root, event.y_root)

    def _fill_shopping_menu(self, row):
        """Remove, then (plan 27.2) what the line can open in the game client: a Pull line's
        place as destination or waypoint, a Buy line's market; then the whole list as a mail (27.3).
        Not shown while the feature's off."""
        if self.shopping_menu.index(tk.END) >= 1:      # an empty range would delete Remove's command too
            self.shopping_menu.delete(1, tk.END)
        if not esi_features.enabled("client"):
            return
        line = self._row_lines.get(row) if len(self.shopping_tree.selection()) == 1 else None
        self.shopping_menu.add_separator()
        if isinstance(line, PullLine) and line.location_id:
            self.shopping_menu.add_command(label=SET_DESTINATION,
                                           command=lambda: self._handle_route(line, add=False))
            self.shopping_menu.add_command(label=ADD_WAYPOINT, command=lambda: self._handle_route(line, add=True))
        elif line is not None and not isinstance(line, PullLine) and line.type_id is not None:
            if self.app.galaxy.is_capital(line.type_id):
                self.shopping_menu.add_command(label=FIND_A_HULL, command=lambda: self.app.find_a_hull(
                    line.type_id, None, self._buy_pilots.get(item_key(line))))
            self.shopping_menu.add_command(label=SHOW_IN_MARKET, command=lambda: self._handle_show_market(line))
        self.shopping_menu.add_command(label=MAIL_LIST, command=self._handle_mail_list)

    def _handle_route(self, line, add: bool):
        """Set Destination or Add Waypoint: the station or structure a Pull line's stock is in."""
        def send(client, char_id):
            if add:
                return client.add_waypoint(char_id, line.location_id)
            return client.set_destination(char_id, line.location_id)
        self.app.open_in_game(ADD_WAYPOINT.rstrip("…") if add else SET_DESTINATION.rstrip("…"),
                              line.place, send, line.character_id)

    def list_pilots(self):
        """The characters the list is for, in the order their lines come: the mail's recipients."""
        pilots = [p.character_id for p in self.pull_lines] + [self._buy_pilots.get(item_key(i))
                                                               for i in self.buy_items]
        return list(dict.fromkeys(int(p) for p in pilots if p is not None))

    def _handle_mail_list(self):
        """27.3: a new in-game mail holding the list, to the pilots it's for; sent (or not) in game."""
        pilots = self.list_pilots()
        names = self.app.auth_service.index
        doctrine = self.audit_doctrine_combo.get() if self.audit_mode == "doctrine" else ""
        subject = "Shopping list" + (f": {doctrine}" if doctrine else "")
        body = self.shopping_list_text()
        to = ", ".join(names.get(str(p), f"Character {p}") for p in pilots) or "yourself"

        def send(client, char_id):
            return client.new_mail(char_id, pilots or [int(char_id)], subject, body)
        self.app.open_in_game("Mail Shopping List", f"{subject}, to {to}. It opens as a new mail to send in game.",
                              send, pilots[0] if pilots else None)

    def _handle_show_market(self, item):
        """Show in Market: the Buy line's item in the market window."""
        self.app.open_in_game(SHOW_IN_MARKET.rstrip("…"), item.item_name,
                              lambda client, char_id: client.show_market(char_id, item.type_id),
                              self._buy_pilots.get(item_key(item)))

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
            lines = self._reason_line(ship_item)
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

    def _reason_line(self, requirement_item):
        """[the "❌ No Archon in Jita" line] under a requirement with no ship: not its skill line before it,
        nor its contract lines after it (plan 28.3)."""
        for child in self.audit_tree.get_children(requirement_item):
            data = self._node_data.get(child)
            if isinstance(data, (SkillCheck, OfferRef, ChoiceRef, HullSearchRef)) or data == CONTRACT_NOTE:
                continue
            if self.audit_tree.item(child, "text").startswith("📜"):
                continue
            return [child]
        return []

    def _mark_added(self, ship_item):
        """Show "[x]" on the missing item lines of a ship (or the missing hull line) that's on the shopping list."""
        if isinstance(self._node_data.get(ship_item), RequirementResult):
            for line in self._reason_line(ship_item):
                self.audit_tree.item(line, text=mark(self.audit_tree.item(line, "text")))
            return
        for child in self.audit_tree.get_children(ship_item):
            if tuple(self.audit_tree.item(child, "values")) == (MISSING_ITEMS,):
                for line in self.audit_tree.get_children(child):
                    self.audit_tree.item(line, text=mark(self.audit_tree.item(line, "text")))

    # --- hub prices (ESI features plan 28.1) -------------------------------------------------------

    def _priced(self):
        """(hub name, its station and region) when prices are on, else None."""
        if not esi_features.enabled("prices"):
            return None
        hub = esi_features.hub()
        return hub, esi_features.HUBS[hub]

    def _price_types(self):
        """Every type on a Buy or Pull line."""
        return ({i.type_id for i in self.buy_items if i.type_id is not None and not self.app.galaxy.is_capital(i.type_id)}
                | {p.type_id for p in self.pull_lines})

    def _quote(self, hub, type_id, quantity):
        """Each line is priced on its own quantity against the hub's sell orders (D3.2): a Buy line
        for what it buys, a Pull line for what its stock would cost. "pending" until the orders are in."""
        if (hub, type_id) not in self._hub_orders:
            return "pending"
        orders = self._hub_orders[(hub, type_id)]
        return None if orders is None else prices.walk(orders, type_id, quantity)

    def _price_cell(self, priced, type_id, quantity, stock: bool = False):
        """A Buy line's price; a Pull line's (stock=True) is what its units would cost, never "only N"."""
        quote = self._quote(priced[0], type_id, quantity)
        if quote == "pending":
            return "…"
        if stock and quote is not None and quote.listed:
            return f"≈ {prices.isk(quote.cost)}"
        return prices.line_text(quote)

    def reprice(self):
        """The hub changed: price the list again."""
        self._update_shopping_list_ui()

    def _start_pricing(self):
        """Reads the hub's orders for types not read yet, on a worker thread; then the list is redrawn."""
        priced = self._priced()
        if priced is None:
            return
        hub, place = priced
        stale = time.time() - prices.DEFAULT_TTL
        missing = sorted(t for t in self._price_types() if self._hub_read.get((hub, t), 0) < stale)
        if not missing:
            return
        self._price_job += 1
        job = self._price_job
        self.price_status = f"Pricing at {hub}…"

        def work():
            allowed, why = self.app._may_call_ccp()
            if not allowed:
                self.root.after(0, lambda: self._prices_done(job, hub, {}, f"No prices now: {why}"))
                return
            found = {}
            try:
                for n, type_id in enumerate(missing, 1):
                    found[type_id] = self.app.price_book.orders(place["region_id"], place["station_id"], type_id)
                    self.root.after(0, lambda n=n: self._price_progress(job, f"Pricing at {hub}: {n} of {len(missing)}…"))
            except Exception as e:
                self.root.after(0, lambda e=e: self._prices_done(job, hub, found, f"No prices: {e}"))
                return
            self.root.after(0, lambda: self._prices_done(job, hub, found, ""))
        threading.Thread(target=work, daemon=True).start()

    def _price_progress(self, job, text):
        if job == self._price_job:
            self.price_status = text
            self._show_totals()

    def _prices_done(self, job, hub, found, status):
        if job != self._price_job:
            return
        for type_id, orders in found.items():
            self._hub_orders[(hub, type_id)] = orders
            self._hub_read[(hub, type_id)] = time.time()
        self.price_status = status
        self._update_shopping_list_ui(price=False)

    def _show_totals(self):
        """The Fleet Totals text: units, then (prices on) what buying costs and what stock covers."""
        units = sum(item.quantity for item in self.buy_items)
        kinds = len(self.buy_items)
        text = f"{kinds} item type{'s' if kinds != 1 else ''} · {units:,} unit{'s' if units != 1 else ''} to buy"
        if self.pull_lines:
            pulled = sum(p.quantity for p in self.pull_lines)
            text = f"{pulled:,} unit{'s' if pulled != 1 else ''} to pull from stock\n" + text
        priced = self._priced()
        if priced is not None and self.shopping_items:
            hub = priced[0]
            buy = {i.type_id: i.quantity for i in self.buy_items
                   if i.type_id is not None and not self.app.galaxy.is_capital(i.type_id)}
            hulls = sum(1 for i in self.buy_items if i.type_id is not None and self.app.galaxy.is_capital(i.type_id))
            stock = {}
            for p in self.pull_lines:
                stock[p.type_id] = stock.get(p.type_id, 0) + p.quantity
            if self.price_status or any((hub, t) not in self._hub_orders for t in self._price_types()):
                text += "\n" + (self.price_status or f"Pricing at {hub}…")
            else:
                if buy:
                    text += "\n" + prices.totals({t: self._quote(hub, t, q) for t, q in buy.items()}, buy).text(hub)
                if hulls:
                    text += (f"\n{hulls} capital hull{'s' if hulls != 1 else ''} left out: from contracts "
                             f"(right-click ▸ {FIND_A_HULL})")
                if stock:
                    covered = prices.totals({t: self._quote(hub, t, q) for t, q in stock.items()}, stock)
                    text += (f"\nCovered by stock: none of it is sold at {hub}" if not covered.cost
                             else f"\nCovered by stock ≈ {prices.isk(covered.cost)} ISK")
        self.lbl_fleet_totals.config(text=text)

    def _update_shopping_list_ui(self, price: bool = True):
        """Shows the shopping list, its totals, and enables Copy and Export when there's something on it."""
        priced = self._priced()
        if price:
            self.price_status = ""
        self.shopping_tree.configure(displaycolumns=("item", "qty", "price") if priced else ("item", "qty"))
        self.shopping_tree.delete(*self.shopping_tree.get_children())
        self._shopping_rows = {}
        self._row_lines = {}
        # Plan 21: what's in stock nearby is pulled, under its own heading, before what's bought.
        if self.pull_lines:
            self.shopping_tree.insert("", tk.END, values=("Pull from stock", ""), tags=("heading",))
            for pull in self.pull_lines:
                row = self.shopping_tree.insert("", tk.END, values=(f"{pull.place} · {pull.where}: {pull.item_name}",
                                                                    f"{pull.quantity:,}"))
                if priced:
                    self.shopping_tree.set(row, "price", self._price_cell(priced, pull.type_id, pull.quantity,
                                                                          stock=True))
                self._shopping_rows[row] = pull.for_type_id
                self._row_lines[row] = pull
            if self.buy_items:
                self.shopping_tree.insert("", tk.END, values=("Buy", ""), tags=("heading",))
        for item in self.buy_items:
            name = item.item_name + (f" (or {', '.join(item.alternatives)})" if item.alternatives else "")
            row = self.shopping_tree.insert("", tk.END, values=(name, f"{item.quantity:,}"))
            if priced and item.type_id is not None:
                capital = self.app.galaxy.is_capital(item.type_id)
                self.shopping_tree.set(row, "price", "contracts" if capital else
                                       self._price_cell(priced, item.type_id, item.quantity))
            self._shopping_rows[row] = item_key(item)
            self._row_lines[row] = item
        self.shopping_tree.tag_configure("heading", font=(ui_style.FONT_FAMILY, 9, "bold"))
        if self.shopping_items:
            self.lbl_shopping_empty.place_forget()
            if price:
                self._start_pricing()
            self._show_totals()
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
        self._annotate_losses(results)
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

    def _annotate_losses(self, results):
        """
        Losses (plan 30.3): a missing ship a killmail matched reads "Lost <date>: replace" (or
        "Possibly lost"). Once its requirement passes or soft-fails (a replacement is there or on
        its way), the loss stops showing (D7.3).
        """
        if not esi_features.enabled("losses"):
            return
        from app.asset_handling.killmail_pull import insurance_payout
        book = self.app.loss_book
        for result in results:
            for requirement in result.requirement_results:
                missing = [s for s in requirement.ship_results if s.placement == "MISSING" and s.ship_item_id]
                if not missing:
                    continue
                if requirement.status != RequirementStatus.FAIL or requirement.failure == "soft":
                    book.clear(s.ship_item_id for s in missing)
                    continue
                for ship in missing:
                    ship.loss = book.info(ship.ship_item_id)
                    if ship.loss and esi_features.enabled("losses_insurance"):
                        record = book.records.get(str(ship.loss["killmail_id"]), {})
                        payout = insurance_payout(record.get("ship_type_id") or 0)
                        if payout:
                            ship.loss["insurance"] = prices.isk(payout)
                lost = [s.loss for s in missing if s.loss and s.loss["state"] == "lost"]
                if lost and len(lost) == len(missing):
                    requirement.message = f"Lost {lost[0]['date']}: replace"

    def _handle_this_one_lost(self, ship):
        self.app.loss_book.settle(ship.loss["killmail_id"], ship.ship_item_id)
        self._log(f"[INFO] {ship.custom_name or ship.ship_name} marked as the ship lost on {ship.loss['date']}.")
        self._audit_again()

    def _handle_not_this_one(self, ship):
        self.app.loss_book.rule_out(ship.loss["killmail_id"], ship.ship_item_id)
        self._log(f"[INFO] {ship.custom_name or ship.ship_name} wasn't the ship lost on {ship.loss['date']}.")
        self._audit_again()

    def _handle_copy_srp(self, killmail_id):
        from app.asset_handling.killmail_pull import load_killmails
        from app.services.losses import srp_text
        killmail = load_killmails().get(int(killmail_id))
        if killmail is None:
            messagebox.showinfo(COPY_SRP, "The killmail isn't saved any more.")
            return
        text = srp_text(killmail, self.app.evedb_loader.get_type_name)
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self._log(f"[INFO] SRP items copied: {len(text.splitlines())} line(s).")

    def _add_empty_state_node(self, message: str):
        """Adds a single placeholder node to the tree to indicate an empty state."""
        self.audit_tree.insert('', 'end', text=message, values=("",))

    def _insert_node(self, parent: str, node: Node) -> str:
        item = self.audit_tree.status_icons.insert(parent, 'end', text=node.text, tone=node.tone, open=node.open,
                                                   values=node.values)
        if node.data is not None:
            self._node_data[item] = node.data
            if isinstance(node.data, ChoiceRef):
                self._choice_rows[item] = (node.data.character_id, node.data.req_uid)
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
        self._contract_book, self._choice_rows = None, {}       # contracts read again for each tree (28.3)
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
                note = skills_note(audit_result.requirement_results) if audit_result else None
                if note is not None:
                    self._insert_node(char_id, note)

                # Add requirements as children of the character
                req_count = 0
                for req in role_requirements:
                    req_count += 1
                    matching_req_result = None
                    if audit_result:
                        matching_req_result = next((r for r in audit_result.requirement_results
                                                    if r.req_uid == req.get('req_uid')), None)
                    node = self._requirement_node(req, matching_req_result, carried, char_uid)
                    if node is not None:
                        self._insert_node(char_id, node)

                logger.debug(f"Character {char_name} populated with {req_count} requirements.")

        self._log(f"[SUCCESS] Audit tree populated for {doctrine_name}")

    def _requirement_node(self, req: dict, result, carried, character_id=None) -> "Node | None":
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
        node = requirement_node(req, hull, result, carried,
                                lambda ship: self._location_name(ship.location_id), notes, fit_name,
                                place_label=self.app.evedb_loader.location_label, replacing=replacing)
        if character_id is not None and fitting and not is_implant_set(fitting):
            self._add_contract_nodes(node, req, result, int(character_id), fitting)
        return node

    # --- alliance contracts and the 2-hour pass (ESI features plan 28.3-28.5) -----------------------

    def _book(self):
        """The saved contracts, read once per tree drawn (None while Contracts is off)."""
        if not esi_features.enabled("contracts"):
            return None
        if self._contract_book is None:
            self._contract_book = ContractBook(self.app.auth_service.profiles, self.app.evedb_loader.system_of)
        return self._contract_book

    @staticmethod
    def _whole_ship_missing(result) -> bool:
        """D8.1: a hard requirement with no ship at all, or only ships gone missing (not away or misplaced)."""
        if result is None or result.priority != "hard" or result.status != RequirementStatus.FAIL:
            return False
        if result.unassigned_hulls or result.misplaced:
            return False
        return all(s.placement == "MISSING" for s in result.ship_results)

    def offer_text(self, offer: Offer) -> str:
        """'Exact fit · 79.4 M · Jita IV - Moon 4 - Caldari Navy … · until 12 Oct' (and what's missing or extra)."""
        name = self.app.evedb_loader.get_type_name
        c = offer.contract
        parts = [offer.label or ("Exact fit" if offer.kind == EXACT else "Near fit"), f"{prices.isk(c.price)} ISK",
                 self.app.evedb_loader.location_label(c.location_id)]
        if offer.light_years is not None:
            parts.insert(2, f"{offer.light_years:.1f} ly" if offer.light_years >= 0.05 else "this system")
        if c.expires:
            parts.append(f"until {datetime.fromisoformat(c.expires.replace('Z', '+00:00')):%d %b}")
        diff = []
        if offer.missing:
            diff.append("missing: " + ", ".join(name(t) + (f" ×{q}" if q > 1 else "") for t, q in offer.missing.items()))
        if offer.extra:
            diff.append("extra: " + ", ".join(name(t) + (f" ×{q}" if q > 1 else "") for t, q in offer.extra.items()))
        if c.title:
            parts.append(f"“{c.title}”")
        return " · ".join(parts) + (f" ({'; '.join(diff)})" if diff else "")

    def _add_contract_nodes(self, node, req, result, character_id, fitting):
        """Under a hard requirement whose whole ship is missing: the chosen contract, or the alliance offers."""
        book = self._book()
        if book is None or result is None or result.priority != "hard":
            return
        from app.services.losses import is_super
        if is_super(self.app.evedb_loader.get_type_group(fitting.get("hull_type_id") or 0)):
            return          # a Titan or Super: no contracts (D8.10)
        missing = self._whole_ship_missing(result)
        # The ship turned up (D8.4): any ship that isn't missing, a hull with no fitting yet, or a pass.
        found = (result.status != RequirementStatus.FAIL or result.misplaced or bool(result.unassigned_hulls)
                 or any(s.placement != "MISSING" for s in result.ship_results))
        choice, why = self.contract_choices.review(character_id, req["req_uid"], ship_found=found, book=book)
        names = self.app.auth_service.index
        if why == "gone":
            self._log(f"[INFO] The contract chosen for {names.get(str(character_id), character_id)}'s "
                      f"{fitting.get('hull')} is gone (accepted by someone else, or expired): buy instead.")
            node.children.append(Node("⚠ The chosen contract is gone (accepted by someone else, or expired): "
                                      "buy instead", tone="soft", data=CONTRACT_NOTE))
        elif why == "ship found":
            self._log(f"[INFO] {names.get(str(character_id), character_id)}'s {fitting.get('hull')} is in "
                      "the assets: the contract choice is done.")
        if choice is not None:
            ref = ChoiceRef(character_id, req["req_uid"], choice)
            node.children.append(Node(self.choice_text(choice), data=ref,
                                         tone="soft" if choice.get("accepted_by") else None))
            return
        if not missing:
            return
        details = req if "system_id" in req or "location_id" in req else (result.requirement_details or {})
        location = details.get("location_id")
        system = details.get("system_id") or (self.app.evedb_loader.system_of(location) if location else None)
        is_module = lambda t: self.app.evedb_loader.get_type_category(t) == 7      # noqa: E731
        if self.app.galaxy.is_capital(fitting.get("hull_type_id")):
            self._add_capital_offers(node, book, fitting, character_id, req["req_uid"], system, location, is_module)
            return
        offers = book.offers(fitting, system, location, is_module=is_module)
        if not offers:
            return
        exact = [o for o in offers if o.kind == EXACT]
        near = [o for o in offers if o.kind != EXACT]
        parts = []
        if exact:
            parts.append(f"{len(exact)} exact fit{'s' if len(exact) != 1 else ''} from "
                         f"{prices.isk(min(o.contract.price for o in exact))} ISK")
        if near:
            parts.append(f"{len(near)} near fit{'s' if len(near) != 1 else ''} from "
                         f"{prices.isk(min(o.contract.price for o in near))} ISK")
        at_station = sum(o.at_station for o in offers)
        if at_station and location:
            parts.append(f"{at_station} at {self.app.evedb_loader.location_label(location)}")
        label = f"{fitting.get('hull')} for {names.get(str(character_id), character_id)}"
        children = [Node(self.offer_text(o), data=OfferRef(o, character_id, req["req_uid"], label, offers, i))
                    for i, o in enumerate(offers)]
        node.children.append(Node("📜 On alliance contract (this system): " + " · ".join(parts), children))

    def _add_capital_offers(self, node, book, fitting, character_id, req_uid, system, location, is_module):
        """
        A capital (D8.6, D8.11): alliance contracts within one jump, nearest first, the hull being
        enough (its fit comes from the hub, C3); Find a Hull… searches further.
        """
        from app.services.capitals import label_for, rig_counts
        galaxy = self.app.galaxy
        hull = int(fitting["hull_type_id"])
        names = self.app.auth_service.index
        search_ref = HullSearchRef(hull, fitting, character_id, system)
        if system is None or system not in galaxy.systems:
            return
        reach = galaxy.jump_range(hull)
        nearby = galaxy.within(system, reach)
        others = [f for f in self.fitting_manager.list_fittings()
                  if f.get("hull_type_id") == hull and f.get("fit_uid") != fitting.get("fit_uid")]
        rigs = rig_counts(fitting)
        offers = []
        for contract in book.alliance:
            if contract.system_id not in nearby:
                continue
            labelled = label_for(contract, hull, fitting, others, is_module, rigs)
            if labelled is None:
                continue
            label, kind, missing, extra = labelled
            offers.append(Offer(contract, kind, missing, extra, location is not None and contract.location_id == location,
                                label, nearby[contract.system_id]))
        offers.sort(key=lambda o: (o.light_years, ("exact", "near", "hull").index(o.kind), o.contract.price))
        where = f"within 1 jump, {reach:.1f} ly"
        if not offers:
            node.children.append(Node(f"📜 No alliance contract for the hull {where}: right-click ▸ {FIND_A_HULL}",
                                         data=search_ref))
            return
        counts = {}
        for o in offers:
            counts.setdefault(o.label, []).append(o.contract.price)
        parts = [f"{len(p)} × {k} from {prices.isk(min(p))} ISK" for k, p in counts.items()]
        label = f"{fitting.get('hull')} for {names.get(str(character_id), character_id)}"
        children = [Node(self.offer_text(o), data=OfferRef(o, character_id, req_uid, label, offers, i))
                    for i, o in enumerate(offers)]
        node.children.append(Node(f"📜 On alliance contract ({where}): " + " · ".join(parts), children,
                                     data=search_ref))

    def choice_text(self, choice: dict) -> str:
        names = self.app.auth_service.index
        if choice.get("accepted_by"):
            who = names.get(str(choice["accepted_by"]), f"Character {choice['accepted_by']}")
            return f"⚠ Contract accepted by {who}: waiting for the asset pull"
        return (f"📜 Contract chosen: {left_text(self.contract_choices.left(choice))} left · "
                f"{prices.isk(choice.get('price', 0))} ISK at "
                f"{self.app.evedb_loader.location_label(choice.get('location_id'))}")

    def _audit_again(self):
        if self.audit_mode == "system":
            self._handle_run_system_audit()
        else:
            self._handle_run_doctrine_audit()

    def _handle_use_contract(self, ref):
        """Use This Contract… (D8.3): a 2-hour pass; a replacement already on the list comes off it."""
        if not messagebox.askyesno(USE_CONTRACT.rstrip("…"),
                                   f"Use this contract for {ref.label}?\n\n{self.offer_text(ref.offer)}\n\n"
                                   "For 2 hours this ship won't be bought: it comes off the shopping list, and "
                                   "Add Missing Items asks first. Right-click ▸ Open in Game… to accept it."):
            return
        self.contract_choices.choose(ref.character_id, ref.req_uid, ref.offer, self.offer_text(ref.offer))
        key = ("hull", str(ref.character_id), ref.req_uid)
        if key in self._contributions:
            del self._contributions[key]
            self._share_places.pop(key, None)
            self._added_ships.pop(key, None)
            self._rebuild_shopping_items()
            self._update_shopping_list_ui()
        self._log(f"[INFO] Contract chosen for {ref.label}: {prices.isk(ref.offer.contract.price)} ISK. "
                  "Not bought for the next 2 hours.")
        self._audit_again()

    def _handle_cancel_contract(self, ref):
        self.contract_choices.cancel(ref.character_id, ref.req_uid)
        self._log("[INFO] Contract choice cancelled: the ship is bought as usual.")
        self._audit_again()

    def _handle_open_contract(self, ref):
        """Open in Game… (28.5): one contract window at a time, with Open Next through the rest."""
        steps = [(self.offer_text(o), (lambda o=o: lambda client, char_id: client.open_contract(
                  char_id, o.contract.contract_id))()) for o in ref.offers]
        self.app.open_in_game("Open Contract", steps[ref.index][0], steps[ref.index][1], ref.character_id,
                              steps=steps, start=ref.index)

    def _handle_open_choice(self, ref):
        cid = ref.choice["contract_id"]
        self.app.open_in_game("Open Contract", ref.choice.get("label", f"Contract {cid}"),
                              lambda client, char_id: client.open_contract(char_id, cid), ref.character_id)

    def _tick_choices(self):
        """Redraws the countdowns once a minute while any choice is on the tree."""
        for item, (character_id, req_uid) in list(self._choice_rows.items()):
            choice = self.contract_choices.get(character_id, req_uid)
            if choice is None or not self.audit_tree.exists(item):
                self._choice_rows.pop(item, None)
                continue
            self.audit_tree.item(item, text=self.choice_text(choice))
        try:
            self.root.after(60_000, self._tick_choices)
        except (RuntimeError, tk.TclError):
            pass

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
        self._contract_book, self._choice_rows = None, {}       # contracts read again for each tree (28.3)
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
                note = skills_note(shown.requirement_results)
                if note is not None:
                    self._insert_node(char_row, note)
                for req, result in rows:
                    node = self._requirement_node(req, result, carried, char_id)
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
