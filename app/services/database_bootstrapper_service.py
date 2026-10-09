import json
import logging
import shutil
import sqlite3
import zipfile
import requests
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple
import tempfile

from app.services.sde_builder import SdeCheckError, SdeFormatError, build_sde
from app.services.sde_tables import BUILDER_SCHEMA

# progress(stage, done, total): stage is "download" or "build"; total is None when unknown.
ProgressCallback = Callable[[str, int, Optional[int]], None]

logger = logging.getLogger(__name__)

CHUNK = 1024 * 1024

# Fenris Creations' official SDE (docs/archive/SDE_MIGRATION_PLAN.md, step 9.3). latest.jsonl names the
# current release; each release's zip has its own URL, so the build recorded is the
# one actually downloaded even if Fenris Creations publishes another mid-download.
LATEST_URL = "https://developers.eveonline.com/static-data/tranquility/latest.jsonl"
ZIP_URL = "https://developers.eveonline.com/static-data/tranquility/eve-online-static-data-{build}-jsonl.zip"

# Ask for the zip as it is, so Content-Length gives the progress window a total.
AS_IS = {"Accept-Encoding": "identity"}


@dataclass
class UpdateCheck:
    """What Check for DB Update found (UI rework step 5.5)."""
    status: str                          # "newer", "current", "pending" (downloaded, restart to apply) or "unknown"
    message: str
    remote_date: Optional[str] = None    # Fenris Creations' release date of the latest build (ISO 8601)
    local_date: Optional[str] = None     # the installed database's release date, or its file date
    remote_build: Optional[int] = None
    local_build: Optional[int] = None


class DatabaseBootstrapperService:
    """
    Downloads Fenris Creations' SDE, builds the EVE database from it (sde_builder) and installs it.

    A build is saved as eve.db.new and applied at the next start (the running app
    has eve.db open). Each database records its Fenris Creations build number in its sdeInfo
    table, and eve.db.new.json / config/db_version.json keep the same record next
    to it, so Check for DB Update only has to fetch latest.jsonl to compare.
    """

    def __init__(self, project_root: Path):
        self.project_root = project_root
        self.db_path = project_root / "data" / "eve.db"
        self.data_dir = project_root / "data"
        self.new_db_path = self.db_path.with_name("eve.db.new")
        self.pending_version_path = self.db_path.with_name("eve.db.new.json")
        self.version_path = self.data_dir / "config" / "db_version.json"

    def is_database_present(self) -> bool:
        """Checks if the eve.db file exists."""
        return self.db_path.exists()

    # --- versions ----------------------------------------------------------------

    @staticmethod
    def _read_json(path: Path) -> Optional[Dict]:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    @staticmethod
    def _sde_info(db_path: Path) -> Optional[Dict]:
        """The database's sdeInfo row, or None (a Fuzzwork database has no such table)."""
        if not db_path.exists():
            return None
        try:
            db = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
            try:
                row = db.execute("SELECT buildNumber, releaseDate, schemaVersion FROM sdeInfo").fetchone()
            finally:
                db.close()
        except sqlite3.Error:
            return None
        if row is None:
            return None
        return {"build_number": row[0], "release_date": row[1], "schema_version": row[2]}

    def installed_version(self) -> Optional[Dict]:
        """The installed database's Fenris Creations build, or None for a database that doesn't record one (Fuzzwork's)."""
        info = self._sde_info(self.db_path)
        if info:
            return info
        record = self._read_json(self.version_path)
        return record if record and record.get("build_number") else None

    def pending_version(self) -> Optional[Dict]:
        return self._read_json(self.pending_version_path) if self.new_db_path.exists() else None

    def needs_rebuild(self) -> bool:
        """
        True when the installed database was built by older table definitions
        (sdeInfo.schemaVersion below BUILDER_SCHEMA), so a feature's tables may be
        missing. A database without sdeInfo (Fuzzwork's) counts as schema 1.
        """
        if not self.db_path.exists():
            return False
        info = self._sde_info(self.db_path)
        return (info["schema_version"] if info else 1) < BUILDER_SCHEMA

    @staticmethod
    def latest_release(timeout: float = 15) -> Dict:
        """Fenris Creations' current release: {"build_number", "release_date"}. Raises requests or ValueError errors."""
        response = requests.get(LATEST_URL, timeout=timeout)
        response.raise_for_status()
        for line in response.text.splitlines():
            if line.strip():
                record = json.loads(line)
                if record.get("_key") == "sde":
                    return {"build_number": int(record["buildNumber"]), "release_date": record.get("releaseDate")}
        raise ValueError(f"No 'sde' record in {LATEST_URL}")

    def check_for_update(self, timeout: float = 15) -> UpdateCheck:
        """Asks Fenris Creations which release is current (latest.jsonl, a few bytes) and compares it with the installed one."""
        installed = self.installed_version()
        if installed:
            local_date, local_build = installed.get("release_date"), installed.get("build_number")
        elif self.db_path.exists():
            local_date = datetime.fromtimestamp(self.db_path.stat().st_mtime).strftime("%Y-%m-%d %H:%M") + " (file date)"
            local_build = None
        else:
            local_date = local_build = None

        try:
            latest = self.latest_release(timeout)
        except (requests.exceptions.RequestException, ValueError, KeyError) as e:
            return UpdateCheck("unknown", f"Couldn't reach Fenris Creations' database server: {e}", None, local_date,
                               None, local_build)
        remote = dict(remote_date=latest["release_date"], local_date=local_date,
                      remote_build=latest["build_number"], local_build=local_build)

        pending = self.pending_version()
        if pending and pending.get("build_number") == latest["build_number"]:
            return UpdateCheck("pending", "The latest database is already downloaded. Restart the app to apply it.",
                               **remote)
        if installed is None:
            if self.db_path.exists():
                return UpdateCheck("newer", "Switch to Fenris Creations' official database: the database shrinks from about "
                                   "500 MB to about 30 MB.", **remote)
            return UpdateCheck("newer", "No EVE database is installed.", **remote)
        if local_build >= latest["build_number"]:
            return UpdateCheck("current", "Your EVE database is up to date.", **remote)
        return UpdateCheck("newer", "A newer EVE database is available.", **remote)

    # --- download, build and apply -----------------------------------------------------

    def download_and_install_db(self, progress: Optional[ProgressCallback] = None) -> Tuple[bool, str]:
        """
        Downloads Fenris Creations' latest SDE and builds the database from it as eve.db.new,
        which is applied at the next start (apply_pending_update). Reports progress
        if given a callback. Returns (success, message).
        """
        report = progress or (lambda stage, done, total: None)
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            release = self.latest_release()
            url = ZIP_URL.format(build=release["build_number"])

            # Download and build in a temporary folder: eve.db.new only appears once the build passed its checks.
            with tempfile.TemporaryDirectory() as tmp_dir:
                tmp_path = Path(tmp_dir)
                archive_path = tmp_path / "sde.zip"
                built_path = tmp_path / "eve.db"

                self._log(f"Downloading the SDE from {url}...")
                response = requests.get(url, stream=True, timeout=30, headers=AS_IS)
                response.raise_for_status()
                try:
                    total = int(response.headers.get("Content-Length")) or None
                except (TypeError, ValueError):
                    total = None
                done = 0
                report("download", 0, total)
                with open(archive_path, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=CHUNK):
                        if chunk:
                            f.write(chunk)
                            done += len(chunk)
                            report("download", done, total)

                self._log(f"Building the database from build {release['build_number']}...")
                try:
                    counts = build_sde(archive_path, built_path, report)
                except (SdeFormatError, SdeCheckError, ValueError, sqlite3.Error, zipfile.BadZipFile) as e:
                    return False, f"The downloaded data couldn't be built into a database: {e}"
                self._log(f"Built: {', '.join(f'{name} {count:,}' for name, count in counts.items())}")

                # An older download waiting to be applied is replaced.
                if self.new_db_path.exists():
                    self.new_db_path.unlink()
                shutil.move(str(built_path), str(self.new_db_path))
                self.pending_version_path.write_text(json.dumps({
                    "build_number": release["build_number"],
                    "release_date": release["release_date"],
                    "etag": response.headers.get("ETag"),
                    "last_modified": response.headers.get("Last-Modified"),
                    "source": "ccp",
                    "downloaded_at": datetime.now().isoformat(timespec="seconds"),
                }, indent=2), encoding="utf-8")

                return True, "Database update downloaded successfully. A restart is required to apply the changes."

        except requests.exceptions.ConnectionError:
            return False, "No internet connection detected."
        except requests.exceptions.HTTPError as e:
            return False, f"Download failed (HTTP Error): {e}"
        except requests.exceptions.RequestException as e:
            return False, f"Download failed: {e}"
        except ValueError as e:
            return False, f"Fenris Creations' release information couldn't be read: {e}"
        except OSError as e:
            if e.errno == 28: # No space left on device
                return False, "Insufficient disk space."
            return False, f"File system error: {e}"
        except Exception as e:
            return False, f"An unexpected error occurred: {e}"

    def apply_pending_update(self) -> Optional[str]:
        """
        Swaps a downloaded eve.db.new into place (the old one becomes eve.db.bak) and
        records its version. Returns None if there was nothing to apply, else a log line.
        Raises OSError if the swap fails.
        """
        if not self.new_db_path.exists():
            return None
        if self.db_path.exists():
            shutil.move(str(self.db_path), str(self.db_path.with_suffix(".db.bak")))
        shutil.move(str(self.new_db_path), str(self.db_path))
        if self.pending_version_path.exists():
            self.version_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(self.pending_version_path), str(self.version_path))
        return f"Applied the downloaded database update ({self.db_path}); the old one is kept as eve.db.bak."

    def _log(self, message: str):
        """Internal logging helper: the session log (the packaged build has no console)."""
        logger.info(message)
