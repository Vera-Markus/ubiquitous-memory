"""
Which records each installed doctrine package put in the library (design §11.4).

Stored in data/generated/installed_packages.json; this class is its only writer.

    { "snuff-public": { "package_id": "snuff-public", "package_name": "SNUFF Public",
                        "exported_at": "...", "imported_at": "...", "doctrine_manager_approved": true,
                        "fit_uids": [1000, ...], "role_uids": [1000, ...], "doctrine_uids": [5000] } }

A record in the doctrine ranges that no entry lists was made by hand, and an
import carrying the same UID is refused until it's deleted.
"""
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Set

KINDS = ("fit", "role", "doctrine")

# The protected ranges (design §11.2): only these are ever exported or touched by an import.
PROTECTED = {"fit": (1000, 1999), "role": (1000, 1999), "doctrine": (5000, 5999)}


def in_protected_range(kind: str, uid: Any) -> bool:
    low, high = PROTECTED[kind]
    return isinstance(uid, int) and not isinstance(uid, bool) and low <= uid <= high


def slugify(name: str) -> str:
    """"SNUFF Public" -> "snuff-public": a package ID that's stable across exports of the same name."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")
    return slug or "package"


class PackageRegistry:
    def __init__(self, path):
        self.path = Path(path)
        self.entries: Dict[str, Dict[str, Any]] = {}
        self.load()

    # --- file ------------------------------------------------------------------------------

    def exists(self) -> bool:
        return self.path.exists()

    def load(self) -> None:
        if not self.path.exists():
            self.entries = {}
            return
        with self.path.open("r", encoding="utf-8") as file:
            data = json.load(file)
        self.entries = data if isinstance(data, dict) else {}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as file:
            json.dump(self.entries, file, indent=2)

    # --- reading ---------------------------------------------------------------------------

    def uids(self, key: str, kind: str) -> Set[int]:
        return set((self.entries.get(key) or {}).get(f"{kind}_uids", []))

    def installed_by(self, kind: str, uid: int) -> List[str]:
        """Keys of every installed package that contains this record."""
        return sorted(key for key, entry in self.entries.items() if uid in entry.get(f"{kind}_uids", []))

    # --- writing (callers save) ------------------------------------------------------------

    def record(self, key: str, package_id: str, package_name: str, exported_at: str,
               uids: Dict[str, Iterable[int]], approved: bool = False) -> None:
        self.entries[key] = {
            "package_id": package_id,
            "package_name": package_name,
            "exported_at": exported_at,
            "doctrine_manager_approved": approved,
            "imported_at": datetime.now().isoformat(timespec="seconds"),
            **{f"{kind}_uids": sorted(set(uids.get(kind, ()))) for kind in KINDS},
        }
