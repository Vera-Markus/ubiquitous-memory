import os
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

# Global configuration for logging level
# In a production app, this could be loaded from a config file or environment variable.
DEBUG_MODE = os.getenv("EVE_DEBUG", "False").lower() in ("true", "1", "t")

GUI_LOGGER = "app.gui"          # the lines the GUI's _log shows; they carry their own [LEVEL] tag
SESSION_LOGS_KEPT = 3           # this session's file and the two before it
_session_handler: Optional[logging.FileHandler] = None

def setup_logging(level=None):
    """
    Configures the root logger.
    If level is None, uses DEBUG_MODE to decide.
    """
    if level is None:
        level = logging.DEBUG if DEBUG_MODE else logging.INFO

    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )


class _SessionFormatter(logging.Formatter):
    """GUI lines as the log viewer shows them; anything else also names its level and logger."""

    def format(self, record):
        stamp = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
        if record.name == GUI_LOGGER:
            return f"[{stamp}] {record.getMessage()}"
        return f"[{stamp}] [{record.levelname}] {record.name}: {record.getMessage()}"


def start_session_log(log_dir: Path, keep: int = SESSION_LOGS_KEPT) -> Path:
    """
    Starts this session's log file in log_dir and deletes all but the newest `keep`
    session files (this one included). Everything logged through the logging module
    from INFO up goes to the file, and the GUI's _log lines (GUI_LOGGER) go only
    there. Calling it again replaces the previous session file's handler. Returns
    the new file's path.
    """
    global _session_handler
    stop_session_log()
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = log_dir / f"session-{stamp}.log"
    n = 1
    while path.exists():                     # two sessions in the same second
        n += 1
        path = log_dir / f"session-{stamp}-{n}.log"

    _session_handler = logging.FileHandler(path, encoding="utf-8")
    _session_handler.setLevel(logging.DEBUG if DEBUG_MODE else logging.INFO)
    _session_handler.setFormatter(_SessionFormatter())
    root = logging.getLogger()
    root.addHandler(_session_handler)
    if root.level > _session_handler.level or root.level == logging.NOTSET:
        root.setLevel(_session_handler.level)
    # The GUI prints its own lines to the console, so they go only to the file.
    gui = logging.getLogger(GUI_LOGGER)
    gui.propagate = False
    gui.setLevel(logging.INFO)
    gui.addHandler(_session_handler)

    # Oldest first, by last write. This session's file is the newest and always stays.
    sessions = sorted(log_dir.glob("session-*.log"), key=lambda p: (p.stat().st_mtime, p.name))
    for old in sessions[:max(len(sessions) - keep, 0)]:
        if old != path:
            try:
                old.unlink()
            except OSError:
                pass                         # still open elsewhere; it goes next time
    return path


def stop_session_log() -> None:
    """Closes this session's log file, if one is open."""
    global _session_handler
    if _session_handler is not None:
        logging.getLogger().removeHandler(_session_handler)
        logging.getLogger(GUI_LOGGER).removeHandler(_session_handler)
        _session_handler.close()
        _session_handler = None


def session_log_path() -> Optional[Path]:
    """The open session log file, or None."""
    return Path(_session_handler.baseFilename) if _session_handler is not None else None
