"""
Settings sent with every ESI call, following CCP's ESI best practices (RC2 §1).

- User-Agent: the app's name and version and a contact, so CCP can reach the
  developer about the app's traffic. Testers can read it in the source; that's intended.
- X-Compatibility-Date: the ESI behaviour the app was checked against. Without it,
  ESI answers as of its oldest date (2020-01-01).
"""
from app.version import __version__

ESI_BASE_URL = "https://esi.evetech.net"        # dated requests replace the old /latest/ path

# Checked 2026-10-06: the OpenAPI specs for 2020-01-01 and 2026-08-18 are identical for
# every route the app calls (character and corp assets and their names, structures,
# roles, divisions). Before moving it on, compare the specs again
# (https://esi.evetech.net/meta/openapi.json?compatibility_date=YYYY-MM-DD).
COMPATIBILITY_DATE = "2026-08-18"

CONTACT = "verafleetmanagementtool@gmail.com"
USER_AGENT = f"EveFleetManagementTool/{__version__} ({CONTACT})"


def esi_headers() -> dict[str, str]:
    """Headers for every ESI request."""
    return {"User-Agent": USER_AGENT, "X-Compatibility-Date": COMPATIBILITY_DATE}
