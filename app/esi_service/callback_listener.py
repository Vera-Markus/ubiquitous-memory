import asyncio
from aiohttp import web
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger("CallbackListener")

class AuthCallbackListener:
    """
    A lightweight aiohttp server that listens for CCP OAuth2 redirects.
    """
    def __init__(self, host: str = "localhost", port: int = 8080):
        self.host = host
        self.port = port
        self.server: Optional[web.Server] = None
        self.captured_data: Dict[str, Any] = {}
        self._event: Optional[asyncio.Event] = None

    def reset(self) -> None:
        """Resets the captured data and the event for a new authentication attempt."""
        self.captured_data = {}
        if self._event:
            self._event.clear()
        logger.info("CallbackListener state reset.")

    async def handle_callback(self, request: web.Request) -> web.Response:
        """
        Handles the incoming redirect from CCP.
        Expected query params: code, state
        """
        logger.info(f"Received redirect request: {request.query}")

        self.captured_data = {
            "code": request.query.get("code"),
            "state": request.query.get("state")
        }

        if self.captured_data["code"]:
            logger.info(f"Successfully captured authorization code: {self.captured_data['code'][:10]}...")
            if self._event:
                self._event.set()
            return web.Response(text="<h1>Authentication Successful!</h1><p>You can close this window and return to the application.</p>", content_type='text/html')
        else:
            logger.error("Redirect received but no authorization code found in query parameters.")
            return web.Response(text="<h1>Authentication Failed</h1><p>No authorization code was found in the redirect.</p>", status=400, content_type='text/html')

    async def start(self):
        """Starts the HTTP server."""
        self._event = asyncio.Event()
        self.app = web.Application()
        self.app.router.add_get('/', self.handle_callback)
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, self.host, self.port)
        await self.site.start()
        logger.info(f"Callback listener started on http://{self.host}:{self.port}")

    async def stop(self):
        """Stops the HTTP server."""
        if self.runner:
            await self.runner.cleanup()
            logger.info("Callback listener stopped.")

    async def wait_for_code(self, timeout: float = 300.0) -> Optional[Dict[str, str]]:
        """
        Waits for the authorization code to be captured.
        Returns a dict with 'code' and 'state', or None if timed out.
        """
        try:
            if not self._event:
                logger.error("No event found. Is the listener started?")
                return None
            await asyncio.wait_for(self._event.wait(), timeout=timeout)
            return self.captured_data
        except asyncio.TimeoutError:
            logger.error("Timed out waiting for authorization code.")
            return None
