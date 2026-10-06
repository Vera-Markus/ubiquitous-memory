import base64
import hashlib
import json
import os
import secrets
import urllib.parse
import logging
import asyncio
import threading
import weakref
import jwt
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Tuple

import httpx
from jwt import PyJWKClient
from app.esi_service.callback_listener import AuthCallbackListener
from app.paths import AUTH_DIR
from app.version import __version__

logger = logging.getLogger("AuthService")


def _utc_now() -> datetime:
    """Current UTC time without tzinfo, matching the last_used values already saved in profiles."""
    return datetime.now(timezone.utc).replace(tzinfo=None)

class AuthService:
    """
    Handles OAuth2 PKCE authentication flow for CCP ESI, supporting multiple characters.

    The app holds one instance (EVEFleetGUI's), shared with the asset pull, which runs
    on a worker thread with its own event loop: profile and index changes go through
    _state_lock, and token refreshes take a lock per event loop (F7).
    """
    AUTH_BASE_URL = "https://login.eveonline.com/v2"
    TOKEN_BASE_URL = "https://login.eveonline.com/v2/oauth/token"
    # Access tokens are JWTs signed by CCP (RS256); their keys are published here (F7).
    JWKS_URL = "https://login.eveonline.com/oauth/jwks"
    ISSUERS = ("https://login.eveonline.com", "login.eveonline.com")
    AUTH_DIR = str(AUTH_DIR)
    INDEX_FILE = os.path.join(AUTH_DIR, "index.json")

    def __init__(self, client_id: str, redirect_uri: str):
        self.client_id = client_id
        self.redirect_uri = redirect_uri
        self._ensure_auth_dir()
        
        logger.debug(f"AuthService initialized - ID: {id(self)}")
        
        # Character profiles: { character_id: { tokens, name, owner_hash } }
        self.profiles: Dict[str, Dict] = self._load_all_profiles()
        # Index: { character_id: character_name }
        self.index: Dict[str, str] = self._load_index()
        
        self.active_character_id: Optional[str] = self._determine_active_character()
        
        self._state_lock = threading.RLock()                 # profiles, index and their files
        self._refresh_locks = weakref.WeakKeyDictionary()    # event loop -> asyncio.Lock
        self._jwks_client: Optional[PyJWKClient] = None
        self.callback_listener = AuthCallbackListener()
        self._expected_state: Optional[str] = None

    def _ensure_auth_dir(self) -> None:
        if not os.path.exists(self.AUTH_DIR):
            os.makedirs(self.AUTH_DIR)

    def _load_index(self) -> Dict[str, str]:
        if os.path.exists(self.INDEX_FILE):
            try:
                with open(self.INDEX_FILE, "r") as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Failed to load auth index: {e}")
        return {}

    def _save_index(self) -> None:
        try:
            with open(self.INDEX_FILE, "w") as f:
                json.dump(self.index, f, indent=4)
        except Exception as e:
            logger.error(f"Failed to save auth index: {e}")

    def _load_all_profiles(self) -> Dict[str, Dict]:
        profiles = {}
        if not os.path.exists(self.AUTH_DIR):
            return profiles

        for filename in os.listdir(self.AUTH_DIR):
            if filename.endswith(".json") and filename != "index.json":
                char_id = filename.replace(".json", "")
                try:
                    with open(os.path.join(self.AUTH_DIR, filename), "r") as f:
                        profile_data = json.load(f)
                        # Ensure last_used exists for compatibility
                        if "last_used" not in profile_data:
                            profile_data["last_used"] = _utc_now().isoformat()
                        profiles[char_id] = profile_data
                except Exception as e:
                    logger.error(f"Failed to load profile {char_id}: {e}")
        return profiles

    async def purge_expired_profiles(self, days: int = 30) -> None:
        """
        Deletes profiles that haven't been used within the specified number of days.
        """
        logger.info(f"Starting profile purge (threshold: {days} days)...")
        cutoff_date = _utc_now() - timedelta(days=days)
        expired_ids = []

        for char_id, profile in self.profiles.items():
            last_used_str = profile.get("last_used")
            if not last_used_str:
                # If no last_used, we treat it as potentially expired or skip it.
                # For safety, let's skip it for now or assume it's new.
                continue

            try:
                last_used = datetime.fromisoformat(last_used_str)
                if last_used < cutoff_date:
                    expired_ids.append(char_id)
            except ValueError as e:
                logger.error(f"Failed to parse last_used for {char_id}: {e}")

        if not expired_ids:
            logger.info("No expired profiles found.")
            return

        with self._state_lock:
            for char_id in expired_ids:
                try:
                    name = self.index.get(char_id) or self.profiles.get(char_id, {}).get("name") or char_id
                    # 1. Delete the file
                    file_path = os.path.join(self.AUTH_DIR, f"{char_id}.json")
                    if os.path.exists(file_path):
                        os.remove(file_path)

                    # 2. Remove from memory and the index
                    self.profiles.pop(char_id, None)
                    self.index.pop(char_id, None)

                    logger.info(f"Purged expired profile: {name} ({char_id})")
                except Exception as e:
                    logger.error(f"Failed to purge profile {char_id}: {e}")

            # Save the updated index
            self._save_index()
        logger.info(f"Purge complete. Removed {len(expired_ids)} profiles.")

    def _determine_active_character(self) -> Optional[str]:
        # For now, we'll just pick the first one available if no preference is set.
        # In a real app, this might come from a user setting or config.
        if self.profiles:
            return list(self.profiles.keys())[0]
        return None

    def signing_key(self, token: str):
        """CCP's public key for this token (by its kid), from CCP's key page; fetched once and cached."""
        if self._jwks_client is None:
            self._jwks_client = PyJWKClient(self.JWKS_URL, cache_keys=True, timeout=15,
                                            headers={"User-Agent": f"EveFleetManagementTool/{__version__}"})
        return self._jwks_client.get_signing_key_from_jwt(token).key

    def verify_access_token(self, access_token: str) -> Dict:
        """
        The token's claims, after checking it was signed by CCP (RS256, against CCP's
        published keys), issued by CCP's login server, issued to this app, and not
        expired (F7). Raises if any check fails, or if CCP's keys can't be fetched.
        """
        try:
            key = self.signing_key(access_token)
        except jwt.PyJWKClientError as e:
            raise Exception(f"Couldn't fetch CCP's login keys to check the login; try again ({e}).") from e
        return jwt.decode(access_token, key, algorithms=["RS256"], audience=self.client_id,
                          issuer=list(self.ISSUERS), leeway=60)

    def _extract_identity(self, tokens: Dict[str, str]) -> Tuple[str, str, str]:
        """
        Extracts character_id, name, and owner_hash from the access token, once its
        signature and claims check out (verify_access_token).
        """
        access_token = tokens.get("access_token")
        if not access_token:
            raise Exception("No access token found in exchanged tokens.")

        try:
            decoded = self.verify_access_token(access_token)
            
            # sub is 'CHARACTER:EVE:123456789'
            sub = decoded.get("sub", "")
            if not sub.startswith("CHARACTER:EVE:"):
                raise Exception(f"Unexpected 'sub' claim format: {sub}")
            
            char_id = sub.split(":")[-1]
            char_name = decoded.get("name", "Unknown Character")
            owner_hash = decoded.get("owner", "")

            if not char_id or not char_name or not owner_hash:
                raise Exception("Missing required identity claims in JWT.")

            return char_id, char_name, owner_hash
        except Exception as e:
            logger.error(f"Failed to extract identity from JWT: {e}")
            raise Exception(f"Identity extraction failed: {e}")

    async def save_character_profile(self, char_id: str, char_name: str, tokens: Dict[str, str]) -> None:
        """
        Saves a character's profile and updates the index.
        """
        profile_path = os.path.join(self.AUTH_DIR, f"{char_id}.json")
        
        # Update last_used timestamp
        tokens["last_used"] = _utc_now().isoformat()
        
        profile_data = {
            **tokens,
            "name": char_name
        }

        try:
            with self._state_lock:
                with open(profile_path, "w") as f:
                    json.dump(profile_data, f, indent=4)

                self.profiles[char_id] = profile_data
                self.index[char_id] = char_name
                self._save_index()
            logger.info(f"Saved profile for {char_name} ({char_id})")
        except Exception as e:
            logger.error(f"Failed to save profile for {char_id}: {e}")
            raise


    def remove_profile(self, char_id: str) -> bool:
        """
        Deletes a character's login: its profile file (tokens included) and its
        index entry. If it was the active character, another one becomes active
        (or none). Returns True if there was anything to remove.
        """
        char_id = str(char_id)
        profile_path = os.path.join(self.AUTH_DIR, f"{char_id}.json")
        with self._state_lock:
            found = os.path.exists(profile_path) or char_id in self.profiles or char_id in self.index
            if os.path.exists(profile_path):
                os.remove(profile_path)
            self.profiles.pop(char_id, None)
            if self.index.pop(char_id, None) is not None:
                self._save_index()
            if self.active_character_id == char_id:
                self.active_character_id = self._determine_active_character()
        if found:
            logger.info(f"Removed the login for character {char_id}")
        return found

    async def switch_character(self, char_id: str) -> None:
        """
        Sets the active character.
        """
        if char_id not in self.profiles:
            raise Exception(f"Character {char_id} not found in profiles.")
        
        self.active_character_id = char_id
        logger.info(f"Switched active character to: {self.index.get(char_id)}")

    def get_access_token(self) -> Optional[str]:
        """
        Returns the current access token for the active character.
        """
        if not self.active_character_id:
            return None
        
        profile = self.profiles.get(self.active_character_id)
        return profile.get("access_token") if profile else None

    def generate_pkce(self) -> Tuple[str, str]:
        """
        Generates a code verifier and code challenge for PKCE.
        """
        code_verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")
        sha256 = hashlib.sha256()
        sha256.update(code_verifier.encode())
        code_challenge = base64.urlsafe_b64encode(sha256.digest()).decode().rstrip("=")
        return code_verifier, code_challenge

    def get_authorization_url(self, scopes: list[str], code_challenge: str) -> str:
        """
        Generates the URL for the user to visit to authorize the application.
        """
        # Generate a cryptographically secure state
        self._expected_state = secrets.token_urlsafe(16)
        
        query_params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": " ".join(scopes),
            "state": self._expected_state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        return f"{self.AUTH_BASE_URL}/oauth/authorize?{urllib.parse.urlencode(query_params)}"

    async def exchange_code(self, authorization_code: str, code_verifier: str) -> Dict[str, str]:
        """
        Exchanges an authorization code for access and refresh tokens.
        """
        data = {
            "grant_type": "authorization_code",
            "code": authorization_code,
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "code_verifier": code_verifier,
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.post(self.TOKEN_BASE_URL, data=data)
            response.raise_for_status()
            new_tokens = response.json()
            
            # Extract identity to know which character we just logged in as
            char_id, char_name, owner_hash = self._extract_identity(new_tokens)
            
            # Save the new profile
            await self.save_character_profile(char_id, char_name, new_tokens)
            
            # Always update the active character to the one just authenticated
            self.active_character_id = char_id
                
            return new_tokens

    async def perform_full_auth_flow(self, scopes: list[str]) -> Tuple[Dict[str, str], str, str]:
        """
        Performs the complete OAuth2 PKCE flow:
        1. Starts the callback listener.
        2. Opens the browser to the CCP authorization page.
        3. Waits for the callback.
        4. Exchanges the code for tokens.

        Returns: (tokens, char_id, char_name)
        """
        logger.info(f"[AUDIT] perform_full_auth_flow - Scopes requested: {scopes}")
        code_verifier, code_challenge = self.generate_pkce()
        auth_url = self.get_authorization_url(scopes, code_challenge)
        logger.info(f"[AUDIT] perform_full_auth_flow - Generated authorization URL: {auth_url}")

        # Start the listener
        await self.callback_listener.start()
        # Ensure listener is clean before waiting
        self.callback_listener.reset()
        
        try:
            # Open browser
            logger.info(f"Opening browser for authorization: {auth_url}")
            # Use webbrowser module (standard lib) or similar
            import webbrowser
            webbrowser.open(auth_url)

            # Wait for code
            logger.info("Waiting for authorization code via callback listener...")
            callback_result = await self.callback_listener.wait_for_code()

            if not callback_result:
                raise Exception("Authentication timed out or failed to capture code.")

            code = callback_result.get("code")
            state = callback_result.get("state")
            
            # Validate state to prevent CSRF
            if state != self._expected_state:
                logger.error("Login callback state mismatch: the response didn't come from this login attempt.")
                raise Exception("CSRF Warning: the login response's state doesn't match this login attempt.")

            logger.debug("Login callback state validated; authorization code received.")

            # Exchange code for tokens
            tokens = await self.exchange_code(code, code_verifier)
            
            # We need to extract identity again because exchange_code returns tokens
            # but we want to return the identity that was just used.
            # Since exchange_code already updated self.active_character_id (in my previous commit),
            # we can rely on it, OR better, we can re-extract to be safe.
            
            char_id, char_name, _ = self._extract_identity(tokens)
            
            logger.info(f"Authentication flow completed successfully for {char_name} ({char_id}).")
            return tokens, char_id, char_name

        finally:
            # Always stop the listener
            await self.callback_listener.stop()

    def _refresh_lock(self) -> asyncio.Lock:
        """
        One refresh lock per event loop: the shared instance is used from the GUI's
        login loop and from each pull's own loop, and an asyncio.Lock belongs to one loop.
        """
        loop = asyncio.get_running_loop()
        with self._state_lock:
            lock = self._refresh_locks.get(loop)
            if lock is None:
                lock = self._refresh_locks[loop] = asyncio.Lock()
            return lock

    async def refresh_access_token(self) -> Dict[str, str]:
        """
        Uses the refresh token to obtain a new access token.
        Uses an asyncio Lock to prevent concurrent refresh requests.
        """
        if not self.active_character_id:
            raise Exception("No active character selected.")

        async with self._refresh_lock():
            # Double-check if another task already refreshed it while we were waiting for the lock
            # In a real scenario, we'd check if the current access_token is still 'old'
            # But for simplicity, we'll just proceed if we are the lock holder.
            
            profile = self.profiles.get(self.active_character_id)
            if not profile:
                # Removed (or never loaded) since the pull started: don't bring it back.
                raise Exception("Active character profile not found.")

            refresh_token = profile.get("refresh_token")
            if not refresh_token:
                raise Exception("No refresh token available. User must re-authenticate.")

            data = {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": self.client_id,
            }

            logger.info(f"Attempting to refresh access token for {self.active_character_id}...")
            async with httpx.AsyncClient() as client:
                try:
                    response = await client.post(self.TOKEN_BASE_URL, data=data)
                    response.raise_for_status()
                    new_tokens = response.json()
                    
                    # Update the profile with new tokens
                    # We need to re-extract identity in case it changed (unlikely but safe)
                    char_id, char_name, owner_hash = self._extract_identity(new_tokens)
                    
                    await self.save_character_profile(char_id, char_name, new_tokens)
                    
                    logger.info("Token refresh successful.")
                    return new_tokens
                except httpx.HTTPStatusError as e:
                    logger.error(f"Token refresh failed with status {e.response.status_code}: {e.response.text}")
                    raise Exception(f"ESI Authentication Refresh Failed: {e.response.status_code}")
                except Exception as e:
                    logger.error(f"Unexpected error during token refresh: {e}")
                    raise Exception(f"ESI Authentication Refresh Error: {str(e)}")

