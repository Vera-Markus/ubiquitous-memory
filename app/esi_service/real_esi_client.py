import asyncio
import time
import httpx
import jwt
import logging
from typing import TYPE_CHECKING, Awaitable, Callable, Optional
from datetime import datetime
from app.esi_service.base_interfaces import IESIClient, ESIRequest, ESIResponse
from app.esi_service.esi_settings import ESI_BASE_URL, esi_headers

if TYPE_CHECKING:
    from app.esi_service.auth_service import AuthService

logger = logging.getLogger("RealESIClient")


def _int_header(headers, name: str) -> Optional[int]:
    try:
        return int(headers.get(name))
    except (TypeError, ValueError):
        return None


def _expires_soon(token: str, margin: int = 60) -> bool:
    """True if the access token expires within `margin` seconds (read without verifying: timing only)."""
    try:
        exp = jwt.decode(token, options={"verify_signature": False}).get("exp")
    except jwt.PyJWTError:
        return False
    return exp is not None and exp - time.time() < margin


class RealESIClient(IESIClient):
    """
    A production-grade ESI client implementation for the actual ESI.

    Every call carries the User-Agent and compatibility date (esi_settings), and the
    client keeps to ESI's limits (RC2 §1): a 429 waits out Retry-After and tries again;
    a 420, or ESI's error allowance running low, pauses every call through this client
    until ESI's window resets (callers get a 420 back without ESI being asked); and when
    a route's rate-limit tokens run low, calls are spaced out.
    """
    MAX_429_RETRIES = 2
    MAX_RETRY_AFTER = 60        # seconds; a longer Retry-After gives up and returns the 429
    ERROR_LIMIT_FLOOR = 10      # errors left in ESI's window before pausing until it resets
    LOW_TOKENS = 100            # rate-limit tokens left before calls are spaced out
    SLOW_DOWN_SECONDS = 1.0

    def __init__(self, base_url: str = ESI_BASE_URL, auth_service: Optional['AuthService'] = None,
                 transport: Optional[httpx.AsyncBaseTransport] = None,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                 clock: Callable[[], float] = time.monotonic):
        self.base_url = base_url.rstrip("/")
        self.auth_service = auth_service
        self.client = httpx.AsyncClient(base_url=self.base_url, headers=esi_headers(), transport=transport)
        self._sleep = sleep
        self._clock = clock
        self._paused_until: Optional[float] = None      # clock time ESI's error window resets

    def paused_for(self) -> float:
        """Seconds until calls go to ESI again after an error-limit pause (0 if not paused)."""
        if self._paused_until is None:
            return 0.0
        return max(self._paused_until - self._clock(), 0.0)

    def _pause(self, seconds: int, why: str) -> None:
        seconds = max(seconds, 1)
        self._paused_until = self._clock() + seconds
        logger.warning(f"{why}: pausing ESI calls for {seconds} s, until ESI's error window resets.")

    def _note_limits(self, response: httpx.Response) -> None:
        """Reads ESI's limit headers after each response (see the class docstring)."""
        headers = response.headers
        reset = _int_header(headers, "X-ESI-Error-Limit-Reset")
        if response.status_code == 420:
            self._pause(reset or 60, "ESI's error limit was reached (420)")
            return
        remain = _int_header(headers, "X-ESI-Error-Limit-Remain")
        if remain is not None and remain <= self.ERROR_LIMIT_FLOOR:
            self._pause(reset or 60, f"Only {remain} errors left in ESI's error window")

    async def _send(self, request: ESIRequest, headers: dict) -> httpx.Response:
        return await self.client.request(
            method=request.method,
            url=request.url,
            params=request.params,
            json=request.json,
            headers=headers,
            timeout=15.0
        )

    async def request(self, request: ESIRequest) -> ESIResponse:
        headers = request.headers.copy() if request.headers else {}

        paused = self.paused_for()
        if paused > 0:
            return ESIResponse(
                status_code=420,
                data={"error": f"ESI calls are paused for {int(paused) + 1} s to stay within ESI's error limit."},
                headers={},
                timestamp=datetime.now()
            )

        # Inject token from AuthService if available. A token about to expire is refreshed
        # first: the 401 ESI would answer counts against its error limit.
        if self.auth_service:
            token = self.auth_service.get_access_token()
            if token and _expires_soon(token):
                try:
                    await self.auth_service.refresh_access_token()
                    token = self.auth_service.get_access_token()
                except Exception as e:
                    logger.warning(f"Token refresh before the call failed ({e}); trying with the old token.")
            if token:
                headers["Authorization"] = f"Bearer {token}"

        try:
            response = await self._send(request, headers)

            # Handle 401 Unauthorized with automatic refresh and retry. A 401 for a scope the
            # login lacks won't refresh away, so it goes straight back to the caller.
            if response.status_code == 401 and self.auth_service and "scope" not in response.text:
                logger.info("Detected 401 Unauthorized. Attempting token refresh...")
                try:
                    await self.auth_service.refresh_access_token()

                    # Update token for the retry
                    new_token = self.auth_service.get_access_token()
                    if new_token:
                        headers["Authorization"] = f"Bearer {new_token}"
                        logger.debug("Token refreshed. Retrying original request...")
                        response = await self._send(request, headers)
                    else:
                        raise Exception("Refresh succeeded but no new token was returned.")
                except Exception as refresh_error:
                    logger.error(f"Automatic token refresh failed: {refresh_error}")
                    # Re-raise as an ESIResponse with 401 to indicate authentication failure
                    return ESIResponse(
                        status_code=401,
                        data={"error": f"Authentication failed during auto-refresh: {str(refresh_error)}"},
                        headers={},
                        timestamp=datetime.now()
                    )

            # Rate limited: wait as long as ESI asks, a limited number of times.
            retries = 0
            while response.status_code == 429 and retries < self.MAX_429_RETRIES:
                wait = _int_header(response.headers, "Retry-After")
                if wait is None or wait > self.MAX_RETRY_AFTER:
                    logger.warning(f"ESI rate limit (429) on {request.url}; Retry-After {wait} s is too long to wait.")
                    break
                logger.warning(f"ESI rate limit (429) on {request.url}; waiting {wait} s as ESI asks.")
                await self._sleep(wait)
                retries += 1
                response = await self._send(request, headers)

            self._note_limits(response)

            # Verbose Logging: Response
            logger.debug(f"RESPONSE STATUS: {response.status_code}")
            logger.debug(f"RESPONSE HEADERS: {dict(response.headers)}")

            body_preview = response.text[:500]
            logger.debug(f"RESPONSE BODY PREVIEW:\n{body_preview}")
            logger.debug("-" * 40)

            # Extract data
            data = None
            if response.status_code in (200, 201, 429, 400, 401, 403, 404, 420, 520):     # 201: a saved fit's ID
                try:
                    data = response.json()
                except Exception:
                    data = response.text
            elif response.status_code == 304:
                data = None  # 304 has no body

            # Spread calls out when this route's token bucket runs low.
            tokens_left = _int_header(response.headers, "X-Ratelimit-Remaining")
            if tokens_left is not None and tokens_left < self.LOW_TOKENS:
                logger.warning(f"ESI rate limit: {tokens_left} tokens left for "
                               f"{response.headers.get('X-Ratelimit-Group', 'this route')}; slowing down.")
                await self._sleep(self.SLOW_DOWN_SECONDS)

            return ESIResponse(
                status_code=response.status_code,
                data=data,
                headers=dict(response.headers),
                timestamp=datetime.now()
            )

        except httpx.HTTPError as e:
            logger.error(f"HTTP Error occurred: {e}")
            return ESIResponse(
                status_code=500,
                data={"error": str(e)},
                headers={},
                timestamp=datetime.now()
            )
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            return ESIResponse(
                status_code=500,
                data={"error": str(e)},
                headers={},
                timestamp=datetime.now()
            )

    async def close(self):
        await self.client.aclose()
