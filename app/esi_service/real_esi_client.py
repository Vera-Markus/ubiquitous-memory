import httpx
import logging
from typing import TYPE_CHECKING, Optional
from datetime import datetime
from app.esi_service.base_interfaces import IESIClient, ESIRequest, ESIResponse

if TYPE_CHECKING:
    from app.esi_service.auth_service import AuthService

logger = logging.getLogger("RealESIClient")

class RealESIClient(IESIClient):
    """
    A production-grade ESI client implementation for the actual CCP ESI.
    """
    def __init__(self, base_url: str, auth_service: Optional['AuthService'] = None):
        self.base_url = base_url.rstrip("/")
        self.auth_service = auth_service
        self.client = httpx.AsyncClient(base_url=self.base_url)

    async def request(self, request: ESIRequest) -> ESIResponse:
        # Construct full URL
        path = request.url
        headers = request.headers.copy() if request.headers else {}
        
        # Inject token from AuthService if available
        if self.auth_service:
            token = self.auth_service.get_access_token()
            if token:
                headers["Authorization"] = f"Bearer {token}"

        # Track if we have already attempted a retry due to 401
        attempted_retry = False

        try:
            response = await self.client.request(
                method=request.method,
                url=path,
                params=request.params,
                headers=headers,
                timeout=15.0
            )

            # Handle 401 Unauthorized with automatic refresh and retry
            if response.status_code == 401 and self.auth_service and not attempted_retry:
                logger.info("Detected 401 Unauthorized. Attempting token refresh...")
                try:
                    await self.auth_service.refresh_access_token()
                    attempted_retry = True
                    
                    # Update token for the retry
                    new_token = self.auth_service.get_access_token()
                    if new_token:
                        headers["Authorization"] = f"Bearer {new_token}"
                        logger.debug("Token refreshed. Retrying original request...")
                        
                        response = await self.client.request(
                            method=request.method,
                            url=path,
                            params=request.params,
                            headers=headers,
                            timeout=15.0
                        )
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

            # Verbose Logging: Response
            logger.debug(f"RESPONSE STATUS: {response.status_code}")
            logger.debug(f"RESPONSE HEADERS: {dict(response.headers)}")
            
            body_preview = response.text[:500]
            logger.debug(f"RESPONSE BODY PREVIEW:\n{body_preview}")
            logger.debug("-" * 40)

            # Extract data
            data = None
            if response.status_code in (200, 429, 400, 401, 403, 404, 420):
                try:
                    data = response.json()
                except Exception:
                    data = response.text
            elif response.status_code == 304:
                data = None  # 304 has no body

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
