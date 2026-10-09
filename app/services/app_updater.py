"""
Help ▸ Check for Updates: a newer release of the app, from the public release repo on GitHub.

The check reads the repo's release list (pre-releases included: every RC is one, and
GitHub's "latest release" call skips them) and compares the newest tag with
app/version.py. Only the part before the hyphen counts, as in the release workflow:
v1.7.1-rc7.1 is 1.7.1.

An installed copy downloads the new setup.exe, checks it against the release's
SHA256SUMS.txt and runs it silently with /RELAUNCH=1, which reopens the app when it's
done (installer/EveFleetManagementTool.iss). data\\ is never touched. A portable copy, or
the app running from source, is pointed to the release page instead.

The once-a-day check at startup is switched in Options ▸ Updates and saved in
data/config/ui_settings.json beside the theme.
"""
import hashlib
import json
import logging
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import requests

from app import paths
from app.version import __version__

logger = logging.getLogger(__name__)

RELEASE_REPO = "Vera-Markus/ubiquitous-memory"
RELEASES_URL = f"https://api.github.com/repos/{RELEASE_REPO}/releases?per_page=20"
HEADERS = {"User-Agent": f"EveFleetManagementTool/{__version__}", "Accept": "application/vnd.github+json"}
SETUP_SUFFIX = "-setup.exe"
SUMS_NAME = "SHA256SUMS.txt"
CHUNK = 1024 * 1024

SETTINGS_FILE = "ui_settings.json"          # shared with the theme and ESI features
AT_STARTUP_KEY = "update.check_at_startup"
LAST_CHECK_KEY = "update.last_check"
CHECK_EVERY = timedelta(days=1)

# Inno Setup: no wizard, no message boxes, close the running app, and reopen it once, through the
# iss's [Run] (/NORESTARTAPPLICATIONS: Restart Manager mustn't start a second copy).
INSTALLER_ARGS = ["/SILENT", "/SUPPRESSMSGBOXES", "/CLOSEAPPLICATIONS", "/NORESTARTAPPLICATIONS", "/NORESTART",
                  "/RELAUNCH=1"]

ProgressCallback = Callable[[str, int, Optional[int]], None]


class UpdateError(Exception):
    """A download that failed, or one that didn't match its published hash."""


@dataclass
class Release:
    tag: str
    version: Tuple[int, ...]
    notes: str
    page_url: str
    setup_url: Optional[str] = None
    setup_name: Optional[str] = None
    sums_url: Optional[str] = None

    @property
    def label(self) -> str:
        """'1.7.1-rc7.1': the tag as people say it."""
        return self.tag[1:] if self.tag.startswith("v") else self.tag


@dataclass
class UpdateCheck:
    status: str                         # "newer", "current" or "unknown" (couldn't ask)
    message: str
    release: Optional[Release] = None


def parse_version(text: str) -> Optional[Tuple[int, ...]]:
    """'v1.7.1-rc7.1' or '1.7.1' -> (1, 7, 1); None if it isn't a version."""
    base = text.strip().lstrip("vV").split("-", 1)[0]
    try:
        parts = tuple(int(part) for part in base.split("."))
    except ValueError:
        return None
    return parts if parts else None


def _release_from(record: Dict) -> Optional[Release]:
    if record.get("draft"):
        return None
    tag = record.get("tag_name") or ""
    version = parse_version(tag)
    if version is None:
        return None
    release = Release(tag, version, (record.get("body") or "").strip(), record.get("html_url") or "")
    for asset in record.get("assets") or []:
        name, url = asset.get("name") or "", asset.get("browser_download_url")
        if name.endswith(SETUP_SUFFIX):
            release.setup_url, release.setup_name = url, name
        elif name == SUMS_NAME:
            release.sums_url = url
    return release


def newest_release(records: List[Dict]) -> Optional[Release]:
    """The highest version among published releases (drafts and odd tags left out)."""
    releases = [r for r in (_release_from(record) for record in records) if r]
    return max(releases, key=lambda r: r.version) if releases else None


def check_for_update(current: str = __version__, timeout: float = 15) -> UpdateCheck:
    """Asks GitHub for the release list and compares the newest with the running version."""
    try:
        response = requests.get(RELEASES_URL, headers=HEADERS, timeout=timeout)
        response.raise_for_status()
        records = response.json()
        if not isinstance(records, list):
            raise ValueError("the release list isn't a list")
    except (requests.exceptions.RequestException, ValueError) as e:
        return UpdateCheck("unknown", f"Couldn't reach GitHub to check for updates: {e}")
    release = newest_release(records)
    mine = parse_version(current) or ()
    if release is None or release.version <= mine:
        return UpdateCheck("current", f"You have the latest version ({current}).", release)
    return UpdateCheck("newer", f"Version {release.label} is available. You have {current}.", release)


def install_kind() -> str:
    """'installed' (the setup.exe's copy: it has an uninstaller), 'portable' (the zip) or 'source'."""
    if not getattr(sys, "frozen", False):
        return "source"
    return "installed" if any(paths.PROJECT_ROOT.glob("unins*.exe")) else "portable"


def expected_hash(sums_text: str, file_name: str) -> Optional[str]:
    """The SHA256 SHA256SUMS.txt gives for file_name ('<hash>  <name>' lines), lower case."""
    for line in sums_text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("*") == file_name:
            return parts[0].lower()
    return None


def download_installer(release: Release, progress: Optional[ProgressCallback] = None,
                       target_dir: Optional[Path] = None) -> Path:
    """
    Downloads the release's setup.exe into a new temporary folder (or target_dir) and checks
    it against SHA256SUMS.txt. Returns its path; raises UpdateError if anything's wrong.
    """
    report = progress or (lambda stage, done, total: None)
    if not (release.setup_url and release.setup_name and release.sums_url):
        raise UpdateError(f"Release {release.label} has no installer or no SHA256SUMS.txt.")
    try:
        sums = requests.get(release.sums_url, headers=HEADERS, timeout=30)
        sums.raise_for_status()
        want = expected_hash(sums.text, release.setup_name)
        if not want:
            raise UpdateError(f"SHA256SUMS.txt doesn't list {release.setup_name}.")

        folder = Path(target_dir) if target_dir else Path(tempfile.mkdtemp(prefix="EveFleetUpdate-"))
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / release.setup_name
        response = requests.get(release.setup_url, headers={**HEADERS, "Accept": "application/octet-stream"},
                                stream=True, timeout=30)
        response.raise_for_status()
        try:
            total = int(response.headers.get("Content-Length")) or None
        except (TypeError, ValueError):
            total = None
        digest, done = hashlib.sha256(), 0
        report("download", 0, total)
        with open(path, "wb") as f:
            for chunk in response.iter_content(chunk_size=CHUNK):
                if chunk:
                    f.write(chunk)
                    digest.update(chunk)
                    done += len(chunk)
                    report("download", done, total)
    except requests.exceptions.RequestException as e:
        raise UpdateError(f"The download failed: {e}") from e
    except OSError as e:
        raise UpdateError(f"The installer couldn't be saved: {e}") from e

    got = digest.hexdigest()
    if got != want:
        try:
            path.unlink()
        except OSError:
            pass
        raise UpdateError(f"The download doesn't match its published SHA256, so it wasn't run.\n\n"
                          f"Expected {want}\nGot {got}")
    logger.info("Downloaded %s (SHA256 %s)", path, got)
    return path


def launch_installer(path: Path) -> None:
    """Starts the installer on its own; the caller closes the app straight after."""
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen([str(path), *INSTALLER_ARGS], creationflags=flags, close_fds=True)


# --- the startup check's settings ------------------------------------------------------------

def _read(config_dir: Path) -> Dict:
    try:
        settings = json.loads((Path(config_dir) / SETTINGS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return settings if isinstance(settings, dict) else {}


def _write(config_dir: Path, key: str, value) -> None:
    path = Path(config_dir) / SETTINGS_FILE
    settings = _read(config_dir)
    settings[key] = value
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2), encoding="utf-8")


def check_at_startup(config_dir: Optional[Path] = None) -> bool:
    return bool(_read(config_dir or paths.CONFIG_DIR).get(AT_STARTUP_KEY, True))


def set_check_at_startup(on: bool, config_dir: Optional[Path] = None) -> None:
    _write(config_dir or paths.CONFIG_DIR, AT_STARTUP_KEY, bool(on))


def startup_check_due(config_dir: Optional[Path] = None, now: Optional[datetime] = None) -> bool:
    """Switched on, and the last startup check was a day or more ago (or never)."""
    config_dir = config_dir or paths.CONFIG_DIR
    if not check_at_startup(config_dir):
        return False
    try:
        last = datetime.fromisoformat(_read(config_dir).get(LAST_CHECK_KEY) or "")
    except ValueError:
        return True
    return (now or datetime.now()) - last >= CHECK_EVERY


def record_startup_check(config_dir: Optional[Path] = None, now: Optional[datetime] = None) -> None:
    _write(config_dir or paths.CONFIG_DIR, LAST_CHECK_KEY, (now or datetime.now()).isoformat(timespec="seconds"))
