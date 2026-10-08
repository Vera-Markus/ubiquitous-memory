import logging
import shutil
import sys
import threading
import asyncio
from collections import deque
from datetime import datetime, timedelta
from app.paths import PROJECT_ROOT, GENERATED_DIR, CONFIG_DIR, EVE_DB_PATH, LOG_DIR, RAW_DIR, AUTH_DIR, CLONES_DIR, CORP_DIR
import tkinter as tk
from tkinter import filedialog, ttk
from app.gui import themed_dialogs as messagebox

from app.version import BUILD_DATE, __version__
from app.logging_config import GUI_LOGGER, session_log_path, start_session_log
from app.gui.dialogs.log_viewer import LogViewer
from app.gui.dialogs.confirm_typed_dialog import ConfirmTypedDialog
from app.gui.dialogs.progress_dialog import ProgressDialog
from app.gui.dialogs.remove_character_dialog import RemoveCharacterDialog
from app.gui.dialogs.send_to_client_dialog import SendToClientDialog
from app.gui.dialogs.structure_name_dialog import StructureNameDialog

from app.loaders.fitting_loader import EVEdbLoader
from app.loaders.fitting_manager import fittingManager
from app.esi_service.auth_service import AuthService
from app.esi_service.esi_settings import ESI_BASE_URL
from app.esi_service.game_client import GameClient
from app.services.prices import PriceBook
from app.services.capitals import Galaxy, PublicContracts
from app.esi_service.game_fittings import FittingBackups, GameFittings
from app.services.losses import LossBook
from app.esi_service.real_esi_client import RealESIClient
from app.loaders.ship_designations import ShipDesignations
from app.esi_service.oauth_config import CLIENT_ID, REDIRECT_URI, SCOPES
from app.services.export_resolved_locations import save_manual_location, skip_location_prompt, structures_to_name
from app.services.pull_state_service import AUTO_PULL_INTERVAL_SECONDS, AUTO_PULL_RETRY_SECONDS, PullStateService
from app.services.pull_sequence import Hooks
from app.services import esi_features
from app.esi_service import server_status as server_status_module
from app.esi_service.server_status import ServerStatus
from app.gui.server_status_icon import ServerStatusIcon
from app.services.asset_pipeline_service import AssetPipelineService
from app.services.reset_service import ResetService
from app.services.character_removal_service import CharacterRemovalService
from app.services.relationship_tree_service import RelationshipTreeService
from email.utils import parsedate_to_datetime
from app.services.database_bootstrapper_service import DatabaseBootstrapperService
from app.loaders.role_manager import RoleManager
from app.loaders.doctrine_manager import DoctrineManager
from app.loaders.package_registry import PackageRegistry
from app.services.audit_collection_service import AuditCollectionService
from app.services.audit_engine import AuditEngine
from app.gui.tabs.audit_tab import AuditTab
from app.gui.tabs.corp_tab import CorpTab
from app.gui.package_actions import PackageActions
from app.gui.tabs.fittings_tab import FittingsTab
from app.gui.tabs.library_tab import LibraryTab
from app.gui import style as ui_style

MAX_LOG_LINES = 2000        # Debug ▸ View Logs shows the most recent lines; the session file keeps them all

# Shown once, and again after a Full Reset (ESI features plan 25.6, D11.10).
STATUS_NOTICE = ("The app now checks Tranquility's status before it calls CCP.\n\n"
                 "The lamp on the right of the tab row shows it: green online, amber with problems "
                 "(VIP mode, or ESI degraded), red offline, grey unknown. A red dot means CCP's status "
                 "page has a new message: hover over the lamp to read it, click it to open the page.\n\n"
                 "While Tranquility is down, and during daily downtime (10:55–11:15 UTC), buttons that "
                 "call CCP are off; everything else keeps working. Automatic pulls wait and try again "
                 "every 5 minutes.")


PUBLIC_CONTRACTS_WARNING = (
    "Find a Hull… will also search public contracts, in every region within 5 capital jumps.\n\n"
    "This is slow the first time: what each contract holds is read one at a time, at a pace ESI accepts. "
    "A wide search can take 5 to 10 minutes. The window fills in nearest first, and contracts read once "
    "aren't read again.\n\nTurn public contracts on?")


class EVEFleetGUI:
    def __init__(self, root):
        self.root = root
        self.root.title(f"EVE Fleet Management Tool {__version__}")
        self.root.geometry("1400x900")
        self.root.minsize(1400, 900)
        self.root.maxsize(1400, 900)
        ui_style.apply(self.root, ui_style.load_theme(CONFIG_DIR))     # the saved colour theme (Options ▸ Appearance)

        self._db_job_running = False    # a database check or download is in progress
        self._login_running = False     # Characters ▸ Add Character is waiting for the browser
        self._auto_pull_pending = False
        self._status_checking = False
        # Tranquility's status, checked before anything calls CCP (ESI features plan, Phase 25)
        self.server_status = ServerStatus()

        # Logging first, so every line from here on reaches the session file (F10).
        self.log_lines = deque(maxlen=MAX_LOG_LINES)
        self.log_viewer = None
        try:
            self.session_log = start_session_log(LOG_DIR)
            # Straight to the file (not the View Logs buffer), so a shared log says which release wrote it
            logging.getLogger(GUI_LOGGER).info(f"EVE Fleet Management Tool {__version__}")
        except OSError as e:
            self.session_log = None
            self._log(f"[WARN] Couldn't start the session log file in {LOG_DIR}: {e}")

        # Constants
        self.CLIENT_ID = CLIENT_ID
        self.REDIRECT_URI = REDIRECT_URI
        self.ESI_BASE_URL = ESI_BASE_URL

        # Initialize Managers & Services
        self.fitting_manager = fittingManager(str(GENERATED_DIR / "fittings.json"))
        self.auth_service = AuthService(self.CLIENT_ID, self.REDIRECT_URI)
        self.esi_client = RealESIClient(self.ESI_BASE_URL, self.auth_service)
        self.game_client = GameClient(self.auth_service)       # Open in the game client (plan 27.1)
        self.price_book = PriceBook()                          # hub prices for the shopping list (plan 28.1)
        self._galaxy = None                                    # systems and capital ranges, read when needed (28.6)
        self.public_contracts = PublicContracts()              # public contract lists and items, cached (28.6)
        self.game_fittings = GameFittings(self.auth_service)   # in-game fitting sync (plan 29)
        self.fitting_backups = FittingBackups()                # fits deleted from the game, 14 days (29.5)
        self.loss_book = LossBook()                            # killmails matched to ships (plan 30)
        self.pull_state_path = CONFIG_DIR / "pull_state.json"
        
        # Initialize Pull State Service
        self.pull_state_service = PullStateService(self.pull_state_path)
        self.pull_state_service.load()

        # Initialize Asset Pipeline Service
        self.asset_pipeline_service = AssetPipelineService(self.auth_service)     # one AuthService (F7)

        # Initialize Reset Service
        self.reset_service = ResetService(PROJECT_ROOT, AUTH_DIR, RAW_DIR, GENERATED_DIR, CONFIG_DIR, CLONES_DIR, CORP_DIR)

        # Check for Database existence before loading services that depend on it
        self.bootstrapper = DatabaseBootstrapperService(PROJECT_ROOT)
        
        # Handle pending database update from previous session
        self._handle_pending_database_update()

        if not self.bootstrapper.is_database_present():
            if messagebox.askyesno("Database Missing", "EVE database not found. Would you like to download it now? "
                                   "(about 100 MB from CCP; the database is built from it in a few seconds)"):
                success, message = self._download_database()
                if not success:
                    messagebox.showerror("Download Failed", message)
                    sys.exit(1)
                else:
                    self._handle_pending_database_update()
                    if not self.bootstrapper.is_database_present():
                        messagebox.showerror("Installation Error", "Database was downloaded but could not be installed. Please check disk space or permissions.")
                        sys.exit(1)
                    messagebox.showinfo("Download Complete", "Database downloaded and installed successfully.")
            else:
                messagebox.showwarning("Database Missing", "The application requires the EVE database to function. It will now exit.")
                sys.exit(1)
        elif self.bootstrapper.needs_rebuild():
            self._handle_database_rebuild()

        # Initialize remaining services and UI
        self._setup_services()
        self._setup_ui()
        self.root.after(100, self._poll_server_status)
        self.root.after(800, self._show_status_notice)

    def _setup_services(self):
        """Initializes the remaining application services."""
        # Initialize Role Manager
        self.role_manager = RoleManager(str(GENERATED_DIR / "roles.json"))
        
        # Initialize Doctrine Manager
        self.doctrine_manager = DoctrineManager(str(GENERATED_DIR / "doctrines.json"))

        # Which records each installed doctrine package put in the library (design §11.4)
        self.package_registry = PackageRegistry(GENERATED_DIR / "installed_packages.json")

        # Initialize Relationship Tree Service
        self.relationship_tree_service = RelationshipTreeService(self.doctrine_manager, self.role_manager)

        # Initialize EVE Database Loader
        self.evedb_loader = EVEdbLoader(str(EVE_DB_PATH))

        # Bring saved fittings up to the current layout (design §12): fighters out of
        # cargo, hull IDs filled. Does nothing once every fitting is current.
        migration = self.fitting_manager.migrate(self.evedb_loader)
        if migration["migrated"]:
            self._log(f"[INFO] Updated {migration['migrated']} saved fitting(s): moved "
                      f"{migration['fighter_types_moved']} fighter type(s) out of cargo.")

        # Initialize Audit Collection Service
        self.audit_collection_service = AuditCollectionService(self.doctrine_manager, self.evedb_loader, GENERATED_DIR)

        # Initialize Audit Engine
        self.audit_engine = AuditEngine(self.doctrine_manager, self.role_manager, self.fitting_manager, self.evedb_loader)

        # Each ship's fitting and owner (Ships tab): the audit follows assigned ships (UI thoughts 18.3)
        self.ship_designations = ShipDesignations(GENERATED_DIR / "ship_designations.json")
        self.asset_pipeline_service.ship_designations = self.ship_designations     # last seen, 30-day expiry

        # Characters ▸ Remove Character: login, asset data and assignments
        self.character_removal_service = CharacterRemovalService(
            self.auth_service, self.doctrine_manager, self.role_manager, RAW_DIR, GENERATED_DIR, CLONES_DIR, CORP_DIR)

        # Character name -> id map (written by the Options tab, read by Audit and Library tabs)
        self.library_char_id_map = {}

        # Sync GUI variables with service
        self.auto_pull_var = tk.BooleanVar(value=self.pull_state_service.auto_pull_enabled)


    def _handle_pending_database_update(self):
        """Applies a database downloaded last session (eve.db.new), keeping the old one as eve.db.bak."""
        try:
            applied = self.bootstrapper.apply_pending_update()
            if applied:
                self._log(f"[INFO] {applied}")
        except OSError as e:
            self._log(f"[ERROR] Database update failed: {e}")

    def _handle_database_rebuild(self):
        """
        The database was built by older table definitions (a later version added
        tables): offer to download and rebuild it now. Declining keeps the old one.
        """
        if not messagebox.askyesno("Database Update Needed", "This version of the tool needs a rebuilt EVE database. "
                                   "Download it now? (about 100 MB from CCP)\n\nWithout it, the app keeps using "
                                   "your current database, and features that need the new data won't work."):
            self._log("[WARNING] The EVE database needs rebuilding for this version; kept the old one for now.")
            return
        success, message = self._download_database()
        if success:
            self._handle_pending_database_update()
            messagebox.showinfo("Database Updated", "The EVE database was rebuilt.")
        else:
            self._log(f"[ERROR] {message}")
            messagebox.showerror("Download Failed", message + "\n\nThe app keeps using your current database.")

    # --- EVE database download and update check (UI rework step 5.5, SDE plan step 9.3) ----

    DB_STAGES = {"download": "Downloading", "build": "Building database"}
    DB_SIZED = ("download",)            # the build counts JSON read, not a size worth showing

    def _run_with_progress(self, title: str, job, stages=None, status: str = "Starting…", sized=None):
        """
        Runs job(report) on a worker thread while a progress window shows it's still
        going, and returns its result (or raises its exception). The window keeps
        the app responsive, also before the main window is built.
        """
        dialog = ProgressDialog(self.root, title, stages, status, sized)
        self.progress_dialog = dialog
        outcome = {}

        def work():
            try:
                outcome["value"] = job(dialog.report)
            except Exception as e:
                outcome["error"] = e
            finally:
                dialog.close()

        threading.Thread(target=work, daemon=True).start()
        dialog.wait()
        if "error" in outcome:
            raise outcome["error"]
        return outcome["value"]

    def _download_database(self):
        """Downloads the EVE database with the progress window. Returns (success, message)."""
        allowed, why = self._may_call_ccp()         # D11.9: nothing goes to CCP during an outage
        if not allowed:
            return False, why
        self._db_job_running = True
        try:
            return self._run_with_progress("Downloading the EVE database",
                                           lambda report: self.bootstrapper.download_and_install_db(report),
                                           self.DB_STAGES, "Connecting…", self.DB_SIZED)
        finally:
            self._db_job_running = False

    @staticmethod
    def _readable_date(value):
        """A release date ("2026-10-02T11:08:57Z") or HTTP date as "2026-10-02 11:08 UTC"; anything else as is."""
        if not value:
            return "unknown"
        for parse in (datetime.fromisoformat, parsedate_to_datetime):
            try:
                return parse(value).strftime("%Y-%m-%d %H:%M UTC")
            except (TypeError, ValueError):
                pass
        return value

    @classmethod
    def _readable_version(cls, build, date):
        """'build 3569502, released 2026-10-02 11:08 UTC', or just the date when the build isn't known."""
        released = cls._readable_date(date)
        return f"build {build}, released {released}" if build else released

    def _handle_check_db_update(self):
        """Tools ▸ Check for DB Update: ask the server, then offer the download only if it's worth it."""
        if self._db_job_running:
            return
        if not self._guard("Check for DB Update"):
            return
        self._db_job_running = True
        try:
            check = self._run_with_progress("Checking for a database update",
                                            lambda report: self.bootstrapper.check_for_update(),
                                            status="Asking CCP's server…")
        finally:
            self._db_job_running = False
        server = self._readable_version(check.remote_build, check.remote_date)
        yours = self._readable_version(check.local_build, check.local_date)
        self._log(f"[INFO] Database update check: {check.status}. CCP's: {server}. Yours: {yours}.")

        details = f"\n\nCCP's latest: {server}\nYours: {yours}"
        if check.status in ("current", "pending"):
            messagebox.showinfo("Check for DB Update", check.message + details)
            return
        if check.status == "newer":
            question = check.message + details + "\n\nDownload it now (about 100 MB)? It's applied the next time the app starts."
        else:
            question = check.message + details + "\n\nDownload the latest database anyway (about 100 MB)? It's applied the next time the app starts."
        if not messagebox.askyesno("Check for DB Update", question):
            return
        self._log("[INFO] Starting database update download...")
        success, message = self._download_database()
        if success:
            self._log(f"[SUCCESS] {message}")
            messagebox.showinfo("Update Downloaded", message)
        else:
            self._log(f"[ERROR] {message}")
            messagebox.showerror("Update Failed", message)

    def _log(self, message: str):
        """
        Prints a message, writes it to the session log file and keeps it for
        Debug ▸ View Logs (F10: the packaged build has no console). Safe to call
        from any thread: the file write happens straight away, and worker threads
        hand the line to the Tk thread for the viewer.
        """
        print(message)
        logging.getLogger(GUI_LOGGER).info(message)
        line = f"[{datetime.now().strftime('%H:%M:%S')}] {message}\n"
        if threading.current_thread() is not threading.main_thread():
            try:
                self.root.after(0, self._append_log, line)
            except (RuntimeError, tk.TclError):
                pass        # the window is closing
            return
        self._append_log(line)

    def _append_log(self, line: str):
        self.log_lines.append(line)
        viewer = self.log_viewer
        if viewer is not None and viewer.is_open():
            try:
                viewer.append(line)
            except tk.TclError as e:
                print(f"[ERROR] Failed to show a line in the log viewer: {e}")

    def _handle_view_logs(self):
        """Debug ▸ View Logs: open the viewer, or bring the open one to the front."""
        if self.log_viewer is not None and self.log_viewer.is_open():
            self.log_viewer.show()
            return
        self.log_viewer = LogViewer(self, list(self.log_lines), MAX_LOG_LINES)

    def _handle_export_logs(self):
        """Debug ▸ Export Logs: save a copy of this session's log file."""
        source = session_log_path()
        default_name = source.name if source else f"session-{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        target = filedialog.asksaveasfilename(
            title="Export Logs", initialfile=default_name, defaultextension=".log",
            filetypes=[("Log files", "*.log"), ("Text files", "*.txt"), ("All files", "*.*")])
        if not target:
            return
        try:
            if source and source.exists():
                shutil.copyfile(source, target)
            else:
                # No session file (it couldn't be created): save what the viewer has.
                with open(target, "w", encoding="utf-8") as f:
                    f.writelines(self.log_lines)
        except OSError as e:
            self._log(f"[ERROR] Failed to export the logs: {e}")
            messagebox.showerror("Export Logs", f"Failed to export the logs:\n{e}")
            return
        self._log(f"[INFO] Logs exported to {target}")

    def _handle_user_guide(self):
        from app.gui.dialogs.user_guide import UserGuideWindow
        UserGuideWindow.show(self.root)

    def _handle_about(self):
        """Help ▸ About: the version, and when this program was built."""
        built = f"Built {BUILD_DATE}" if BUILD_DATE else "Running from source (not a release build)"
        messagebox.showinfo("About", f"EVE Fleet Management Tool\nVersion {__version__}\n{built}\n\n"
                            "MIT licence. An unofficial fan project, not affiliated with or endorsed by CCP hf. "
                            "EVE Online and all related names, images and data are trademarks or property of CCP hf.")

    def _setup_menu(self):
        """Sets up the main application menu bar."""
        self.menu_bar = tk.Menu(self.root)
        self.root.config(menu=self.menu_bar)

        # Characters Menu
        self.characters_menu = tk.Menu(self.menu_bar, tearoff=0)
        self.menu_bar.add_cascade(label="Characters", menu=self.characters_menu)
        self.characters_menu.add_command(label="Add Character", command=self._start_login_thread)
        self.characters_menu.add_command(label="Remove Character…", command=self._handle_remove_character)

        # Tools Menu
        self.tools_menu = tk.Menu(self.menu_bar, tearoff=0)
        self.menu_bar.add_cascade(label="Tools", menu=self.tools_menu)
        self.tools_menu.add_command(label="Check for DB Update", command=self._handle_check_db_update)
        self.tools_menu.add_command(label="Name Unknown Structures…", command=self._handle_name_unknown_structures)
        self.tools_menu.add_separator()
        self.tools_menu.add_command(label="Clear Asset Data…", command=self._handle_clear_assets)
        self.tools_menu.add_command(label="Clear Non-Doctrine Data…", command=self._handle_clear_local_library)
        self.tools_menu.add_command(label="Clear Library…", command=self._handle_clear_library)
        self.tools_menu.add_separator()
        self.tools_menu.add_command(label="Full Reset…", command=self._handle_reset_data)

        # Debug Menu
        self.debug_menu = tk.Menu(self.menu_bar, tearoff=0)
        self.menu_bar.add_cascade(label="Debug", menu=self.debug_menu)
        self.debug_menu.add_command(label="View Logs", command=self._handle_view_logs)
        self.debug_menu.add_command(label="Export Logs", command=self._handle_export_logs)

        # Help Menu: the user guide (Assets/user_guide.md), also on F1
        self.help_menu = tk.Menu(self.menu_bar, tearoff=0)
        self.menu_bar.add_cascade(label="Help", menu=self.help_menu)
        self.help_menu.add_command(label="User Guide", accelerator="F1", command=self._handle_user_guide)
        self.help_menu.add_command(label="About", command=self._handle_about)
        self.root.bind_all("<F1>", lambda e: self._handle_user_guide())

    def _setup_ui(self):
        # Menu Bar
        self._setup_menu()

        # Notebook
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(pady=10, fill=tk.BOTH, expand=True)

        # Tranquility's status, in the empty space on the right of the tab row (plan 25.3)
        self.status_icon = ServerStatusIcon(self.root, self.server_status)
        self.status_icon.place(in_=self.notebook, relx=1.0, x=-6, y=1, anchor="ne")
        
        # Tabs
        self.options_tab = ttk.Frame(self.notebook)
        self.audit_tab = ttk.Frame(self.notebook)
        self.corp_tab = ttk.Frame(self.notebook)
        self.fittings_tab = ttk.Frame(self.notebook)
        self.library_tab = ttk.Frame(self.notebook)
        
        # Add tabs to notebook. Audit comes first, so it's the tab the app opens on.
        self.notebook.add(self.audit_tab, text="Doctrines")
        self.notebook.add(self.corp_tab, text="Ships")
        self.notebook.add(self.library_tab, text="Library")
        self.notebook.add(self.fittings_tab, text="Fittings")
        self.notebook.add(self.options_tab, text="Options")
        
        # Doctrine package export and import (the Library tab's buttons; the Export tab closed in step 7.1)
        self.package_actions = PackageActions(self)

        self.audit_view = AuditTab(self, self.audit_tab)
        self._setup_options_tab()
        self.fittings_view = FittingsTab(self, self.fittings_tab)
        self.library_view = LibraryTab(self, self.library_tab)
        self.corp_view = CorpTab(self, self.corp_tab)


    def _setup_options_tab(self):
        """Sets up the Options tab (formerly Connection): characters and pulls."""
        # Main Content Area (Split Left/Right)
        main_content_frame = ttk.Frame(self.options_tab)
        main_content_frame.pack(pady=10, padx=10, fill=tk.BOTH, expand=True)

        # PanedWindow for splitting the space
        paned_window = tk.PanedWindow(main_content_frame, orient=tk.HORIZONTAL)
        paned_window.pack(fill=tk.BOTH, expand=True)

        # --- Left Side: Connected Characters ---
        char_frame = ttk.LabelFrame(paned_window, text="Connected Characters", padding=(10, 10))
        paned_window.add(char_frame, width=250)
        # The character list takes a quarter of the width the first time the tab is drawn; the divider
        # can still be dragged.
        self._options_split_placed = False

        def place_split(event):
            if not self._options_split_placed and event.width > 100:
                self._options_split_placed = True
                paned_window.sash_place(0, int(event.width * 0.25), 0)
        paned_window.bind("<Configure>", place_split, add="+")

        ttk.Label(char_frame, text="Add characters from Characters ▸ Add Character.",
                 foreground=ui_style.MUTED, wraplength=220, justify=tk.LEFT).pack(anchor=tk.W, pady=(0, 5))

        list_container = ttk.Frame(char_frame)
        list_container.pack(fill=tk.BOTH, expand=True)

        # A themed list like the other tabs' (its selection shows in every theme): click a character
        # to select it; right-click ▸ Remove Character… or the Delete key removes it.
        self.character_list = ttk.Treeview(list_container, show="tree", selectmode="browse")
        self.character_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        scrollbar = ttk.Scrollbar(list_container, orient=tk.VERTICAL, command=self.character_list.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.character_list.config(yscrollcommand=scrollbar.set)
        self.character_menu = tk.Menu(self.character_list, tearoff=0)
        self.character_menu.add_command(label="Remove Character…", command=self._handle_remove_character)
        self.character_list.bind("<Button-3>", self._on_character_right_click)
        self.character_list.bind("<Delete>", lambda e: self._handle_remove_character())

        # --- Right Side: pull status and controls ---
        # Two columns, each as wide as its panels: pulling on the left (Pull All, Asset Status,
        # Auto Pull), settings on the right (Appearance, ESI Features). The rest is left free.
        right_outer = ttk.Frame(paned_window)
        paned_window.add(right_outer)
        right_side_frame = ttk.Frame(right_outer)
        right_side_frame.pack(anchor=tk.NW, padx=5)
        pull_column = ttk.Frame(right_side_frame)
        pull_column.grid(row=0, column=0, sticky="nw", padx=(0, 10))
        settings_column = ttk.Frame(right_side_frame)
        settings_column.grid(row=0, column=1, sticky="nw")

        # Asset cooldown/status panel
        status_panel = ttk.LabelFrame(pull_column, text="Asset Status", padding=(5, 5))

        status_info_frame = ttk.Frame(status_panel)
        status_info_frame.pack(fill=tk.X)

        ttk.Label(status_info_frame, text="Last Pull:").pack(anchor=tk.W)
        self.lbl_last_pull = ttk.Label(status_info_frame, text=self._last_pull_text())
        self.lbl_last_pull.pack(anchor=tk.W)

        ttk.Label(status_info_frame, text="Next Available Pull:").pack(anchor=tk.W, pady=(5, 0))
        self.lbl_next_pull = ttk.Label(status_info_frame, text="Ready")
        self.lbl_next_pull.pack(anchor=tk.W)

        # Auto Pull panel
        auto_pull_panel = ttk.LabelFrame(pull_column, text="Auto Pull", padding=(5, 5))

        self.chk_auto_pull = ttk.Checkbutton(
            auto_pull_panel, 
            text="Enable Auto Pull", 
            variable=self.auto_pull_var,
            command=self._handle_auto_pull_toggle
        )
        self.chk_auto_pull.pack(pady=5, padx=5, anchor=tk.W)

        # Next Auto Pull Label
        self.lbl_next_auto_pull = ttk.Label(auto_pull_panel, text="Next Auto Pull: Ready", font=(ui_style.FONT_FAMILY, 9, "italic"))
        self.lbl_next_auto_pull.pack(pady=2, padx=5, anchor=tk.W)

        # Pull All panel
        pull_all_panel = ttk.LabelFrame(pull_column, text="Pull All", padding=(5, 5))
        # Pull All first, then what it's done and will do.
        for panel in (pull_all_panel, status_panel, auto_pull_panel):
            panel.pack(fill=tk.X, pady=2)

        self.btn_pull_all = ttk.Button(
            pull_all_panel, 
            text="Pull All", 
            command=self._handle_pull_all_click
        )
        self.btn_pull_all.pack(pady=5, padx=5, fill=tk.X)
        # What a pull is doing: safe mode's countdown, characters skipped (plan 25.4)
        self.lbl_pull_status = ttk.Label(pull_all_panel, text="", foreground=ui_style.MUTED, wraplength=380,
                                         justify=tk.LEFT)
        self.lbl_pull_status.pack(padx=5, anchor=tk.W)

        # Appearance panel: the colour theme, applied straight away and remembered
        appearance_panel = ttk.LabelFrame(settings_column, text="Appearance", padding=(5, 5))
        appearance_panel.pack(fill=tk.X, pady=2)
        ttk.Label(appearance_panel, text="Theme:").pack(side=tk.LEFT, padx=5)
        self.theme_combo = ttk.Combobox(appearance_panel, values=list(ui_style.THEMES), state="readonly", width=16)
        self.theme_combo.set(ui_style.current_theme)
        self.theme_combo.pack(side=tk.LEFT, padx=5, pady=5)
        self.theme_combo.bind("<<ComboboxSelected>>", self._handle_theme_selected)

        # ESI Features: one switch per feature, remembered (ESI features plan 26.2)
        features_panel = ttk.LabelFrame(settings_column, text="ESI Features", padding=(5, 5))
        features_panel.pack(fill=tk.X, pady=2)
        self.feature_vars, self.feature_checks = {}, {}
        for key, spec in esi_features.shown().items():
            row = ttk.Frame(features_panel)
            row.pack(fill=tk.X, padx=(24 if spec.get("under") else 5, 5), pady=1)
            var = tk.BooleanVar(value=esi_features.stored(key, CONFIG_DIR))
            check = ttk.Checkbutton(row, text=spec["label"], variable=var,
                                    command=lambda k=key: self._handle_feature_toggle(k))
            check.pack(side=tk.LEFT)
            self.feature_vars[key], self.feature_checks[key] = var, check
            if key == "prices":
                ttk.Label(row, text="Trade hub:").pack(side=tk.LEFT, padx=(12, 4))
                self.hub_combo = ttk.Combobox(row, values=list(esi_features.HUBS), state="readonly", width=12)
                self.hub_combo.set(esi_features.hub(CONFIG_DIR))
                self.hub_combo.pack(side=tk.LEFT)
                self.hub_combo.bind("<<ComboboxSelected>>", self._handle_hub_selected)
        self._update_feature_checks()

        # 3. Initialize Character Loading
        self._initialize_connected_characters()
        self._start_cooldown_timer()

    def _handle_feature_toggle(self, key: str):
        """Options ▸ ESI Features: a switch, saved at once. The next pull or audit follows it."""
        on = self.feature_vars[key].get()
        if key == "public_contracts" and on and not messagebox.askyesno(
                "Public Contracts", PUBLIC_CONTRACTS_WARNING):
            self.feature_vars[key].set(False)
            return
        esi_features.set_enabled(key, on, CONFIG_DIR)
        self._update_feature_checks()
        self.fittings_view.show_game_buttons()
        self._log(f"[INFO] {esi_features.FEATURES[key]['label']}: {'on' if on else 'off'}. "
                  "Run the audit again to see the change.")

    def _update_feature_checks(self):
        """A sub-switch is greyed out while its feature is off."""
        for key, check in self.feature_checks.items():
            parent = esi_features.FEATURES[key].get("under")
            if parent:
                check.state(["!disabled"] if self.feature_vars.get(parent, tk.BooleanVar()).get() else ["disabled"])

    def _handle_hub_selected(self, event=None):
        esi_features.set_hub(self.hub_combo.get(), CONFIG_DIR)
        self.hub_combo.selection_clear()
        self._log(f"[INFO] Prices from {esi_features.hub(CONFIG_DIR)}.")
        self.audit_view.reprice()

    def _handle_theme_selected(self, event=None):
        """Options ▸ Appearance: redraws every open window in the chosen theme and saves it."""
        theme = self.theme_combo.get()
        self.theme_combo.selection_clear()
        if theme == ui_style.current_theme:
            return
        ui_style.apply(self.root, theme)
        ui_style.save_theme(CONFIG_DIR, theme)
        self._log(f"[INFO] Theme: {theme}")

    def _set_add_character_enabled(self, enabled: bool):
        """Characters ▸ Add Character is greyed out while a login is running (and during an outage)."""
        self._login_running = not enabled
        self._update_ccp_controls()

    def _update_ccp_controls(self):
        """Menu entries that call CCP are off while a login runs, or while Tranquility is down (D11.9)."""
        locked = self.server_status.locked()
        self.characters_menu.entryconfig("Add Character",
                                         state=tk.DISABLED if locked or self._login_running else tk.NORMAL)
        self.tools_menu.entryconfig("Check for DB Update", state=tk.DISABLED if locked else tk.NORMAL)

    def _handle_remove_character(self):
        """Characters ▸ Remove Character: choose a character, review what goes, remove."""
        if self.asset_pipeline_service.is_running:
            messagebox.showwarning("Remove Character", "An asset pull is running. Try again when it finishes.")
            return
        if self._login_running:
            messagebox.showwarning("Remove Character", "A login is in progress. Try again when it finishes.")
            return
        characters = {cid: self.auth_service.index.get(cid, f"Character {cid}") for cid in self.auth_service.profiles}
        characters.update(self.auth_service.index)
        if not characters:
            messagebox.showinfo("Remove Character", "There are no connected characters.")
            return
        selection = self.character_list.selection()
        preselect = selection[0] if selection else None       # each row's ID is its character ID
        self.remove_character_dialog = RemoveCharacterDialog(
            self, self.character_removal_service, characters, self._remove_character, preselect)

    def _on_character_right_click(self, event):
        row = self.character_list.identify_row(event.y)
        if not row:
            return
        self.character_list.selection_set(row)
        self.character_list.focus(row)
        self.character_menu.post(event.x_root, event.y_root)

    def _remove_character(self, char_id: str):
        try:
            plan = self.character_removal_service.remove(char_id)
        except Exception as e:
            self._log(f"[ERROR] Failed to remove character {char_id}: {e}")
            messagebox.showerror("Remove Character", f"Failed to remove the character:\n{e}")
            # Show whatever state it was left in.
            self._populate_listbox(self.auth_service.profiles, self.auth_service.index)
            return
        self._log(f"[INFO] Removed {plan.name} ({char_id}): login, {plan.merged_assets} asset item(s), "
                  f"{len(plan.assignments)} role assignment(s).")
        self._populate_listbox(self.auth_service.profiles, self.auth_service.index)
        messagebox.showinfo("Remove Character", f"{plan.name} was removed.")

    def _start_login_thread(self):
        """Characters ▸ Add Character: starts the SSO login flow in a separate thread."""
        if not self._guard("Add Character", login=True):
            return
        self._log("[INFO] Starting CCP Login flow...")
        self._set_add_character_enabled(False)
        
        thread = threading.Thread(target=self._run_async_loop, args=(self._execute_auth_flow(),), daemon=True)
        thread.start()

    def _run_async_loop(self, coro):
        """Runs an asyncio event loop in a background thread."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(coro)
        except Exception as e:
            self.root.after(0, lambda e=e: self._on_auth_failure(str(e)))
        finally:
            loop.close()

    async def _execute_auth_flow(self):
        """The asynchronous sequence of the auth flow."""
        # 1. Perform Full Auth Flow (Redirects, Code Exchange, etc.)
        # Scopes are now centrally managed in oauth_config.py
        tokens, char_id, char_name = await self.auth_service.perform_full_auth_flow(SCOPES)
        
        if not tokens:
            raise Exception("Failed to acquire tokens.")

        # 2. Use the client to get the authenticated character
        # We use the identity returned from the auth flow directly
        character_data = {
            "character_id": char_id,
            "character_name": char_name
        }
        
        # 3. Update UI on success
        self.root.after(0, lambda: self._on_auth_success(character_data))

    def _on_auth_success(self, character_data: dict):
        """Updates the UI after successful authentication."""
        char_name = character_data.get("character_name", "Unknown Character")
        char_id = character_data.get("character_id", "Unknown ID")
        
        self._log(f"[SUCCESS] Authenticated as: {char_name} ({char_id})")
        
        # The Library assigns characters from this map (its overview's right-click menu). A character
        # added again (a new login) replaces its old entry rather than appearing twice.
        self.library_char_id_map = {n: c for n, c in self.library_char_id_map.items() if str(c) != str(char_id)}
        self.library_char_id_map[char_name] = char_id
        self._redraw_character_listbox()
        self.library_view._update_relationship_tree()

        self._set_add_character_enabled(True)

    def _on_auth_failure(self, error_msg: str):
        """Handles errors during the authentication flow."""
        self._log(f"[ERROR] Authentication failed: {error_msg}")
        self._set_add_character_enabled(True)
        messagebox.showerror("Authentication Error", error_msg)

    def _refresh_all_tabs(self):
        """Triggers a refresh of all major UI lists."""
        self.library_view._refresh_doctrine_list()
        self.library_view._refresh_role_list()
        self.fittings_view._refresh_fitting_list()
        # Add more as needed



    def _initialize_connected_characters(self):
        """Loads existing character profiles and populates the character listbox."""
        self._log("[INFO] Initializing connected characters...")
        
        # Run in background thread to avoid blocking UI
        thread = threading.Thread(target=self._run_async_loop, args=(self._load_and_populate_characters(),), daemon=True)
        thread.start()

    def _handle_pull_all_click(self):
        """Handles the Pull All button click."""
        if self.asset_pipeline_service.is_running:
            self._log("[WARNING] Pull All requested while pipeline is already running.")
            return

        if self.pull_state_service.is_in_cooldown():
            self._log("[WARNING] Pull All requested during cooldown.")
            return

        if not self._guard("Pull All"):
            return

        self._log("Pull All pressed")
        self.btn_pull_all.config(state=tk.DISABLED)     # at once; the 1 s timer keeps it in step after that
        threading.Thread(target=self._execute_full_pull, daemon=True).start()

    def _start_cooldown_timer(self):
        """Starts the background timer to update the cooldown UI."""
        self._update_cooldown_ui()

    def _last_pull_text(self) -> str:
        last_pull = self.pull_state_service.last_pull_time
        return last_pull.strftime("%Y-%m-%d %H:%M") if last_pull else "Never"

    def _update_cooldown_ui(self):
        """Updates the pull status every second: Pull All is offered when no pull is running and the cooldown is over."""
        self._check_auto_pull_schedule()
        self.lbl_last_pull.config(text=self._last_pull_text())

        running = self.asset_pipeline_service.is_running
        remaining = self.pull_state_service.cooldown_remaining()
        locked = self.server_status.locked()
        if running:
            self.lbl_next_pull.config(text="Pulling…", foreground=ui_style.INFO)
        elif remaining > 0:
            mins, secs = divmod(int(remaining), 60)
            self.lbl_next_pull.config(text=f"{mins}m {secs:02d}s", foreground=ui_style.WARN)
        elif locked:
            self.lbl_next_pull.config(text="Waiting for Tranquility", foreground=ui_style.WARN)
        else:
            self.lbl_next_pull.config(text="Ready", foreground=ui_style.TEXT)
        self.btn_pull_all.config(state=tk.DISABLED if running or remaining > 0 or locked else tk.NORMAL)
        self._update_ccp_controls()

        # Update the Auto Pull countdown
        if self.auto_pull_var.get():
            next_time = self.pull_state_service.next_auto_pull()
            if next_time:
                remaining_auto = (next_time - datetime.now()).total_seconds()
                if remaining_auto > 0:
                    mins, secs = divmod(int(remaining_auto), 60)
                    self.lbl_next_auto_pull.config(text=f"Next Auto Pull: {mins}m {secs:02d}s", foreground=ui_style.INFO)
                else:
                    self.lbl_next_auto_pull.config(text="Next Auto Pull: Running...", foreground=ui_style.OK)
            else:
                self.lbl_next_auto_pull.config(text="Next Auto Pull: Scheduling...", foreground=ui_style.INFO)
        else:
            self.lbl_next_auto_pull.config(text="Next Auto Pull: Disabled", foreground=ui_style.MUTED)

        self.root.after(1000, self._update_cooldown_ui)

    def _check_auto_pull_schedule(self):
        """Starts the automatic pull when it's due: an hour after the last successful one, or a retry (plan 25.5)."""
        if not self.auto_pull_var.get() or self._auto_pull_pending:
            return

        if self.asset_pipeline_service.is_running:
            return

        now = datetime.now()
        if not self.pull_state_service.auto_pull_due(now):
            return
        # Conflict Management: skip if a manual pull is still in its cooldown
        if self.pull_state_service.is_in_cooldown():
            self._log("Auto Pull skipped due to recent manual pull.")
            # Update last_auto_pull_time to prevent spamming the log every second
            self.pull_state_service.last_auto_pull_time = now
            self.pull_state_service.save()
            return

        self._auto_pull_pending = True
        threading.Thread(target=self._execute_auto_pull, daemon=True).start()

    def _execute_auto_pull(self):
        """
        The automatic pull, on a worker thread. While Tranquility is down it's held
        silently (D11.2) and tried again every 5 minutes; the hourly timer restarts
        from the pull that succeeds (D11.3).
        """
        try:
            allowed, why = self._may_call_ccp()
            if not allowed:
                self._schedule_auto_retry(f"Auto Pull is waiting: {why}")
                return
            self._log(f"[AUTO] {AUTO_PULL_INTERVAL_SECONDS // 60} minute interval reached. Triggering scheduled pull.")
            success = self._execute_full_pull(manual=False)
            if success:
                self.pull_state_service.last_auto_pull_time = datetime.now()
                self.pull_state_service.auto_retry_at = None
                self.pull_state_service.save()
            elif success is False:
                self._schedule_auto_retry("Auto Pull didn't finish")
        finally:
            self._auto_pull_pending = False

    def _schedule_auto_retry(self, why: str):
        at = datetime.now() + timedelta(seconds=AUTO_PULL_RETRY_SECONDS)
        self.pull_state_service.auto_retry_at = at
        self._log(f"[AUTO] {why}. Next try at {at:%H:%M}.")

    def _execute_full_pull(self, manual: bool = True):
        """
        Runs the asset pull pipeline. Callers run this on a worker thread; _log is
        thread-safe and the dialogs are handed to the Tk thread. A manual pull asks
        before retrying and reports how it went; an automatic one shows no dialog at
        all (D11.2, Q25.1): the status line and Last Pull say how it went.
        """
        success = self.asset_pipeline_service.execute_full_pull(self._log, self._pull_hooks(manual))
        self._set_pull_status("")
        if success is None:
            return None             # another pull was already running; the service logged it
        result = self.asset_pipeline_service.last_result
        if success:
            self.pull_state_service.last_pull_time = datetime.now()
            self.pull_state_service.save()      # the service has logged the success line
            if manual:
                self.root.after(0, lambda: self._after_successful_pull(result))
            else:
                skipped = ", ".join(self.auth_service.index.get(c, c) for c in result.dropped) if result else ""
                self._set_pull_status(f"Automatic pull at {datetime.now():%H:%M}: done"
                                      + (f"; skipped {skipped}" if skipped else "") + ".")
        elif result is not None and result.held:
            why = self.server_status.reason() or "Tranquility isn't answering."
            self._log(f"[WARNING] The pull is on hold: {why}")
            if manual:
                self.root.after(0, lambda: messagebox.showwarning("Pull All", f"The pull is on hold.\n\n{why}\n\n"
                                                                  "Characters pulled before it stopped are saved."))
        elif result is not None and result.stopped:
            self._log("[INFO] The pull was stopped; characters pulled before it stopped are saved.")
        else:
            self._log("[ERROR] Asset pull pipeline failed.")
            if manual:
                self.root.after(0, lambda: messagebox.showerror("Error", "An error occurred during the asset pull pipeline."))
        return success

    # --- the safe-mode sequence and Tranquility's status (ESI features plan, Phase 25) -----------

    def _pull_hooks(self, manual: bool) -> Hooks:
        """What the pull sequence needs from the app (plan 25.4)."""
        status = self.server_status

        def ask_retry_blocking(char, outcome) -> bool:
            answer, done = {}, threading.Event()

            def ask():
                try:
                    name = self.auth_service.index.get(char, char)
                    why = outcome.message or (f"ESI error {outcome.status}" if outcome.status else "ESI didn't answer")
                    answer["yes"] = messagebox.askyesno(
                        "Pull Failed", f"The pull failed on {name}: {why}.\n\nThe rest of the pull has stopped. "
                        "Retry all characters?")
                finally:
                    done.set()

            self.root.after(0, ask)
            done.wait()
            return answer.get("yes", False)

        async def ask_retry(char, outcome) -> bool:
            return await asyncio.to_thread(ask_retry_blocking, char, outcome)

        def esi_up() -> bool:
            try:
                status.check(page=True)
            except Exception as e:
                self._log(f"[WARNING] Couldn't check Tranquility's status: {e}")
            self._refresh_status_icon_soon()
            return not status.locked()

        def page_has_error():
            page = server_status_module.fetch_page()
            status.apply_page(page)
            self._refresh_status_icon_soon()
            if not page.ok:
                return None
            if page.has_error:
                status.hold()
                return True
            return False

        return Hooks(manual=manual, ask_retry=ask_retry, esi_up=esi_up, page_has_error=page_has_error,
                     notify=self._set_pull_status)

    def _set_pull_status(self, text: str):
        """The pull panel's status line; anything but the countdown also goes to the log. Any thread."""
        if text and not text.startswith(("Safe mode: checking", "Pulling ")):
            self._log(f"[INFO] {text}")
        try:
            self.root.after(0, lambda: self.lbl_pull_status.config(text=text))
        except (RuntimeError, tk.TclError):
            pass        # the window is closing

    def _may_call_ccp(self, login: bool = False):
        """
        Checks Tranquility's status (unless it was checked in the last 30 s) and says
        whether calls to CCP may go ahead: (allowed, why). Any thread.
        """
        try:
            self.server_status.ensure_fresh()
        except Exception as e:
            self._log(f"[WARNING] Couldn't check Tranquility's status: {e}")
        allowed, why = self.server_status.allow(login=login)
        self._refresh_status_icon_soon()
        return allowed, why

    def _guard(self, title: str, login: bool = False) -> bool:
        """A manual action that calls CCP: allowed, or a pop-up saying why not (D11.1)."""
        allowed, why = self._may_call_ccp(login)
        if not allowed:
            self._log(f"[INFO] {title}: not now. {why}")
            messagebox.showwarning(title, why)
        return allowed

    @property
    def galaxy(self) -> Galaxy:
        """Systems, stations and capital jump ranges (ESI features plan 28.6), read on first use."""
        if self._galaxy is None:
            self._galaxy = Galaxy(EVE_DB_PATH, self.evedb_loader.system_of)
        return self._galaxy

    def find_a_hull(self, hull_type_id, fitting=None, pilot=None, centre=None):
        """The Capital Contract Search window (28.6)."""
        from app.gui.dialogs.capital_search import CapitalSearchWindow
        if not self._guard("Find a Hull"):
            return None
        self.capital_search = CapitalSearchWindow(self, hull_type_id, fitting, pilot, centre)
        return self.capital_search

    def open_in_game(self, title: str, what: str, send, pilot=None, steps=None, start: int = 0):
        """
        Open in the game client (plan 27.1): the character chooser for one window or route.
        send(game_client, char_id) is the call; pilot, the line's pilot, is chosen at first.
        steps: [(what, send)] to go through with Open Next (contracts, 28.5), from `start`.
        """
        if not esi_features.enabled("client"):
            return None
        characters = {str(c): self.auth_service.index.get(str(c), f"Character {c}")
                      for c in self.auth_service.profiles}
        if not characters:
            messagebox.showinfo(title, "No character is logged in: add one under Characters first.")
            return None
        if not self._guard(title):
            return None
        self.client_dialog = SendToClientDialog(self, title, what, characters, send, pilot, steps, start)
        return self.client_dialog

    def _refresh_status_icon_soon(self):
        try:
            self.root.after(0, self._refresh_status_icon)
        except (RuntimeError, tk.TclError):
            pass

    def _refresh_status_icon(self):
        icon = getattr(self, "status_icon", None)
        if icon is not None:
            icon.refresh()

    def _poll_server_status(self):
        """Every 30 s: the icon's own checks, on a worker thread when they're due (D11.16)."""
        if not self._status_checking and self.server_status.status_due(server_status_module.utc_now()):
            self._status_checking = True

            def work():
                try:
                    first = self.server_status.reading is None
                    before = self.server_status.shown_state()
                    self.server_status.check()
                    after = self.server_status.shown_state()
                    if after != before and not first:      # the first check isn't a change
                        self._log(f"[INFO] Tranquility: {after}. {self.server_status.reason()}".rstrip())
                except Exception as e:
                    self._log(f"[WARNING] Couldn't check Tranquility's status: {e}")
                finally:
                    self._status_checking = False
                    self._refresh_status_icon_soon()

            threading.Thread(target=work, daemon=True).start()
        self._refresh_status_icon()
        self.root.after(30_000, self._poll_server_status)

    def _show_status_notice(self):
        """Once, and again after a Full Reset: the app now checks Tranquility's status (D11.10, D11.18)."""
        if self.pull_state_service.status_notice_seen:
            return
        messagebox.showinfo("Tranquility Status", STATUS_NOTICE)
        self.pull_state_service.status_notice_seen = True
        self.pull_state_service.save()

    def match_losses(self, seen_now=None):
        """
        Losses (plan 30.2): new killmails matched to the ships gone since the last pull. Each ship that
        could be it is asked about (Q30.1). A lost Titan or Supercarrier gets condolences and the offer
        to remove its requirement (D8.10).
        """
        from app.asset_handling.corp_pull import load_corporations
        from app.asset_handling.killmail_pull import load_killmails
        from app.loaders.role_manager import fitting_in_use
        from app.loaders.ship_designations import pulled_item_ids
        from app.services.losses import is_super
        if not esi_features.enabled("losses"):
            return []
        designations = self.ship_designations.designations
        requirements = [(role, req) for role in self.role_manager.list_roles() for req in role.get("requirements", [])]

        def tied(d):
            """D7.7: a requirement uses the ship's fitting, in the ship's system (its Home) or anywhere."""
            home = (d.get("home") or {}).get("system_id")
            return any(fitting_in_use(req) == d.get("fit_uid") and
                       (home is None or req.get("system_id") in (None, home)) for _, req in requirements)
        corporations = [c["corporation_id"] for c in load_corporations()] \
            if esi_features.enabled("losses_corporation") else []
        if seen_now is None:
            seen_now = pulled_item_ids(GENERATED_DIR, CORP_DIR)       # what the latest pull saw
        new = self.loss_book.match_new(load_killmails(), designations, seen_now,
                                       [int(c) for c in self.auth_service.profiles], corporations, tied)
        names = self.auth_service.index
        corp_names = {str(c["corporation_id"]): c.get("name") for c in load_corporations()}
        for record in new:
            hull = self.evedb_loader.get_type_name(record["ship_type_id"])
            a_hull = ("an " if hull[:1].upper() in "AEIOU" else "a ") + hull
            who = names.get(str(record["pilot"]), f"Character {record['pilot']}")
            if record["candidates"]:
                self._ask_which_lost(record, hull, a_hull, who, names, corp_names)
            if record["lost"] is not None:
                d = designations.get(record["lost"], {})
                named = f'{hull} "{d["custom_name"]}"' if d.get("custom_name") else hull
                self._log(f"[WARNING] Lost: {self._owner_name(d, names, corp_names)}'s {named} on {record['time'][:10]}.")
            elif record["candidates"]:
                self._log(f"[WARNING] {who} lost {a_hull} on {record['time'][:10]}: {len(record['candidates'])} "
                          "ship(s) could be it (right-click ▸ This One Was Lost, or It Wasn't This One).")
            if is_super(self.evedb_loader.get_type_group(record["ship_type_id"])):
                self._condolences(record, hull, a_hull, who)
        return new

    @staticmethod
    def _owner_name(designation, names, corp_names) -> str:
        owner = designation.get("owner") or designation.get("holder") or {}
        if owner.get("kind") == "character":
            return names.get(str(owner.get("id")), f"Character {owner.get('id')}")
        return corp_names.get(str(owner.get("id"))) or "the corporation"

    def _ask_which_lost(self, record, hull, a_hull, who, names, corp_names):
        """
        Q30.1: the app never decides which ship a killmail was. For each ship that could be it: Yes, it
        was; No, it wasn't (the next one's asked); Cancel, decide later (it stays Possibly lost).
        """
        when = record["time"][:10]
        where = self.evedb_loader.get_system_name(record["system_id"]) if record.get("system_id") else ""
        for item_id in list(record["candidates"]):
            d = self.ship_designations.designations.get(item_id, {})
            whose = self._owner_name(d, names, corp_names)
            named = f'{hull} "{d["custom_name"]}"' if d.get("custom_name") else hull
            fitting = self.fitting_manager.get_fitting(d.get("fit_uid")) if d.get("fit_uid") is not None else None
            home = (d.get("home") or {}).get("system_id")
            about = ", ".join(x for x in ((fitting or {}).get("fit_name"),
                                          f"home {self.evedb_loader.get_system_name(home)}" if home else "") if x)
            answer = messagebox.askyesnocancel(
                "Ship Lost?",
                f"{who} lost {a_hull} on {when}" + (f" in {where}" if where else "") + ".\n\n"
                f"Was it {whose}'s {named}" + (f" ({about})" if about else "") + "? It's gone since an "
                "earlier pull.\n\nYes: it was.\nNo: it wasn't.\nCancel: decide later (right-click it ▸ This "
                "One Was Lost).")
            if answer is None:
                return
            if answer:
                self.loss_book.settle(record["killmail_id"], item_id)
                return
            self.loss_book.rule_out(record["killmail_id"], item_id)

    def _condolences(self, record, hull, a_hull, who):
        """D8.10: thoughts and prayers, and the offer to remove the requirement while they save up."""
        from app.loaders.role_manager import fitting_in_use
        lost = self.ship_designations.designations.get(record.get("lost") or -1, {})
        found = [(role, req) for role in self.role_manager.list_roles() for req in role.get("requirements", [])
                 if lost and fitting_in_use(req) == lost.get("fit_uid")]
        text = (f"{who} lost {a_hull} on {record['time'][:10]}. Our thoughts and prayers are with them, and "
                "with their wallet.")
        if not found:
            messagebox.showinfo("Condolences", text)
            return
        role, req = found[0]
        if messagebox.askyesno("Condolences", text + f"\n\nRemove the {hull} requirement from {role['role_name']} "
                                                     "while they save up for the next one?"):
            self.role_manager.remove_requirement(role["role_uid"], req["req_uid"])
            self._log(f"[INFO] Removed the {hull} requirement from {role['role_name']}.")

    def _after_successful_pull(self, result=None):
        try:
            self.match_losses()
        except Exception as e:
            self._log(f"[WARNING] Losses weren't matched: {e}")
        dropped = result.dropped if result is not None else {}
        if dropped:
            names = self.auth_service.index
            lines = "\n".join(f"• {names.get(c, c)}: {why}" for c, why in dropped.items())
            messagebox.showwarning("Pull Finished", f"The asset pull finished, but these characters were skipped:\n\n"
                                   f"{lines}\n\nTheir assets are from the last pull that reached them.")
        else:
            messagebox.showinfo("Success", "Asset pull and aggregation completed successfully.")
        self._name_unknown_structures(include_skipped=False)

    # --- structures no character could look up (dead code clean-up C2) -------------------

    def _handle_name_unknown_structures(self):
        """Tools ▸ Name Unknown Structures: every unknown structure, skipped ones included."""
        title = "Name Unknown Structures"
        if self._pull_running(title):
            return
        if not self._name_unknown_structures(include_skipped=True):
            messagebox.showinfo(title, "Every structure your characters have assets in has a name.")

    def _name_unknown_structures(self, include_skipped: bool) -> int:
        """
        Offers to name, one at a time, the structures no character could look up. Skip
        (or closing the window) stops the prompt after each pull for that structure.
        Returns how many structures were offered.
        """
        found = structures_to_name(include_skipped=include_skipped)
        if not found:
            return 0
        systems = {s["solarSystemName"]: s["solarSystemID"] for s in self.evedb_loader.get_all_solar_systems()}
        names = self.auth_service.index
        for number, (structure_id, holders) in enumerate(found, 1):
            result = StructureNameDialog(self.root, structure_id, [names.get(h, h) for h in holders], systems,
                                         position=f"{number} of {len(found)}").ask()
            if result:
                _, name, system_id = result
                save_manual_location(structure_id, name, system_id)
                self._log(f"[INFO] Named structure {structure_id} by hand: {name} "
                          f"({self.evedb_loader.get_system_name(system_id)})")
            else:
                skip_location_prompt(structure_id)
                self._log(f"[INFO] Skipped naming structure {structure_id}; Tools ▸ Name Unknown Structures can name it later.")
        return len(found)

    # --- Tools ▸ delete options (UI rework step 5.6) -----------------------------------------

    def _pull_running(self, title: str) -> bool:
        if self.asset_pipeline_service.is_running:
            messagebox.showwarning(title, "An asset pull is running. Try again when it finishes.")
            return True
        return False

    def _refresh_after_library_change(self):
        """Every view that shows library records, after a clear or a package import."""
        self._refresh_all_tabs()
        self.library_view._refresh_role_selector_combo()
        self.library_view._populate_library_hulls()
        self.library_view._refresh_library_requirement_list()
        self.library_view._populate_library_doctrines()     # also redraws the relationship tree
        self._populate_audit_doctrine_combo()

    def _handle_clear_assets(self):
        """Tools ▸ Clear Asset Data: pulled assets only."""
        title = "Clear Asset Data"
        if self._pull_running(title):
            return
        files = self.reset_service.asset_files()
        if not messagebox.askyesno(title,
                f"This deletes the pulled asset data for every character ({len(files)} file(s)).\n\n"
                "Logins, the library, saved location names and the Auto Pull setting stay. Audits show "
                "no assets until the next pull, which can run straight away.\n\nClear the asset data?"):
            return
        try:
            removed = self.reset_service.clear_assets(self.pull_state_service)
        except OSError as e:
            self._log(f"[ERROR] Failed to clear the asset data: {e}")
            messagebox.showerror(title, f"Failed to clear the asset data:\n{e}")
            return
        self._log(f"[INFO] Cleared the asset data ({removed} file(s)).")
        messagebox.showinfo(title, f"The asset data was cleared ({removed} file(s)).")

    def _handle_clear_local_library(self):
        """Tools ▸ Clear Non-Doctrine Data: everything outside the protected (package) ranges."""
        title = "Clear Non-Doctrine Data"
        plan = self.reset_service.plan_clear_local_library(self.fitting_manager, self.role_manager, self.doctrine_manager)
        if plan.is_empty():
            messagebox.showinfo(title, "There's no non-doctrine data: everything in the library is in the "
                                       "package ranges (made in Doctrine Mode or imported from a package).")
            return
        if not messagebox.askyesno(title, plan.summary() + "\n\nThis can't be undone. Clear it?"):
            return
        self.reset_service.clear_local_library(self.fitting_manager, self.role_manager, self.doctrine_manager)
        self._log(f"[INFO] Cleared non-doctrine data: {len(plan.fit_uids)} fitting(s), {len(plan.role_uids)} "
                  f"role(s), {len(plan.doctrine_uids)} doctrine(s).")
        self._refresh_after_library_change()
        messagebox.showinfo(title, "The non-doctrine data was cleared.")

    def _handle_clear_library(self):
        """Tools ▸ Clear Library: every fitting, role and doctrine. Offers an export first."""
        title = "Clear Library"
        answer = messagebox.askyesnocancel(title,
            "Clearing the library deletes every fitting, role and doctrine.\n\n"
            "Export a doctrine package first?\n\n"
            "Yes: open the Export Package window (run Clear Library again afterwards).\n"
            "No: continue to clearing.")
        if answer is None:
            return
        if answer:
            self.package_actions._handle_export_package()
            return
        fits, roles, doctrines = (len(self.fitting_manager.list_fittings()), len(self.role_manager.roles),
                                  len(self.doctrine_manager.doctrines))
        if not messagebox.askyesno(title,
                f"Delete {fits} fitting(s), {roles} role(s) and {doctrines} doctrine(s), with every requirement "
                "and character assignment, the ships' assigned fittings, the installed-package records and the "
                "remembered export package names?\n\nAsset data and logins stay. This can't be undone."):
            return
        counts = self.reset_service.clear_library(self.fitting_manager, self.role_manager, self.doctrine_manager,
                                                  self.package_registry,
                                                  ship_designations=self.ship_designations)
        self._log(f"[INFO] Cleared the library: {counts[0]} fitting(s), {counts[1]} role(s), {counts[2]} doctrine(s).")
        self._refresh_after_library_change()
        messagebox.showinfo(title, "The library was cleared.")

    def _handle_reset_data(self):
        """Tools ▸ Full Reset: everything, behind a typed RESET confirmation."""
        confirm = ConfirmTypedDialog(
            self.root, "Full Reset",
            "This PERMANENTLY deletes:\n"
            "- all character logins\n"
            "- all downloaded asset data\n"
            "- your whole library: fittings, roles and doctrines, with their requirements, "
            "character assignments and installed package records\n"
            "- the pull times and the remembered export package names\n\n"
            "Export a doctrine package first if you want to keep your doctrines. "
            "The app closes when the reset is done.",
            "RESET").ask()

        if not confirm:
            return

        self._log("[WARN] User initiated local data reset.")

        # Run reset in a separate thread to keep GUI responsive
        threading.Thread(target=self._execute_reset_data, daemon=True).start()

    def _execute_reset_data(self):
        """Performs the actual deletion of local data files using ResetService."""
        try:
            self._log("[INFO] Starting data reset process...")
            
            # Call the service to perform the actual deletion
            success, message = self.reset_service.reset_local_data()

            if success:
                # Instead of attempting to reconstruct state, inform user restart is required.
                self.root.after(0, lambda: messagebox.showinfo("Reset Complete", f"{message}\n\nA restart of the application is required to apply changes."))
                self._log(f"[SUCCESS] {message}. Restart required.")
                # Gracefully close the application
                self.root.after(0, self.root.destroy)
            else:
                self._log(f"[ERROR] Reset failed: {message}")
                self.root.after(0, lambda: messagebox.showerror("Reset Error", message))

        except Exception as e:
            self._log(f"[ERROR] An unexpected error occurred during reset: {str(e)}")
            self.root.after(0, lambda e=e: messagebox.showerror("Reset Error", f"An unexpected error occurred: {e}"))

    def _handle_auto_pull_toggle(self):
        """Handles the Auto Pull toggle change."""
        self.pull_state_service.auto_pull_enabled = self.auto_pull_var.get()
        state = "Enabled" if self.pull_state_service.auto_pull_enabled else "Disabled"
        self._log(f"Auto Pull {state}")
        self.pull_state_service.save()

    async def _load_and_populate_characters(self):
        """Asynchronously loads profiles and updates the UI."""
        try:
            # 1. Purge expired profiles first
            await self.auth_service.purge_expired_profiles(days=30)
            
            # 2. Load current profiles from service
            profiles = self.auth_service.profiles
            index = self.auth_service.index
            
            if not profiles:
                self._log("[INFO] No connected characters found.")
                return

            self._log(f"[INFO] Found {len(profiles)} connected characters.")

            # 3. Update UI
            self.root.after(0, lambda: self._populate_listbox(profiles, index))
            
        except Exception as e:
            self._log(f"[ERROR] Failed to load connected characters: {e}")
            self.root.after(0, lambda e=e: self._on_auth_failure(f"Initialization error: {e}"))

    def _populate_listbox(self, profiles: dict, index: dict):
        """Updates the character listbox and the Library's character map with loaded profiles."""
        # The Library assigns characters from this map
        self.library_char_id_map = {index.get(char_id, f"Unknown ({char_id})"): char_id for char_id in profiles}
        self._redraw_character_listbox()
        self.library_view._update_relationship_tree()

        self._log(f"[SUCCESS] Loaded {len(profiles)} characters.")

    def _redraw_character_listbox(self):
        """The Options tab's characters, alphabetical (UI thoughts 11, plan 17.2)."""
        self.character_list.delete(*self.character_list.get_children())
        for name, char_id in sorted(self.library_char_id_map.items(), key=lambda c: c[0].casefold()):
            self.character_list.insert("", tk.END, iid=str(char_id), text=name)

    def _populate_audit_doctrine_combo(self):
        """Cross-tab refresh: doctrine changes update the Audit tab selector."""
        self.audit_view._populate_audit_doctrine_combo()
