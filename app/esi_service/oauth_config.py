"""
Centralized OAuth2 configuration for the Eve Fleet Management Tool.
This file serves as the single source of truth for Client ID, Redirect URI, and Scopes.
"""

from typing import List

# CCP Developer Portal Configuration
CLIENT_ID = "ce1794f8622a499fa9016460dfbd040a"
REDIRECT_URI = "http://localhost:8080/"

# Requested ESI Scopes
# Note: 'publicData' is the minimum for /characters/ endpoint.
SCOPES: List[str] = ["esi-assets.read_assets.v1", "esi-universe.read_structures.v1", "publicData"]

# Planned (not requested yet): corporation asset pulls will need
#   "esi-assets.read_corporation_assets.v1"
# for GET /corporations/{corporation_id}/assets/ (needs a Director role in game).
# Add it to SCOPES with that feature; until then nothing uses it, so logins don't ask for it.
