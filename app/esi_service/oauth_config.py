"""
Centralized OAuth2 configuration for the Eve Fleet Management Tool.
This file serves as the single source of truth for Client ID, Redirect URI, and Scopes.
"""

from typing import List

# CCP Developer Portal Configuration
CLIENT_ID = "ce1794f8622a499fa9016460dfbd040a"
REDIRECT_URI = "http://localhost:8080/"

# Requested ESI Scopes: each one is used by a call the app makes (/characters/{id}/ is public and needs none).
SCOPES: List[str] = ["esi-assets.read_assets.v1", "esi-universe.read_structures.v1",
                     # Clones and implants (implants in doctrines): pulled with the assets.
                     "esi-clones.read_implants.v1", "esi-clones.read_clones.v1",
                     # Corporation hangars (tracked items plan, Phase 13): roles decide who pulls;
                     # only a Director can read a corporation's assets and division names.
                     "esi-characters.read_corporation_roles.v1", "esi-assets.read_corporation_assets.v1",
                     "esi-corporations.read_divisions.v1"]
