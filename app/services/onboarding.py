"""
Onboard and Adopt for one system (homes and priorities plan, step 24.2; S3-S5, D2-D4).

Both only ever run when the user confirms a preview; nothing here changes anything until
apply_onboarding / apply_adoption is called with the user's choices.

- **Onboard** (new ships): each linked character's hulls in the system with no fitting (and
  not <Personal>) of a hull some requirement of theirs there uses. Each is offered the
  fittings their requirements in the system use for that hull (D2), preselected when there's
  one, with how the ship would audit against each. Applied: the fitting, the character as
  owner, and the system as Home.
- **Next** (first-time setup, D2): their hulls in the system with no fitting whose hull has no
  saved fitting at all (a mining fleet), offered as <Personal>.
- **A corporation as owner** (1.7.2 plan, 31): the window can give every ship it onboards a
  corporation as owner instead of its holder. Corporation ships never count for a character's
  requirement, so doctrines don't limit the choice: each hull is offered every saved fitting
  (`all_fittings`, the ones required here first), and hulls nothing here requires but that have a
  saved fitting are offered too (`plan.other`, only while the corporation owner is on).
- Ships carried inside another ship (an escape Astero in a bay) are left out of both: their
  carrier's bays account for them.
- **Adopt** (established ships): their ships in the system with a fitting, whose Home is
  another system or none (never Anywhere, C2). Only ships whose fitting a requirement of
  theirs in the system uses can be adopted (D3); others are listed but not offered.
  Applied: Home = the system; the fitting is never touched (Q6). Adopting can leave the old
  Home short; adoption_warnings says where, before the user confirms.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.loaders.role_manager import fitting_in_use
from app.loaders.ship_designations import home_system, is_anywhere
from app.models.audit_models import RequirementStatus
from app.services.audit.bays import BayContext
from app.services.requirement_priority import covering

SKIP = "Skip"
PERSONAL = "<Personal>"


@dataclass
class ShipInSystem:
    item_id: int
    type_id: int
    hull: str
    custom_name: str
    character_id: int
    character_name: str
    where: str

    def label(self) -> str:
        name = f'{self.hull} "{self.custom_name}"' if self.custom_name else self.hull
        return f"{name} · {self.character_name} · {self.where}"


@dataclass
class OnboardShip(ShipInSystem):
    fittings: List[Tuple[int, str]] = field(default_factory=list)     # (fit_uid, name) required here for this hull
    previews: Dict[int, str] = field(default_factory=dict)            # fit_uid -> "Ready", "Not ready: 3 items missing"
    all_fittings: List[Tuple[int, str]] = field(default_factory=list)  # every saved fitting of the hull, required first

    @property
    def default(self) -> str:
        """One fitting to choose from: chosen. Several: the user picks (S4)."""
        return self.fittings[0][1] if len(self.fittings) == 1 else ""


@dataclass
class AdoptShip(ShipInSystem):
    fit_uid: int = 0
    fit_name: str = ""
    home: Optional[Dict[str, Any]] = None
    home_name: str = ""                 # "" for no Home
    required: bool = True               # a requirement of theirs here uses its fitting (D3)


@dataclass
class SystemShips:
    system_id: int
    system_name: str
    onboard: List[OnboardShip] = field(default_factory=list)
    personal: List[ShipInSystem] = field(default_factory=list)
    adopt: List[AdoptShip] = field(default_factory=list)
    other: List[OnboardShip] = field(default_factory=list)      # saved fittings, none required here: corporation owner only


def _roles_of(doctrine_manager: Any, character_id: int) -> List[int]:
    roles = set()
    for doctrine in doctrine_manager.list_doctrines():
        for role_key, assigned in (doctrine.get("character_assignments") or {}).items():
            if str(character_id) in {str(c) for c in assigned} and str(role_key).isdigit():
                roles.add(int(role_key))
    return sorted(roles)


def _requirements(role_manager: Any, doctrine_manager: Any, character_id: int,
                  system_id: Optional[int] = None) -> List[Tuple[dict, dict]]:
    """(requirement, role) for the character's roles, optionally only those in one system."""
    found = []
    for role_uid in _roles_of(doctrine_manager, character_id):
        role = role_manager.get_role(role_uid) or {}
        for req in role.get("requirements", []):
            if system_id is None or req.get("system_id") == system_id:
                found.append((req, role))
    return found


def _preview(tracking: Any, engine: Any, fitting_manager: Any, sighting: Any, fitting: dict) -> str:
    """How the ship would audit against the fitting, so a wrong pick is seen before it costs anything."""
    try:
        ship, carried, ships = tracking.ships_of(sighting)
        if ship is None:
            return ""
        result = engine.audit_ship(ship, fitting, BayContext(carried=carried, ships_by_item=ships,
                                                              get_fitting=fitting_manager.get_fitting))
    except Exception:
        return ""
    if result.status == RequirementStatus.PASS:
        return "Ready"
    if result.status == RequirementStatus.WARN:
        return "Ready, needs attention"
    missing = sum(1 for s in result.shortfalls if s.missing > 0)
    return f"Not ready: {missing} item type{'s' if missing != 1 else ''} missing" if missing else "Not ready"


def plan_system(system_id: int, characters: Dict[int, str], tracking: Any, role_manager: Any, doctrine_manager: Any,
                fitting_manager: Any, engine: Any) -> SystemShips:
    """What Onboard and Adopt would offer in the system, for these characters ({ID: name}). Changes nothing."""
    sde = tracking.sde
    plan = SystemShips(system_id, sde.get_system_name(system_id) or str(system_id))
    designations = tracking.designations
    fittings = {f["fit_uid"]: f for f in fitting_manager.list_fittings()}

    def by_name(uids):
        return sorted(((uid, fittings[uid].get("fit_name", str(uid))) for uid in uids), key=lambda f: f[1].casefold())
    for character_id, character_name in characters.items():
        required = {fitting_in_use(req) for req, _ in _requirements(role_manager, doctrine_manager, character_id, system_id)}
        required = {uid for uid in required if uid in fittings}
        for sighting in tracking.universe.ships_held_by({"kind": "character", "id": int(character_id)}):
            if sighting.system_id != system_id:
                continue
            base = dict(item_id=sighting.item_id, type_id=sighting.type_id, hull=sde.get_type_name(sighting.type_id),
                        custom_name=sighting.custom_name or "", character_id=int(character_id),
                        character_name=character_name, where=tracking.where(sighting))
            d = designations.get(sighting.item_id) if designations is not None else None
            if d is None:
                if sighting.carrier_item_id is not None:
                    continue        # carried in another ship: its carrier's bays account for it, not onboarding
                for_hull = by_name(uid for uid in required if fittings[uid].get("hull_type_id") == sighting.type_id)
                others = by_name(uid for uid, f in fittings.items()
                                 if f.get("hull_type_id") == sighting.type_id and uid not in required)
                if for_hull or others:
                    ship = OnboardShip(**base, fittings=for_hull, all_fittings=for_hull + others, previews={
                        uid: _preview(tracking, engine, fitting_manager, sighting, fittings[uid])
                        for uid, _ in for_hull + others})
                    (plan.onboard if for_hull else plan.other).append(ship)
                else:
                    plan.personal.append(ShipInSystem(**base))
                continue
            if d.get("personal") or d.get("fit_uid") is None or is_anywhere(d.get("home")):
                continue
            if home_system(d.get("home")) == system_id:
                continue                                    # already this system's
            if int((d.get("owner") or {}).get("id") or 0) != int(character_id):
                continue                                    # someone else's ship: not theirs to adopt
            home = d.get("home")
            plan.adopt.append(AdoptShip(
                **base, fit_uid=d["fit_uid"], fit_name=fittings.get(d["fit_uid"], {}).get("fit_name", str(d["fit_uid"])),
                home=home, home_name=sde.get_system_name(home_system(home)) if home else "",
                required=d["fit_uid"] in required))
    for rows in (plan.onboard, plan.personal, plan.adopt, plan.other):
        rows.sort(key=lambda s: (s.character_name.casefold(), s.hull.casefold(), s.custom_name.casefold(), s.item_id))
    return plan


def adoption_warnings(plan: SystemShips, chosen: Iterable[int], tracking: Any, role_manager: Any,
                      doctrine_manager: Any) -> List[str]:
    """
    What adopting the chosen ships would leave short: for each (character, fitting, old Home),
    when fewer ships keep that Home than the character's requirements there need. Each fails
    hard afterwards (a purchase, or a ship moved back, H9).
    """
    chosen = set(chosen)
    leaving: Dict[Tuple[int, int, int], List[AdoptShip]] = {}
    for ship in plan.adopt:
        old = home_system(ship.home)
        if ship.item_id in chosen and old is not None:
            leaving.setdefault((ship.character_id, ship.fit_uid, old), []).append(ship)
    lines = []
    designations = tracking.designations
    for (character_id, fit_uid, old), ships in sorted(leaving.items()):
        needed = sum(1 for req, role in _requirements(role_manager, doctrine_manager, character_id, old)
                     if fitting_in_use(req) == fit_uid and covering(role, req) is None)
        if not needed:
            continue
        owner = {"kind": "character", "id": character_id}
        staying = sum(1 for d in designations.assigned_to(fit_uid, owner) if home_system(d.get("home")) == old) - len(ships)
        if staying < needed:
            short = needed - max(staying, 0)
            lines.append(f"{ships[0].home_name}: {ships[0].character_name}'s {ships[0].fit_name} will be {short} "
                         f"ship{'s' if short != 1 else ''} short (fails hard: buy one, or move one back)")
    return lines


def apply_onboarding(plan: SystemShips, choices: Dict[int, str], personal: Iterable[int], designations: Any,
                     when: Optional[str] = None, owner: Optional[Dict[str, Any]] = None) -> Tuple[int, int]:
    """
    choices: item ID -> the fitting name chosen, SKIP or PERSONAL. personal: item IDs from the
    Next page to mark <Personal>. owner: a corporation ({kind, id}) every ship onboarded here
    gets instead of its holder; then any saved fitting of the hull can be chosen, and
    plan.other's ships are onboarded too. Returns (ships given a fitting, ships marked Personal).
    """
    when = when or datetime.now(timezone.utc).isoformat(timespec="seconds")
    owner = {"kind": owner["kind"], "id": int(owner["id"])} if owner else None
    assigned = marked = 0
    for ship in plan.onboard + (plan.other if owner else []):
        choice = choices.get(ship.item_id) or ship.default
        ship_owner = owner or {"kind": "character", "id": ship.character_id}
        if choice == PERSONAL:
            designations.mark_personal(ship.item_id, ship.type_id, ship_owner, ship.custom_name, when)
            marked += 1
            continue
        fit_uid = next((uid for uid, name in (ship.all_fittings if owner else ship.fittings) if name == choice), None)
        if fit_uid is not None:
            designations.assign(ship.item_id, ship.type_id, fit_uid, ship_owner, ship.custom_name, when,
                                system_id=plan.system_id)
            assigned += 1
    chosen = set(personal)
    for ship in plan.personal:
        if ship.item_id in chosen:
            designations.mark_personal(ship.item_id, ship.type_id,
                                       owner or {"kind": "character", "id": ship.character_id}, ship.custom_name, when)
            marked += 1
    return assigned, marked


def onboard_ships(designations: Any, ships: Iterable[Any], fit_uid: Optional[int], holder: Dict[str, Any],
                  owner: Optional[Dict[str, Any]], home: Optional[Dict[str, Any]], when: Optional[str] = None) -> int:
    """
    The Ships tab's Onboard These Ships (1.7.2 plan, 32.1): several ships of one hull get one
    fitting (None: <Personal>), one owner and one Home in a single step, no doctrine needed.
    ships: rows with item_id, type_id, custom_name and system_id. home None leaves each ship
    its Home, or its own system when it has none (H2). Returns how many ships were onboarded.
    """
    when = when or datetime.now(timezone.utc).isoformat(timespec="seconds")
    ships = list(ships)
    for ship in ships:
        if fit_uid is None:
            designations.mark_personal(ship.item_id, ship.type_id, holder, ship.custom_name, when)
        else:
            designations.assign(ship.item_id, ship.type_id, fit_uid, holder, ship.custom_name, when,
                                system_id=ship.system_id)
    ids = [ship.item_id for ship in ships]
    if owner:
        designations.set_owner(ids, owner)
    if home and fit_uid is not None:
        designations.set_home(ids, home)
    return len(ships)


def apply_adoption(plan: SystemShips, chosen: Iterable[int], designations: Any) -> int:
    """The chosen ships' Home becomes the system; only ships whose fitting is required here (D3)."""
    allowed = {s.item_id for s in plan.adopt if s.required}
    return designations.set_home([i for i in chosen if i in allowed], {"system_id": plan.system_id})
