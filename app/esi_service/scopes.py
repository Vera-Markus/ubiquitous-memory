"""
The scope ledger (ESI features plan 26.1): every ESI scope the app uses, and what for.

BASE is what the main build already asks for. BUNDLE is what the ESI features add; at the
merge to v2 it's the list to enable on the application's page at developers.eveonline.com,
and oauth_config.SCOPES becomes BASE + BUNDLE. Each feature names the scopes it needs in
FEATURES, and skips a login that lacks them ("log in again"), never failing.

tests/test_scopes.py fails if any scope string in app/ is missing from BASE or BUNDLE: the
lab's login has every scope, so an unlisted one would work here and only break after the merge.
"""
from typing import Dict, Iterable, Tuple

BASE: Dict[str, str] = {
    "esi-assets.read_assets.v1": "Character assets and names",
    "esi-universe.read_structures.v1": "Naming player structures",
    "esi-clones.read_implants.v1": "Implant sets: the active clone",
    "esi-clones.read_clones.v1": "Implant sets: jump clones",
    "esi-characters.read_corporation_roles.v1": "Who may pull corporation hangars",
    "esi-assets.read_corporation_assets.v1": "Corporation hangars (Directors)",
    "esi-corporations.read_divisions.v1": "Corporation hangar names (Directors)",
}

BUNDLE: Dict[str, str] = {
    "esi-skills.read_skills.v1": "Skill check: trained skills (plan 26)",
    "esi-skills.read_skillqueue.v1": "Skill check: skills finished since the last login (plan 26)",
    "esi-location.read_online.v1": "Open in the game client: is the character in game (plan 27)",
    "esi-location.read_location.v1": "Where the character is: the capital contract search (plan 28.6), "
                                     "and placing the ship they're in",
    "esi-location.read_ship_type.v1": "The ship the character is in, which ESI's assets leave out",
    "esi-ui.open_window.v1": "Open in the game client: market, contracts (plan 27)",
    "esi-ui.write_waypoint.v1": "Open in the game client: destinations and waypoints (plan 27)",
    "esi-contracts.read_character_contracts.v1": "Contracts: your own, and ones you accepted (plan 28)",
    "esi-contracts.read_corporation_contracts.v1": "Contracts: alliance and corporation (plan 28)",
    "esi-fittings.read_fittings.v1": "Fitting sync: import from the game (plan 29)",
    "esi-fittings.write_fittings.v1": "Fitting sync: save to and delete from the game (plan 29)",
    "esi-killmails.read_killmails.v1": "Losses (plan 30)",
    "esi-killmails.read_corporation_killmails.v1": "Losses: corporation ships (Directors, plan 30)",
}

FEATURES: Dict[str, Tuple[str, ...]] = {
    "skills": ("esi-skills.read_skills.v1", "esi-skills.read_skillqueue.v1"),
    "client": ("esi-location.read_online.v1", "esi-ui.open_window.v1", "esi-ui.write_waypoint.v1"),
    "contracts": ("esi-contracts.read_character_contracts.v1", "esi-contracts.read_corporation_contracts.v1"),
    "capital_search": ("esi-location.read_location.v1",),
    "active_ship": ("esi-location.read_location.v1", "esi-location.read_ship_type.v1"),
    "fittings": ("esi-fittings.read_fittings.v1", "esi-fittings.write_fittings.v1"),
    "losses": ("esi-killmails.read_killmails.v1",),
    "losses_corporation": ("esi-killmails.read_corporation_killmails.v1",),
}

ALL = {**BASE, **BUNDLE}


def missing(feature: str, scopes: Iterable[str]) -> Tuple[str, ...]:
    """The feature's scopes a login lacks (empty: it may go ahead)."""
    have = set(scopes)
    return tuple(s for s in FEATURES[feature] if s not in have)
