"""
Centralized OAuth2 configuration for the Eve Fleet Management Tool.
This file serves as the single source of truth for Client ID, Redirect URI, and Scopes.
"""

from typing import List

from app.esi_service import scopes

# CCP Developer Portal Configuration
CLIENT_ID = "ce1794f8622a499fa9016460dfbd040a"
REDIRECT_URI = "http://localhost:8080/"

# Requested ESI Scopes: every one in the scope ledger (app/esi_service/scopes.py), which says what each
# is used for. The application's page at developers.eveonline.com must have each of them ticked, or the
# login fails with invalid_scope. (/characters/{id}/ and the other public routes need none.)
SCOPES: List[str] = list(scopes.ALL)
