import sys
from pathlib import Path

# Determine if the application is running as a frozen executable or as source code
if getattr(sys, 'frozen', False):
    # Frozen mode (PyInstaller)
    # In frozen mode, the executable is in the PROJECT_ROOT
    PROJECT_ROOT = Path(sys.executable).resolve().parent
    # For bundled resources (e.g. Assets), PyInstaller extracts them to _MEIPASS
    _MEIPASS = Path(getattr(sys, '_MEIPASS', PROJECT_ROOT))
else:
    # Source mode
    # Assuming this file is at app/paths.py, so parents[1] is the project root
    PROJECT_ROOT = Path(__file__).resolve().parents[1]
    _MEIPASS = PROJECT_ROOT

DATA_DIR = PROJECT_ROOT / "data"
EVE_DB_PATH = DATA_DIR / "eve.db"

# Writable directories (must be in PROJECT_ROOT/data)
AUTH_DIR = DATA_DIR / "auth"
RAW_DIR = DATA_DIR / "raw"
CORP_DIR = DATA_DIR / "corp"           # corporation hangars per corporation, apart from raw/ (Phase 13)
CLONES_DIR = DATA_DIR / "clones"      # clones and implants per character, apart from raw/ (aggregated)
GENERATED_DIR = DATA_DIR / "generated"
CONFIG_DIR = DATA_DIR / "config"
LOG_DIR = DATA_DIR / "logs"          # one log file per session, newest three kept

# Assets directory (could be bundled or next to exe)
# If we want it to be consistent, we use _MEIPASS if frozen
ASSETS_DIR = _MEIPASS / "Assets"

def initialize_runtime_directories():
    """
    Creates all necessary writable runtime directories.
    Does not create the database file itself.
    """
    directories = [
        DATA_DIR,
        AUTH_DIR,
        RAW_DIR,
        CLONES_DIR,
        CORP_DIR,
        GENERATED_DIR,
        CONFIG_DIR,
        LOG_DIR
    ]
    for directory in directories:
        directory.mkdir(parents=True, exist_ok=True)
