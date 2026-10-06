"""
The highest shared role and doctrine UIDs ever handed out, so deleting the newest
shared record never lets its UID be reused. Packages identify records by UID: a
reused one would make a recipient's next update replace an unrelated record.

Fittings keep their mark inside fittings.json (step 2.6b). roles.json and
doctrines.json are bare lists, so their marks live in this small file beside them.
Local records never travel, so only the shared ranges have a mark.
"""
import json
import logging
from pathlib import Path
from typing import Dict

logger = logging.getLogger(__name__)

FILE_NAME = "uid_high_water.json"


class HighWaterMarks:
    """One mark per kind ("roles", "doctrines"). The file is read on every call because two managers share it."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def _read(self) -> Dict[str, int]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            logger.error(f"Failed to read {self.path.name}: {e}")
            return {}
        return {k: v for k, v in data.items() if isinstance(v, int)} if isinstance(data, dict) else {}

    def get(self, kind: str, default: int) -> int:
        """The highest UID of this kind ever handed out, or default before the first."""
        return self._read().get(kind, default)

    def raise_to(self, kind: str, uid: int) -> None:
        """Records uid as handed out, if it's above the current mark."""
        marks = self._read()
        if uid <= marks.get(kind, uid - 1):
            return
        marks[kind] = uid
        try:
            self.path.write_text(json.dumps(marks, indent=4), encoding="utf-8")
        except OSError as e:
            logger.error(f"Failed to save {self.path.name}: {e}")
