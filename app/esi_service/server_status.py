"""
Tranquility's status, checked before the app calls CCP (ESI features plan, Phase 25).

Two sources:
- ESI's GET /status: players online, server version, start time, VIP mode. It's ESI
  itself, so when it doesn't answer, ESI is down for the app too.
- CCP's status page (status.eveonline.com, Atlassian Statuspage): the state of each
  component (Tranquility, ESI, Login…), open incidents and scheduled maintenance.

ServerStatus turns the latest readings into one of four states (D11.4) and answers
"may the app call CCP now?" (D11.1, D11.9). The daily downtime window, 10:55–11:15 UTC,
blocks without calling; a good check from 11:10 lifts it early (D11.5).

The fetch functions are module attributes so the GUI harnesses can replace them.
"""
import threading
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import httpx

from app.esi_service.esi_settings import ESI_BASE_URL, USER_AGENT, esi_headers

STATUS_PAGE = "https://status.eveonline.com"
STATUS_PAGE_SUMMARY = STATUS_PAGE + "/api/v2/summary.json"
TIMEOUT = 4.0                   # seconds; a manual action waits for the check

ONLINE, DEGRADED, OFFLINE, UNKNOWN = "online", "degraded", "offline", "unknown"

WINDOW_START = time(10, 55)     # daily downtime, UTC (D11.5)
WINDOW_LIFT = time(11, 10)      # a good check from here ends the window early
WINDOW_END = time(11, 15)

FRESH = timedelta(seconds=30)           # /status is cached 30 s by ESI: no point asking sooner
ONLINE_EVERY = timedelta(hours=1)       # the icon's check while all is well (D11.16)
OUTAGE_EVERY = timedelta(minutes=5)     # while down (D11.3, D11.16)
DOT_LASTS = timedelta(hours=1)          # the red dot clears itself after an hour (D11.17)

# Status page component states that mean "not working" and "completely down".
_TROUBLE = {"degraded_performance", "partial_outage", "major_outage", "under_maintenance"}
_DOWN = {"major_outage"}


@dataclass
class StatusReading:
    """ESI's /status: ok means it answered 200."""
    ok: bool
    vip: bool = False
    players: Optional[int] = None
    start_time: Optional[str] = None
    unreachable: bool = False       # no answer at all (connection failed), not an error status
    error: str = ""


@dataclass
class Message:
    """An open incident or scheduled maintenance on the status page."""
    id: str
    kind: str                       # "incident" or "maintenance"
    title: str
    status: str
    updated_at: str
    body: str = ""
    scheduled_for: str = ""

    @property
    def key(self) -> str:
        return f"{self.id}@{self.updated_at}"       # a new update counts as a new message


@dataclass
class PageReading:
    """The status page's summary: ok means it was read."""
    ok: bool
    components: Dict[str, str] = field(default_factory=dict)
    messages: List[Message] = field(default_factory=list)
    error: str = ""

    def component(self, name: str) -> str:
        """A component's status ('operational', 'major_outage', …), '' if not listed."""
        for key, value in self.components.items():
            if key.lower() == name.lower():
                return value
        return ""

    @property
    def has_error(self) -> bool:
        """Something is wrong: an open incident, or Tranquility or ESI not operational."""
        return bool([m for m in self.messages if m.kind == "incident"]) or \
            self.component("Tranquility") in _TROUBLE or self.component("EVE Swagger Interface (ESI)") in _TROUBLE


def fetch_status() -> StatusReading:
    """GET /status from ESI (no login needed)."""
    try:
        r = httpx.get(ESI_BASE_URL + "/status", headers=esi_headers(), timeout=TIMEOUT)
    except httpx.HTTPError as e:
        return StatusReading(False, unreachable=isinstance(e, (httpx.ConnectError, httpx.ConnectTimeout)),
                             error=str(e) or type(e).__name__)
    if r.status_code != 200:
        return StatusReading(False, error=f"HTTP {r.status_code}")
    try:
        body = r.json()
    except ValueError:
        return StatusReading(False, error="unreadable reply")
    return StatusReading(True, vip=bool(body.get("vip")), players=body.get("players"),
                         start_time=body.get("start_time"))


def read_page(body: dict) -> PageReading:
    """The status page's summary.json as a PageReading (separate for the tests)."""
    components = {c.get("name", ""): c.get("status", "") for c in body.get("components", []) if c.get("name")}
    messages = []
    for kind, key in (("incident", "incidents"), ("maintenance", "scheduled_maintenances")):
        for m in body.get(key, []):
            updates = m.get("incident_updates") or []
            messages.append(Message(str(m.get("id", "")), kind, m.get("name", ""), m.get("status", ""),
                                    m.get("updated_at", ""), (updates[0].get("body", "") if updates else ""),
                                    m.get("scheduled_for", "") or ""))
    return PageReading(True, components, messages)


def fetch_page() -> PageReading:
    """CCP's status page summary: components, open incidents, scheduled maintenance."""
    try:
        r = httpx.get(STATUS_PAGE_SUMMARY, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        r.raise_for_status()
        return read_page(r.json())
    except (httpx.HTTPError, ValueError) as e:
        return PageReading(False, error=str(e) or type(e).__name__)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def in_daily_window(now: datetime) -> bool:
    """10:55–11:15 UTC (D11.5)."""
    t = now.astimezone(timezone.utc).time()
    return WINDOW_START <= t < WINDOW_END


class ServerStatus:
    """
    The latest readings and what they mean. Thread-safe: the icon's checks run on a
    worker thread, the GUI reads the state on the Tk thread.
    """

    def __init__(self):
        self._lock = threading.RLock()
        self.reading: Optional[StatusReading] = None
        self.page: Optional[PageReading] = None
        self.checked_at: Optional[datetime] = None
        self.page_read_at: Optional[datetime] = None
        self._lifted_on = None          # the date a good check ended the daily window early
        self._raised: set = set()       # message keys that have raised the dot once
        self._dot_since: Optional[datetime] = None
        self.held = False               # safe mode found a problem: pulls wait for a good check (D11.8)

    # --- checking ------------------------------------------------------------------------------

    def check(self, now: Optional[datetime] = None, page: Optional[bool] = None) -> None:
        """
        Reads /status now, and the status page when asked, when /status fails (to say
        why), or when it's due. Blocking: call it from a worker thread, or for a
        manual action (a few seconds at worst).
        """
        import app.esi_service.server_status as module         # the harnesses replace the fetchers
        now = now or utc_now()
        reading = module.fetch_status()
        if page is None:
            page = not reading.ok or self.held or self.page_due(now)
        page_reading = module.fetch_page() if page else None
        self.apply(reading, page_reading, now)

    def apply(self, reading: StatusReading, page: Optional[PageReading], now: datetime) -> None:
        with self._lock:
            self.reading = reading
            self.checked_at = now
            if page is not None:
                self.page = page
                self.page_read_at = now
                self._note_messages(page, now)
            if reading.ok and not reading.vip and in_daily_window(now) and \
                    now.astimezone(timezone.utc).time() >= WINDOW_LIFT:
                self._lifted_on = now.astimezone(timezone.utc).date()
            if self.held and reading.ok and self.page is not None and self.page.ok and not self.page.has_error:
                self.held = False           # Tranquility answers and the status page is clear again

    def apply_page(self, page: PageReading, now: Optional[datetime] = None) -> None:
        """A status page reading taken on its own (safe mode reads it every 30 s)."""
        now = now or utc_now()
        with self._lock:
            self.page = page
            self.page_read_at = now
            self._note_messages(page, now)

    def hold(self) -> None:
        """Safe mode saw a problem on the status page: hold pulls until a good check (D11.8)."""
        with self._lock:
            self.held = True

    def ensure_fresh(self, now: Optional[datetime] = None) -> None:
        """Checks unless the last check is under 30 s old (D11.1: check before each manual action)."""
        now = now or utc_now()
        with self._lock:
            fresh = self.checked_at is not None and now - self.checked_at < FRESH
        if not fresh:
            self.check(now)

    def status_due(self, now: datetime) -> bool:
        """The icon's own schedule (D11.16): hourly when up, every 5 minutes when not."""
        with self._lock:
            if self.checked_at is None:
                return True
            since = now - self.checked_at
            day = now.astimezone(timezone.utc).date()
            lift = datetime.combine(day, WINDOW_LIFT, timezone.utc)
            end = datetime.combine(day, WINDOW_END, timezone.utc)
            if self.window_blocks(now):
                # In the window: nothing before 11:10, then every 5 minutes until one lifts it.
                return now >= lift and (self.checked_at < lift or since >= OUTAGE_EVERY)
            if self.locked(now):
                return since >= OUTAGE_EVERY
            if self.checked_at < end <= now:
                return True                 # the first check after the window
            return since >= ONLINE_EVERY

    def page_due(self, now: datetime) -> bool:
        with self._lock:
            if self.page_read_at is None:
                return True
            every = OUTAGE_EVERY if self.held or self.state(now) in (OFFLINE, UNKNOWN) else ONLINE_EVERY
            return now - self.page_read_at >= every

    # --- what it means -------------------------------------------------------------------------

    def window_blocks(self, now: datetime) -> bool:
        with self._lock:
            return in_daily_window(now) and self._lifted_on != now.astimezone(timezone.utc).date()

    def state(self, now: Optional[datetime] = None) -> str:
        """online, degraded, offline or unknown (D11.4)."""
        now = now or utc_now()
        with self._lock:
            r, page = self.reading, self.page
            if r is None:
                return UNKNOWN
            if not r.ok:
                # No answer at all and no status page either: probably no internet.
                if r.unreachable and (page is None or not page.ok):
                    return UNKNOWN
                return OFFLINE
            if r.vip:
                return DEGRADED
            if page is not None and page.ok and page.component("EVE Swagger Interface (ESI)") in _TROUBLE:
                return DEGRADED
            return ONLINE

    def shown_state(self, now: Optional[datetime] = None) -> str:
        """The icon's colour: the daily window shows as offline."""
        now = now or utc_now()
        with self._lock:
            return OFFLINE if self.window_blocks(now) else self.state(now)

    def locked(self, now: Optional[datetime] = None) -> bool:
        """Outbound calls to CCP are off (D11.9, D11.14): down, unknown, VIP, ESI completely down, or the daily window."""
        now = now or utc_now()
        with self._lock:
            if self.window_blocks(now):
                return True
            if self.reading is None:
                return False            # not checked yet: each action checks before it calls
            if self.held:
                return True
            state = self.state(now)
            if state in (OFFLINE, UNKNOWN):
                return True
            if state == DEGRADED and self.reading.vip:
                return True
            return bool(self.page and self.page.ok and self.page.component("EVE Swagger Interface (ESI)") in _DOWN)

    def reason(self, now: Optional[datetime] = None) -> str:
        """Why calls are off, or what's wrong, in one sentence ('' when all is well)."""
        now = now or utc_now()
        with self._lock:
            if self.window_blocks(now):
                return "Tranquility's daily downtime (10:55–11:15 UTC). Calls to CCP resume after it."
            if self.reading is None:
                return ""
            state = self.state(now)
            incident = self.latest_incident()
            if self.held and state not in (OFFLINE, UNKNOWN):
                problem = f" ({incident.title})" if incident else ""
                return f"CCP's status page reports a problem{problem}: pulls are on hold until it's resolved."
            if state == UNKNOWN:
                return "CCP's servers can't be reached. Check your internet connection."
            if state == OFFLINE:
                if incident:
                    return f"Tranquility is down: {incident.title}."
                return "Tranquility isn't answering."
            if self.reading.vip:
                return "Tranquility is in VIP mode: only CCP staff can log in."
            if self.page and self.page.ok and self.page.component("EVE Swagger Interface (ESI)") in _DOWN:
                return "CCP reports ESI is down."
            if state == DEGRADED:
                return "CCP reports ESI is degraded: some calls may fail."
            return ""

    def allow(self, action: str = "", login: bool = False, now: Optional[datetime] = None) -> Tuple[bool, str]:
        """
        May the app call CCP now? Returns (allowed, message). With login=True (Add
        Character) the status page's Login component must be working too (C11.1).
        """
        now = now or utc_now()
        with self._lock:
            if self.locked(now):
                return False, self.reason(now)
            if login and self.page and self.page.ok and self.page.component("Login") in _DOWN:
                return False, "CCP reports the login server is down."
            return True, self.reason(now)

    def latest_incident(self) -> Optional[Message]:
        with self._lock:
            if not self.page or not self.page.ok:
                return None
            incidents = [m for m in self.page.messages if m.kind == "incident"]
            return max(incidents, key=lambda m: m.updated_at) if incidents else None

    def summary(self, now: Optional[datetime] = None) -> List[str]:
        """Lines for the icon's tooltip."""
        now = now or utc_now()
        with self._lock:
            state = self.shown_state(now)
            lines = [{ONLINE: "Tranquility: online", DEGRADED: "Tranquility: online, with problems",
                      OFFLINE: "Tranquility: offline", UNKNOWN: "Tranquility: unknown"}[state]]
            reason = self.reason(now)
            if reason:
                lines.append(reason)
            r = self.reading
            if r and r.ok:
                if r.players is not None:
                    lines.append(f"{r.players:,} players online")
                if r.start_time:
                    lines.append(f"Server started {r.start_time.replace('T', ' ').rstrip('Z')} UTC")
            if self.page and self.page.ok:
                esi = self.page.component("EVE Swagger Interface (ESI)")
                if esi:
                    lines.append(f"ESI: {esi.replace('_', ' ')}")
                for m in self.page.messages:
                    if m.title and m.title in reason:
                        continue            # the reason above already names it
                    when = f" ({m.scheduled_for.replace('T', ' ')[:16]} UTC)" if m.scheduled_for else ""
                    lines.append(f"{'Incident' if m.kind == 'incident' else 'Maintenance'}: {m.title}{when}")
            if self.checked_at:
                lines.append(f"Checked {self.checked_at.astimezone().strftime('%H:%M')}")
            lines.append("Click to open CCP's status page.")
            return lines

    # --- the red dot (D11.7, D11.17) -----------------------------------------------------------

    def _note_messages(self, page: PageReading, now: datetime) -> None:
        """A message (or an update to one) not raised before raises the dot; none left clears it."""
        if not page.ok:
            return
        keys = {m.key for m in page.messages}
        if not keys:
            self._dot_since = None          # resolved: nothing left to tell
        elif keys - self._raised:
            self._dot_since = now
            self._raised |= keys

    def dot(self, now: Optional[datetime] = None) -> bool:
        now = now or utc_now()
        with self._lock:
            return self._dot_since is not None and now - self._dot_since < DOT_LASTS

    def clear_dot(self) -> None:
        """The user clicked the icon."""
        with self._lock:
            self._dot_since = None
