"""
Open in the game client (ESI features plan 27.1): ask a character's running EVE client to
open a window or set a route.

    GET  /characters/{id}/online          is the character in game      esi-location.read_online.v1
    POST /ui/openwindow/marketdetails     the market for a type          esi-ui.open_window.v1
    POST /ui/autopilot/waypoint           set destination, add waypoint  esi-ui.write_waypoint.v1
    POST /ui/openwindow/contract          a contract                     esi-ui.open_window.v1
    POST /ui/openwindow/information       a character, corp or alliance  esi-ui.open_window.v1
    POST /ui/openwindow/newmail           a new mail, filled in          esi-ui.open_window.v1
    GET  /characters/{id}/location        where the character is         esi-location.read_location.v1

The client calls answer 204 whether or not anything happens: a character who is offline or
at character selection gets nothing, and nothing is queued (lab tests). So the app asks
/online first and sends nothing to a character who isn't in game. /online is cached up to
60 s by ESI, so a character who has just logged in can still read as offline for a minute.

The market call doesn't always show (lab tests, 2026-10-07): the Rifter opened the market
with no market window open, but Tritanium and a Devoter once did nothing, each with a 204.
So the chooser stays open after it, with Send Again.

Each call is made as one character, by ID, without touching AuthService's active
character: a pull switching between characters on another thread isn't disturbed.
"""
import logging
from dataclasses import dataclass
from typing import Any, Callable, Optional

from app.asset_handling.clone_pull import token_scopes
from app.esi_service.base_interfaces import ESIRequest
from app.esi_service.esi_settings import ESI_BASE_URL
from app.esi_service.real_esi_client import RealESIClient

logger = logging.getLogger(__name__)

ONLINE = "esi-location.read_online.v1"
OPEN_WINDOW = "esi-ui.open_window.v1"
WAYPOINT = "esi-ui.write_waypoint.v1"
LOCATION = "esi-location.read_location.v1"
LOG_IN_AGAIN = "log in again (Characters ▸ Add Character)"


@dataclass
class Sent:
    """What became of one call: ok, the line to show, and whether to keep the chooser open
    (sent, but it may need sending again: the market)."""
    ok: bool
    message: str
    keep_open: bool = False

    def __bool__(self) -> bool:
        return self.ok


class _AsCharacter:
    """AuthService narrowed to one character, for RealESIClient's token and refresh."""

    def __init__(self, auth_service: Any, char_id: str):
        self._auth = auth_service
        self._char_id = char_id

    def get_access_token(self) -> Optional[str]:
        return self._auth.get_access_token(self._char_id)

    async def refresh_access_token(self):
        return await self._auth.refresh_access_token(self._char_id)


class GameClient:
    def __init__(self, auth_service: Any, client_factory: Optional[Callable[[Any], Any]] = None):
        self._auth = auth_service
        self._factory = client_factory or (lambda auth: RealESIClient(ESI_BASE_URL, auth))

    def name(self, char_id) -> str:
        return self._auth.index.get(str(char_id), f"Character {char_id}")

    def _lacks(self, char_id, scope: str) -> bool:
        return scope not in token_scopes(self._auth.get_access_token(str(char_id)))

    async def _request(self, char_id, request: ESIRequest):
        client = self._factory(_AsCharacter(self._auth, str(char_id)))
        try:
            return await client.request(request)
        finally:
            close = getattr(client, "close", None)
            if close is not None:
                await close()

    async def online(self, char_id) -> Optional[bool]:
        """True or False, or None when it can't be told (no scope, or ESI didn't answer)."""
        if self._lacks(char_id, ONLINE):
            return None
        response = await self._request(char_id, ESIRequest(url=f"/characters/{char_id}/online"))
        if response.status_code != 200 or not isinstance(response.data, dict):
            logger.warning(f"Couldn't tell whether {self.name(char_id)} is in game (HTTP {response.status_code}).")
            return None
        return bool(response.data.get("online"))

    async def location(self, char_id) -> Optional[int]:
        """The character's solar system (the capital search's centre, plan 28.6), or None."""
        if self._lacks(char_id, LOCATION):
            return None
        response = await self._request(char_id, ESIRequest(url=f"/characters/{char_id}/location"))
        if response.status_code != 200 or not isinstance(response.data, dict):
            return None
        return response.data.get("solar_system_id")

    async def _send(self, char_id, scope: str, url: str, params: Optional[dict], done: str,
                    body: Any = None, note: str = "") -> Sent:
        who = self.name(char_id)
        if self._lacks(char_id, scope) or self._lacks(char_id, ONLINE):
            return Sent(False, f"{who}'s login doesn't include opening things in the game; {LOG_IN_AGAIN}.")
        is_online = await self.online(char_id)
        if is_online is None:
            return Sent(False, f"Couldn't tell whether {who} is in game, so nothing was sent.")
        if not is_online:
            return Sent(False, f"{who} isn't in game, so nothing was sent. (Just logged in? "
                               "ESI can take a minute to notice.)")
        response = await self._request(char_id, ESIRequest(url=url, method="POST", params=params, json=body))
        if response.status_code in (200, 204):
            return Sent(True, f"{done} in {who}'s client.{note}", keep_open=bool(note))
        return Sent(False, f"EVE didn't take it (HTTP {response.status_code}); nothing was opened for {who}.")

    async def show_market(self, char_id, type_id: int) -> Sent:
        return await self._send(char_id, OPEN_WINDOW, "/ui/openwindow/marketdetails",
                                {"type_id": int(type_id)}, "Sent to the market",
                                note=" If it didn't appear, Send Again.")

    async def set_destination(self, char_id, location_id: int) -> Sent:
        return await self._send(char_id, WAYPOINT, "/ui/autopilot/waypoint",
                                {"destination_id": int(location_id), "clear_other_waypoints": "true",
                                 "add_to_beginning": "false"}, "Destination set")

    async def add_waypoint(self, char_id, location_id: int) -> Sent:
        return await self._send(char_id, WAYPOINT, "/ui/autopilot/waypoint",
                                {"destination_id": int(location_id), "clear_other_waypoints": "false",
                                 "add_to_beginning": "false"}, "Waypoint added")

    async def open_contract(self, char_id, contract_id: int) -> Sent:
        return await self._send(char_id, OPEN_WINDOW, "/ui/openwindow/contract",
                                {"contract_id": int(contract_id)}, "Contract opened")

    async def show_character(self, char_id, target_id: int) -> Sent:
        """Show Info for a character, corporation or alliance (nothing else opens: lab test)."""
        return await self._send(char_id, OPEN_WINDOW, "/ui/openwindow/information",
                                {"target_id": int(target_id)}, "Info opened")

    async def new_mail(self, char_id, recipients, subject: str, body: str) -> Sent:
        """A new mail window, filled in, for the pilot to send (or not) in game. Nothing is sent by ESI."""
        return await self._send(char_id, OPEN_WINDOW, "/ui/openwindow/newmail", None, "New mail opened",
                                body={"recipients": [int(r) for r in recipients], "subject": subject[:1000],
                                      "body": body[:10000]})
