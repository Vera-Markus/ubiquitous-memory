import logging
import shutil
import sys
import threading
import asyncio
from collections import deque
from datetime import datetime, timedelta
from app.paths import PROJECT_ROOT, GENERATED_DIR, CONFIG_DIR, EVE_DB_PATH, LOG_DIR, RAW_DIR, AUTH_DIR, CLONES_DIR, CORP_DIR
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from app.version import __version__
from app.logging_config import GUI_LOGGER, session_log_path, start_session_log
from app.gui.dialogs.log_viewer import LogViewer
from app.gui.dialogs.confirm_typed_dialog import ConfirmTypedDialog
from app.gui.dialogs.progress_dialog import ProgressDialog
from app.gui.dialogs.remove_character_dialog import RemoveCharacterDialog
from app.gui.dialogs.structure_name_dialog import StructureNameDialog

from app.loaders.fitting_loader import EVEdbLoader
from app.loaders.fitting_manager import fittingManager
from app.esi_service.auth_service import AuthService
from app.esi_service.esi_settings import ESI_BASE_URL
from app.esi_service.real_esi_client import RealESIClient
from app.loaders.ship_designations import ShipDesignations
from app.esi_service.oauth_config import CLIENT_ID, REDIRECT_URI, SCOPES
from app.services.export_resolved_locations import save_manual_location, skip_location_prompt, structures_to_name
from app.services.pull_state_service import AUTO_PULL_INTERVAL_SECONDS, PullStateService
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


class EVEFleetGUI:
    def __init__(self, root):
        self.root = root
        self.root.title(f"EVE Fleet Management Tool {__version__}")
        self.root.geometry("1400x900")
        self.root.minsize(1400, 900)
        self.root.maxsize(1400, 900)
        ui_style.apply(self.root, ui_style.load_theme(CONFIG_DIR))     # the saved colour theme (Options ▸ Appearance)

        self._db_job_running = False    # a database check or download is in progress

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
        self.root.bind_all("<F1>", lambda e: self._handle_user_guide())

    def _setup_ui(self):
        # Menu Bar
        self._setup_menu()

        # Notebook
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(pady=10, fill=tk.BOTH, expand=True)
        
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

        ttk.Label(char_frame, text="Add characters from Characters ▸ Add Character.",
                 foreground=ui_style.MUTED, wraplength=220, justify=tk.LEFT).pack(anchor=tk.W, pady=(0, 5))

        list_container = ttk.Frame(char_frame)
        list_container.pack(fill=tk.BOTH, expand=True)

        self.character_listbox = tk.Listbox(list_container)
        self.character_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        scrollbar = ttk.Scrollbar(list_container, orient=tk.VERTICAL, command=self.character_listbox.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.character_listbox.config(yscrollcommand=scrollbar.set)

        # --- Right Side: pull status and controls ---
        right_side_frame = ttk.Frame(paned_window)
        paned_window.add(right_side_frame)

        # Asset cooldown/status panel
        status_panel = ttk.LabelFrame(right_side_frame, text="Asset Status", padding=(5, 5))
        status_panel.pack(fill=tk.X, pady=2, padx=5)

        status_info_frame = ttk.Frame(status_panel)
        status_info_frame.pack(fill=tk.X)

        ttk.Label(status_info_frame, text="Last Pull:").pack(anchor=tk.W)
        self.lbl_last_pull = ttk.Label(status_info_frame, text=self._last_pull_text())
        self.lbl_last_pull.pack(anchor=tk.W)

        ttk.Label(status_info_frame, text="Next Available Pull:").pack(anchor=tk.W, pady=(5, 0))
        self.lbl_next_pull = ttk.Label(status_info_frame, text="Ready")
        self.lbl_next_pull.pack(anchor=tk.W)

        # Auto Pull panel
        auto_pull_panel = ttk.LabelFrame(right_side_frame, text="Auto Pull", padding=(5, 5))
        auto_pull_panel.pack(fill=tk.X, pady=2, padx=5)

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
        pull_all_panel = ttk.LabelFrame(right_side_frame, text="Pull All", padding=(5, 5))
        pull_all_panel.pack(fill=tk.X, pady=2, padx=5)

        self.btn_pull_all = ttk.Button(
            pull_all_panel, 
            text="Pull All", 
            command=self._handle_pull_all_click
        )
        self.btn_pull_all.pack(pady=5, padx=5, fill=tk.X)

        # Appearance panel: the colour theme, applied straight away and remembered
        appearance_panel = ttk.LabelFrame(right_side_frame, text="Appearance", padding=(5, 5))
        appearance_panel.pack(fill=tk.X, pady=2, padx=5)
        ttk.Label(appearance_panel, text="Theme:").pack(side=tk.LEFT, padx=5)
        self.theme_combo = ttk.Combobox(appearance_panel, values=list(ui_style.THEMES), state="readonly", width=16)
        self.theme_combo.set(ui_style.current_theme)
        self.theme_combo.pack(side=tk.LEFT, padx=5, pady=5)
        self.theme_combo.bind("<<ComboboxSelected>>", self._handle_theme_selected)

        # 3. Initialize Character Loading
        self._initialize_connected_characters()
        self._start_cooldown_timer()

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
        """Characters ▸ Add Character is greyed out while a login is running."""
        self.characters_menu.entryconfig("Add Character", state=tk.NORMAL if enabled else tk.DISABLED)

    def _handle_remove_character(self):
        """Characters ▸ Remove Character: choose a character, review what goes, remove."""
        if self.asset_pipeline_service.is_running:
            messagebox.showwarning("Remove Character", "An asset pull is running. Try again when it finishes.")
            return
        if str(self.characters_menu.entrycget("Add Character", "state")) == "disabled":
            messagebox.showwarning("Remove Character", "A login is in progress. Try again when it finishes.")
            return
        characters = {cid: self.auth_service.index.get(cid, f"Character {cid}") for cid in self.auth_service.profiles}
        characters.update(self.auth_service.index)
        if not characters:
            messagebox.showinfo("Remove Character", "There are no connected characters.")
            return
        preselect = None
        selection = self.character_listbox.curselection()
        if selection:
            preselect = self.character_listbox.get(selection[0]).rsplit("(", 1)[-1].rstrip(")")
        self.remove_character_dialog = RemoveCharacterDialog(
            self, self.character_removal_service, characters, self._remove_character, preselect)

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
        if running:
            self.lbl_next_pull.config(text="Pulling…", foreground=ui_style.INFO)
        elif remaining > 0:
            mins, secs = divmod(int(remaining), 60)
            self.lbl_next_pull.config(text=f"{mins}m {secs:02d}s", foreground=ui_style.WARN)
        else:
            self.lbl_next_pull.config(text="Ready", foreground=ui_style.TEXT)
        self.btn_pull_all.config(state=tk.DISABLED if running or remaining > 0 else tk.NORMAL)

        # Update the Auto Pull countdown
        if self.auto_pull_var.get():
            last_auto = self.pull_state_service.last_auto_pull_time
            if last_auto:
                next_time = last_auto + timedelta(seconds=AUTO_PULL_INTERVAL_SECONDS)
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
        """Checks if it is time to trigger the automatic pull."""
        if not self.auto_pull_var.get():
            return

        if self.asset_pipeline_service.is_running:
            return

        now = datetime.now()
        last_auto = self.pull_state_service.last_auto_pull_time

        # If it's never run, or if the interval has passed since the last one
        if last_auto is None or (now - last_auto).total_seconds() >= AUTO_PULL_INTERVAL_SECONDS:
            # Conflict Management: skip if a manual pull is still in its cooldown
            if self.pull_state_service.is_in_cooldown():
                self._log("Auto Pull skipped due to recent manual pull.")
                # Update last_auto_pull_time to prevent spamming the log every second
                self.pull_state_service.last_auto_pull_time = now
                self.pull_state_service.save()
                return

            self._log(f"[AUTO] {AUTO_PULL_INTERVAL_SECONDS // 60} minute interval reached. Triggering scheduled pull.")
            # Reset the timer immediately to avoid double-triggering before the process finishes
            self.pull_state_service.last_auto_pull_time = now
            self.pull_state_service.save()
            
            # Run the existing pipeline in a background thread
            threading.Thread(target=self._execute_full_pull, daemon=True).start()

    def _execute_full_pull(self):
        """
        Runs the asset pull pipeline. Callers run this on a worker thread; _log is
        thread-safe and the dialogs are handed to the Tk thread.
        """
        success = self.asset_pipeline_service.execute_full_pull(self._log)
        if success is None:
            return              # another pull was already running; the service logged it
        if success:
            self.pull_state_service.last_pull_time = datetime.now()
            self.pull_state_service.save()      # the service has logged the success line
            self.root.after(0, self._after_successful_pull)
        else:
            self._log("[ERROR] Asset pull pipeline failed.")
            self.root.after(0, lambda: messagebox.showerror("Error", "An error occurred during the asset pull pipeline."))

    def _after_successful_pull(self):
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
        self.character_listbox.delete(0, tk.END)
        for name, char_id in sorted(self.library_char_id_map.items(), key=lambda c: c[0].casefold()):
            self.character_listbox.insert(tk.END, f"{name} ({char_id})")

    def _populate_audit_doctrine_combo(self):
        """Cross-tab refresh: doctrine changes update the Audit tab selector."""
        self.audit_view._populate_audit_doctrine_combo()
