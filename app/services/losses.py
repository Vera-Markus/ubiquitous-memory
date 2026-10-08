"""
Losses (ESI features plan 30.2-30.4; D7, D8.10): which assigned ship a killmail was.

A new loss's candidates are the ships of every linked character (Q30.1: any of them may have flown
another's ship), and with corporation losses on, the corporations' ships:
- of the lost hull (the fit isn't compared: modules get swapped, ammo gets used);
- with an assigned fitting that a requirement uses, in the ship's system (D7.7), never <Personal>
  ships (D7.4);
- seen in an earlier pull, before the loss, and not in the latest one.

The app never decides: every candidate is "Possibly lost" until the user says which it was (asked
after the pull, or right-click ▸ This One Was Lost, D7.5) or that it wasn't (It Wasn't This One).
None: ignored. Each killmail is matched once, when it's first seen, and remembered in
data/config/losses.json. A loss shows until its requirement next passes or gets a soft fail (a
replacement is there or on its way, D7.3), or the ship's assignment is forgotten after 30 days.
"""
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Set

from app import paths

LOSSES_FILE = "losses.json"
TITAN, SUPERCARRIER = 30, 659       # groups: no contracts, condolences instead (D8.10)


def _time(text: Optional[str]) -> Optional[datetime]:
    if not text:
        return None
    try:
        when = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


class LossBook:
    def __init__(self, config_dir: Optional[Path] = None):
        self.path = Path(config_dir or paths.CONFIG_DIR) / LOSSES_FILE
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        self.records: Dict[str, dict] = data if isinstance(data, dict) else {}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.records, indent=1), encoding="utf-8")

    # --- matching (30.2) ---------------------------------------------------------------------------

    def match_new(self, killmails: Dict[int, dict], designations: Dict[int, dict], seen_now: Set[int],
                  victims: Iterable[int], corporations: Iterable[int] = (),
                  tied: Callable[[dict], bool] = lambda d: True) -> List[dict]:
        """
        Matches killmails not matched before. victims: linked characters; corporations: those whose
        losses are read (Directors, D7.6). Returns the new loss records (with candidates or not), none
        of them settled: that's the user's to say.
        """
        victims, corporations = {int(v) for v in victims}, {int(c) for c in corporations}
        owners = [{"kind": "character", "id": v} for v in victims] + \
                 [{"kind": "corporation", "id": c} for c in corporations]
        already = {r["lost"] for r in self.records.values() if r.get("lost") is not None}   # lost once already
        new = []
        for kid, km in sorted(killmails.items()):
            if str(kid) in self.records:
                continue
            victim = km.get("victim") or {}
            pilot, corp = int(victim.get("character_id") or 0), int(victim.get("corporation_id") or 0)
            if pilot not in victims and corp not in corporations:
                continue            # a kill, not a loss
            when = _time(km.get("killmail_time"))
            hull = int(victim.get("ship_type_id") or 0)
            # An unlinked corpmate's loss can only be a corporation ship, never one of a linked character's.
            allowed = owners if pilot in victims else [o for o in owners if o["kind"] == "corporation"]
            candidates = []
            for item_id, d in designations.items():
                if d.get("personal") or d.get("fit_uid") is None or int(d.get("type_id") or 0) != hull \
                        or int(item_id) in already:
                    continue
                owner = d.get("owner") or d.get("holder") or {}
                if {"kind": owner.get("kind"), "id": int(owner.get("id") or 0)} not in allowed:
                    continue
                seen = _time(d.get("last_seen"))
                if int(item_id) in seen_now or seen is None or (when is not None and seen > when):
                    continue        # still there, never seen, or seen after the loss: not this one
                if not tied(d):
                    continue
                candidates.append(int(item_id))
            record = {"killmail_id": kid, "time": km.get("killmail_time", ""), "ship_type_id": hull,
                      "system_id": km.get("solar_system_id"), "pilot": pilot, "corporation": corp,
                      "candidates": sorted(candidates), "lost": None, "ruled_out": [], "cleared": False}
            self.records[str(kid)] = record
            new.append(record)
        if new:
            self.save()
        return new

    # --- what's shown (30.3) -----------------------------------------------------------------------

    def info(self, item_id: int) -> Optional[dict]:
        """{"state": "lost" or "possibly", "date", "killmail_id"} for a ship, or None."""
        for record in self.records.values():
            if record.get("cleared") or int(item_id) not in record.get("candidates", []):
                continue
            if record.get("lost") == int(item_id):
                return {"state": "lost", "date": record["time"][:10], "killmail_id": record["killmail_id"]}
            if record.get("lost") is None:
                return {"state": "possibly", "date": record["time"][:10], "killmail_id": record["killmail_id"]}
        return None

    def settle(self, killmail_id: int, item_id: int) -> None:
        """This One Was Lost (D7.5)."""
        record = self.records.get(str(killmail_id))
        if record is not None and int(item_id) in record["candidates"]:
            record["lost"] = int(item_id)
            self.save()

    def rule_out(self, killmail_id: int, item_id: int) -> None:
        """It Wasn't This One: the ship stops being a candidate (and, if it was settled on, isn't lost)."""
        record = self.records.get(str(killmail_id))
        if record is not None and int(item_id) in record["candidates"]:
            record["candidates"].remove(int(item_id))
            record.setdefault("ruled_out", []).append(int(item_id))
            if record.get("lost") == int(item_id):
                record["lost"] = None
            self.save()

    def clear(self, item_ids: Iterable[int]) -> int:
        """D7.3: the requirement passed or soft-failed, so its losses stop showing."""
        ids, cleared = {int(i) for i in item_ids}, 0
        for record in self.records.values():
            if not record.get("cleared") and (record.get("lost") in ids or
                                              (record.get("lost") is None and ids & set(record["candidates"]))):
                record["cleared"] = True
                cleared += 1
        if cleared:
            self.save()
        return cleared


def is_super(group_id: Optional[int]) -> bool:
    return group_id in (TITAN, SUPERCARRIER)


def srp_text(killmail: dict, type_name: Callable[[int], str]) -> str:
    """SRP items (D7.6): the hull, then what was destroyed and what dropped, one line each."""
    victim = killmail.get("victim") or {}
    lines = [f"{type_name(int(victim.get('ship_type_id') or 0))} x1 (hull)"]
    for kind, key in (("destroyed", "quantity_destroyed"), ("dropped", "quantity_dropped")):
        totals: Dict[int, int] = {}
        for item in victim.get("items", []):
            if item.get(key):
                totals[int(item["item_type_id"])] = totals.get(int(item["item_type_id"]), 0) + int(item[key])
        lines += [f"{type_name(t)} x{q} ({kind})" for t, q in sorted(totals.items(), key=lambda kv: type_name(kv[0]))]
    return "\n".join(lines)
