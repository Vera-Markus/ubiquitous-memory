"""
Each character's skills, pulled with the assets for the skill check (ESI features plan 26.3).

    GET /characters/{id}/skills/       trained skills, with the active level   esi-skills.read_skills.v1
    GET /characters/{id}/skillqueue/   what's training, and when it finishes   esi-skills.read_skillqueue.v1

Saved as ESI sends it in data/skills/<id>.json. /skills can be behind for a character who
hasn't logged in since a skill finished, so the levels used are the skills with any queue
entry whose finish date has passed laid on top (CCP's description of the route).

A login without both scopes is skipped without asking ESI: the pull says to log in again.
"""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

from app import paths
from app.esi_service import scopes as scope_ledger
from app.esi_service.base_interfaces import ESIRequest

logger = logging.getLogger("MultiCharAssetPull")       # the pull's logger, so lines reach the GUI log

OK, NO_SCOPE, FAILED = "ok", "no scope", "failed"


async def pull_skills_for_character(esi_client, char_id: str, name: str = "",
                                    skills_dir: Optional[Path] = None, scopes: Optional[set] = None) -> str:
    """Pulls and saves one character's skills and skill queue. Returns OK, NO_SCOPE or FAILED."""
    who = name or char_id
    if scopes is not None and scope_ledger.missing("skills", scopes):
        logger.warning(f"{who}'s login doesn't include skills yet; log in again "
                       "(Characters ▸ Add Character) for the skill check.")
        return NO_SCOPE
    skills = await esi_client.request(ESIRequest(url=f"/characters/{char_id}/skills/"))
    queue = await esi_client.request(ESIRequest(url=f"/characters/{char_id}/skillqueue/"))
    if any(r.status_code in (401, 403) and "scope" in str(r.data) for r in (skills, queue)):
        logger.warning(f"{who}'s login doesn't include skills yet; log in again "
                       "(Characters ▸ Add Character) for the skill check.")
        return NO_SCOPE
    if skills.status_code != 200 or queue.status_code != 200:
        logger.warning(f"Couldn't pull {who}'s skills (HTTP {skills.status_code}/{queue.status_code}); "
                       "the assets are unaffected.")
        return FAILED
    data = skills.data or {}
    record = {
        "character_id": int(char_id),
        "pulled_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "total_sp": data.get("total_sp"),
        "skills": data.get("skills", []),
        "queue": list(queue.data or []),
    }
    folder = Path(skills_dir or paths.SKILLS_DIR)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{char_id}.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(record, indent=2), encoding="utf-8")
    tmp.replace(path)
    logger.info(f"{who}: {len(record['skills'])} skill(s) saved.")
    return OK


def load_levels(char_id, skills_dir: Optional[Path] = None, now: Optional[datetime] = None) -> Optional[Dict[int, int]]:
    """
    {skill type ID: level} for the skill check, or None when the character's skills
    haven't been pulled. The active level (lower for an Alpha clone or with an expert
    system), raised by queue entries that have finished since.
    """
    path = Path(skills_dir or paths.SKILLS_DIR) / f"{char_id}.json"
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return levels(record, now)


def levels(record: dict, now: Optional[datetime] = None) -> Dict[int, int]:
    now = now or datetime.now(timezone.utc)
    result = {int(s["skill_id"]): int(s.get("active_skill_level", s.get("trained_skill_level", 0)))
              for s in record.get("skills", []) if "skill_id" in s}
    for entry in record.get("queue", []):
        finish = entry.get("finish_date")
        if not finish:
            continue
        try:
            done = datetime.fromisoformat(finish.replace("Z", "+00:00")) <= now
        except ValueError:
            continue
        if done:
            skill = int(entry["skill_id"])
            result[skill] = max(result.get(skill, 0), int(entry.get("finished_level", 0)))
    return result
