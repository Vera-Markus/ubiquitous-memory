import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

PULL_COOLDOWN_SECONDS = 900             # after a successful pull, before Pull All is offered again
AUTO_PULL_INTERVAL_SECONDS = 3600       # between automatic pulls

class PullStateService:
    def __init__(self, pull_state_path: Path):
        self.pull_state_path = pull_state_path
        self.auto_pull_enabled = False
        self.last_pull_time: Optional[datetime] = None
        self.last_auto_pull_time: Optional[datetime] = None

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
                "last_auto_pull_time": self.last_auto_pull_time.isoformat() if self.last_auto_pull_time else None
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
