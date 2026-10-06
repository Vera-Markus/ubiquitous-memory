"""
When ESI's cached copy of each character's assets expires (RC2 §1, G5).

ESI caches an asset list for an hour. A pull before then gets the same data as the
last one; the pull still runs (the 15-minute Pull All cooldown already stops spam),
but it says so. Saved in data/config/esi_cache.json: {character_id: expires (ISO, UTC)}.
"""
import json
import logging
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Optional

from app.paths import CONFIG_DIR

logger = logging.getLogger(__name__)

ESI_CACHE_PATH = CONFIG_DIR / "esi_cache.json"


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, ValueError) as e:
        logger.warning(f"Could not read {path.name}: {e}")
        return {}


def parse_expires(header: Optional[str]) -> Optional[datetime]:
    """ESI's Expires header (an HTTP date) as an aware UTC datetime, or None."""
    if not header:
        return None
    try:
        return parsedate_to_datetime(header).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def record_expires(character_id: str, expires_header: Optional[str], path: Optional[Path] = None) -> None:
    """Saves when ESI's copy of this character's assets expires."""
    path = path or ESI_CACHE_PATH
    expires = parse_expires(expires_header)
    if expires is None:
        return
    state = _load(path)
    state[str(character_id)] = expires.isoformat()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except OSError as e:
        logger.warning(f"Could not save {path.name}: {e}")


def cached_until(character_id: str, path: Optional[Path] = None,
                 now: Optional[datetime] = None) -> Optional[datetime]:
    """When ESI's copy expires, if that's still in the future; otherwise None."""
    path = path or ESI_CACHE_PATH
    value = _load(path).get(str(character_id))
    try:
        expires = datetime.fromisoformat(value) if value else None
    except ValueError:
        return None
    now = now or datetime.now(timezone.utc)
    return expires if expires and expires > now else None
