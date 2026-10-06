"""
Characters ▸ Remove Character (UI rework step 5.4): fully purge one character.

    plan = service.plan(char_id)      # what will go, for the confirmation
    service.remove(char_id)           # login and tokens, asset data, assignments

Asset data is the character's raw pull file (raw/<id>.json) and its entries in
generated/all_assets.json, which is every raw file merged with a character_id on
each entry, so dropping those entries matches what the next pull would build.
Their clones and implants (clones/<id>.json) go too, and any corporation hangars
they pulled (corp/<corporation>.json): the next pull uses another Director if there is one.
The shared location cache stays: other characters' assets use the same places.
"""
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional, Tuple

ALL_ASSETS = "all_assets.json"


@dataclass
class RemovalPlan:
    character_id: str
    name: str
    has_login: bool
    asset_file: bool                                       # raw/<id>.json exists
    merged_assets: int                                     # entries in all_assets.json
    assignments: List[Tuple[str, str]] = field(default_factory=list)   # (doctrine name, role name)

    def summary(self) -> str:
        lines = [f"Remove {self.name} ({self.character_id})? This deletes:"]
        lines.append("- their login and tokens" if self.has_login else "- (no saved login)")
        if self.asset_file or self.merged_assets:
            lines.append(f"- their asset data ({self.merged_assets} item(s) from the last pull)")
        else:
            lines.append("- (no asset data)")
        if self.assignments:
            lines.append(f"- their {len(self.assignments)} role assignment(s):")
            lines.extend(f"    {doctrine} → {role}" for doctrine, role in self.assignments)
        else:
            lines.append("- (no role assignments)")
        lines.append("\nThe roles and doctrines themselves stay. This can't be undone; "
                     "add the character again to log back in.")
        return "\n".join(lines)


class CharacterRemovalService:
    def __init__(self, auth_service: Any, doctrine_manager: Any, role_manager: Any,
                 raw_dir: Path, generated_dir: Path, clones_dir: Optional[Path] = None,
                 corp_dir: Optional[Path] = None):
        self.auth = auth_service
        self.doctrines = doctrine_manager
        self.roles = role_manager
        self.raw_dir = Path(raw_dir)
        self.generated_dir = Path(generated_dir)
        self.clones_dir = Path(clones_dir or self.raw_dir.parent / "clones")
        self.corp_dir = Path(corp_dir or self.raw_dir.parent / "corp")

    def _raw_file(self, char_id: str) -> Path:
        return self.raw_dir / f"{char_id}.json"

    def _load_merged(self) -> List[dict]:
        path = self.generated_dir / ALL_ASSETS
        if not path.exists():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []

    def plan(self, char_id: str) -> RemovalPlan:
        char_id = str(char_id)
        assignments = []
        for doctrine, role_uid in self.doctrines.assignments_for_character(char_id):
            role = self.roles.get_role(role_uid)
            assignments.append((doctrine['doctrine_name'], role['role_name'] if role else f"role {role_uid}"))
        merged = sum(1 for a in self._load_merged() if str(a.get('character_id')) == char_id)
        return RemovalPlan(
            character_id=char_id,
            name=self.auth.index.get(char_id, f"Character {char_id}"),
            has_login=char_id in self.auth.profiles or char_id in self.auth.index,
            asset_file=self._raw_file(char_id).exists(),
            merged_assets=merged,
            assignments=sorted(assignments),
        )

    def remove(self, char_id: str) -> RemovalPlan:
        """Removes everything plan() lists. Returns that plan, as it was before removal."""
        char_id = str(char_id)
        plan = self.plan(char_id)

        # Assignments first: if a later step fails, the character at least no longer counts in audits.
        self.doctrines.remove_character_everywhere(char_id)

        raw = self._raw_file(char_id)
        if raw.exists():
            raw.unlink()
        clones = self.clones_dir / f"{char_id}.json"
        if clones.exists():
            clones.unlink()
        for corp in self.corp_dir.glob("*.json") if self.corp_dir.exists() else []:
            try:
                pulled_by = str(json.loads(corp.read_text(encoding="utf-8")).get("pulled_by"))
            except (OSError, ValueError):
                continue
            if pulled_by == char_id:
                corp.unlink()
        if plan.merged_assets:
            kept = [a for a in self._load_merged() if str(a.get('character_id')) != char_id]
            path = self.generated_dir / ALL_ASSETS
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(kept, indent=4), encoding="utf-8")
            tmp.replace(path)

        self.auth.remove_profile(char_id)
        return plan
