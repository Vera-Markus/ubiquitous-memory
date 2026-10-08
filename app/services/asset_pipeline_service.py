import asyncio
import threading
from typing import Callable, Optional
from app.asset_handling.multi_char_asset_pull import run_multi_char_pull
from app.asset_handling.aggregate_assets import aggregate_assets
from app.esi_service.auth_service import AuthService
from app.esi_service.oauth_config import CLIENT_ID, REDIRECT_URI
from app.esi_service.esi_settings import ESI_BASE_URL
from app.esi_service.real_esi_client import RealESIClient
from app.services.export_resolved_locations import update_location_cache
from app.services.pull_sequence import Hooks, SequenceResult

class AssetPipelineService:
    """
    Service responsible for orchestrating the asset synchronization pipeline.
    """
    def __init__(self, auth_service: Optional[AuthService] = None, ship_designations=None):
        # The app's one AuthService (F7): logins, removals and token refreshes all go
        # through it. Without one (tools, tests), a pull makes its own.
        self.auth_service = auth_service
        self.ship_designations = ship_designations     # the app's ShipDesignations: last seen, 30-day expiry
        self._is_running = False
        self._start_lock = threading.Lock()     # the GUI button and the auto-pull timer can race
        self.last_result: Optional[SequenceResult] = None      # who was pulled, dropped, stopped, held

    @property
    def is_running(self) -> bool:
        """Returns True if the pipeline is currently executing."""
        return self._is_running

    def execute_full_pull(self, log_callback: Callable[[str], None], hooks: Optional[Hooks] = None) -> Optional[bool]:
        """
        Executes the full asset pull and aggregation pipeline.
        
        This method imports and calls:
        1. run_multi_char_pull() - Pulls assets for all authenticated characters.
        2. aggregate_assets() - Combines all pulled JSON files into a single file.

        Args:
            log_callback: A callback function to handle logging messages.

        Returns:
            True if the pipeline completed (some characters may have been skipped:
            see last_result), False if it failed, was stopped or is held, None
            (without running) if a pull is already running. hooks drive the
            safe-mode sequence (plan 25.4).
        """
        with self._start_lock:
            if self._is_running:
                log_callback("[INFO] An asset pull is already running; this request was skipped.")
                return None
            self._is_running = True
        log_callback("[START] Starting full asset pull pipeline...")
        
        try:
            # 1. Run Multi-Character Asset Pull
            log_callback("[INFO] Running multi-character asset pull...")
            
            # We need to run the async function in a way that we can wait for it
            # Since this is a synchronous method, we use asyncio.run
            # However, if there is already a loop running, this might fail.
            # Given this is called from a Thread in main_window.py, it should be fine.
            
            self.last_result = result = asyncio.run(run_multi_char_pull(log_callback, self.auth_service, hooks))

            if not result.any_succeeded:
                log_callback("[ERROR] Multi-character asset pull failed.")
                return False
            # Characters that were pulled are saved: combine them even if the pull stopped part way.

            # 2. Run Asset Aggregation
            log_callback("[INFO] Running asset aggregation...")
            
            aggregate_assets("all_assets.json", log_callback)

            # Ships' assigned fittings: mark the ships this pull saw, forget any unseen for 30 days.
            if self.ship_designations is not None:
                try:
                    from app import paths
                    from app.loaders.ship_designations import refresh_from_pull
                    refresh_from_pull(self.ship_designations, paths.GENERATED_DIR, paths.CORP_DIR, log_callback)
                except Exception as e:
                    log_callback(f"[WARNING] Couldn't update the ships' assigned fittings: {e}")

            if result.held or result.stopped:
                log_callback("[WARNING] The asset pull didn't finish; the characters pulled so far are saved.")
                return False      # no more calls to CCP: it's down, or the user stopped

            # 3. Name every location the assets sit in (NPC stations from the SDE,
            # player structures through ESI) so the audit can match them. The
            # assets are already saved, so a failure here doesn't fail the pull.
            log_callback("[INFO] Updating location names...")
            try:
                asyncio.run(self._update_locations(log_callback))
            except Exception as e:
                log_callback(f"[WARNING] Location names could not be updated: {e}. "
                             "Player structures may not match in the audit until the next pull.")

            log_callback("[SUCCESS] Full asset pull and aggregation completed.")
            return True

        except Exception as e:
            log_callback(f"[ERROR] Pipeline failed: {str(e)}")
            return False
        finally:
            self._is_running = False

    async def _update_locations(self, log_callback: Callable[[str], None]) -> None:
        """Builds the location cache and names structures, trying each logged-in character in turn."""
        auth_service = self.auth_service or AuthService(CLIENT_ID, REDIRECT_URI)
        esi_client = RealESIClient(base_url=ESI_BASE_URL, auth_service=auth_service)
        try:
            await update_location_cache(esi_client, log=log_callback, auth_service=auth_service)
        finally:
            await esi_client.close()
