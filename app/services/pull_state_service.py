import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

PULL_COOLDOWN_SECONDS = 900             # after a successful pull, before Pull All is offered again
AUTO_PULL_INTERVAL_SECONDS = 3600       # between automatic pulls
AUTO_PULL_RETRY_SECONDS = 300           # after a blocked or failed automatic pull (plan 25.5)

class PullStateService:
    def __init__(self, pull_state_path: Path):
        self.pull_state_path = pull_state_path
        self.auto_pull_enabled = False
        self.last_pull_time: Optional[datetime] = None
        self.last_auto_pull_time: Optional[datetime] = None
        self.auto_retry_at: Optional[datetime] = None     # not saved: a restart tries straight away
        # The first-start notice that the app checks Tranquility's status (plan 25.6). It's kept
        # here so a Full Reset, which deletes this file, shows it again.
        self.status_notice_seen = False

    def load(self):
        """Loads the pull state from the config file."""
        try:
            if self.pull_state_path.exists():
                with open(self.pull_state_path, 'r') as f:
                    state = json.load(f)
                    self.auto_pull_enabled = state.get("auto_pull_enabled", False)
                    
                    lp_str = state.get("last_pull_time")
                    self.last_pull_time = datetime.fromisoformat(lp_str) if lp_str else None
                    
                    lap_str = state.get("last_auto_pull_time")
                    self.last_auto_pull_time = datetime.fromisoformat(lap_str) if lap_str else None
                    self.status_notice_seen = bool(state.get("status_notice_seen", False))
            else:
                self.auto_pull_enabled = False
                self.last_pull_time = None
                self.last_auto_pull_time = None
        except Exception as e:
            logger.error(f"Failed to load pull state: {e}")
            self.auto_pull_enabled = False
            self.last_pull_time = None
            self.last_auto_pull_time = None

    def save(self):
        """Saves the current pull state to the config file."""
        try:
            state = {
                "auto_pull_enabled": self.auto_pull_enabled,
                "last_pull_time": self.last_pull_time.isoformat() if self.last_pull_time else None,
                "last_auto_pull_time": self.last_auto_pull_time.isoformat() if self.last_auto_pull_time else None,
                "status_notice_seen": self.status_notice_seen,
            }
            with open(self.pull_state_path, 'w') as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save pull state: {e}")

    def cooldown_remaining(self, cooldown_seconds: int = PULL_COOLDOWN_SECONDS) -> float:
        """Seconds until Pull All is offered again; 0 when the cooldown is over (or there was no pull)."""
        if not self.last_pull_time:
            return 0
        return max(cooldown_seconds - (datetime.now() - self.last_pull_time).total_seconds(), 0)

    def is_in_cooldown(self, cooldown_seconds: int = PULL_COOLDOWN_SECONDS) -> bool:
        """Checks if the manual pull cooldown is currently active."""
        return self.cooldown_remaining(cooldown_seconds) > 0

    def auto_pull_due(self, now: datetime, interval_seconds: int = AUTO_PULL_INTERVAL_SECONDS) -> bool:
        """An hour since the last successful automatic pull, and any retry wait over (plan 25.5)."""
        if self.auto_retry_at is not None and now < self.auto_retry_at:
            return False
        return self.last_auto_pull_time is None or (now - self.last_auto_pull_time).total_seconds() >= interval_seconds

    def next_auto_pull(self, interval_seconds: int = AUTO_PULL_INTERVAL_SECONDS) -> Optional[datetime]:
        """When the next automatic pull is due: a retry, or an hour after the last success."""
        if self.auto_retry_at is not None:
            return self.auto_retry_at
        if self.last_auto_pull_time is None:
            return None
        from datetime import timedelta
        return self.last_auto_pull_time + timedelta(seconds=interval_seconds)
