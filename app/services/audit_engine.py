"""
The doctrine audit (design: docs/DOCTRINE_METADATA_V2.md §9).

For each role requirement (one fitting at one location), every ship of the
fitting's hull at that location is audited and listed (D13). Each ship is
asked two questions:

1. Is everything the fitting needs on board? Only a genuine shortfall fails.
2. Is each item where the fitting puts it? Anything else is a refit warning.

The parts live in app/services/audit/; this class finds the ships and puts
the answers together.

Assigned ships (UI_THOUGHTS_PLAN.md 18.3, replacing Phase 14's bindings): with a
TrackingContext (the app always passes one), a requirement's ships are those the character
owns that are assigned its fitting in the Ships tab, followed by item ID wherever they are
(another linked character, a corporation hangar, aboard another ship): HOME, AWAY or
MISSING. Nothing is ever assigned automatically; an unassigned hull at the place makes the
requirement NOT CHECKED. Without a context (tools, older tests) every hull of the type at
the place is audited, as before assignments existed.
"""
import logging
from typing import Any, Dict, List, Optional

from app.services import esi_features
from app.services.skill_requirements import SkillRequirements
from app.loaders.role_manager import fitting_in_use, requirement_priority
from app.loaders.ship_designations import home_system
from app.loaders.sde_rules import SdeRules
from app.models.asset_models import AuditSnapshot, ShipAsset
from app.models.audit_models import (AuditResult, EftItem, ItemShortfall, PackedShipWarning, RequirementResult, RequirementStatus,
                                     ShipRequirementResult)
from app.services.audit.bays import BayContext, escape_covered_carriers, evaluate_bays
from app.services.audit.carried import Expected, check_carried_ships, pooled
from app.services.audit.configuration import evaluate_configuration, strict_locations
from app.services.audit.expectations import FIGHTER_TUBES, ExpectedItem, Expectations, build_expectations
from app.models.bay_registry import BAYS, CATEGORY_SHIP
from app.services.audit.homes import DEPLOYED, HOME, IN_SYSTEM, Placed, share_out
from app.services.audit.inventory import AboardItem, evaluate_inventory, items_aboard
from app.services.audit.ranking import listing_order, requirement_status, ship_status
from app.services.implant_audit import ImplantNeed, audit_implants
from app.services.implant_rules import is_implant_set
from app.services.requirement_priority import covering, effective_priority

logger = logging.getLogger("AuditEngine")


class AuditEngine:
    """
    Evaluates a character's AuditSnapshot against Doctrine and Role requirements.
    """

    def __init__(self, doctrine_manager, role_manager, fitting_manager, evedb_loader, sde_rules: Optional[SdeRules] = None):
        self._skill_requirements: Optional[SkillRequirements] = None      # loaded on first use (plan 26.5)
        self.doctrine_manager = doctrine_manager
        self.role_manager = role_manager
        self.fitting_manager = fitting_manager
        self.evedb_loader = evedb_loader
        self._rules = sde_rules
        self._systems: Dict[int, int] = {}       # location ID -> its solar system

    @property
    def rules(self) -> SdeRules:
        """SDE rules (equivalents, variants, squadrons), opened on first use."""
        if self._rules is None:
            self._rules = SdeRules(self.evedb_loader.db_path)
        return self._rules

    def audit(self, snapshot: AuditSnapshot, tracking: Optional[Any] = None) -> List[AuditResult]:
        """
        Performs a full audit of all assigned roles for the character in the snapshot.
        With a TrackingContext, requirements follow their bound ships (plan 14.3).
        """
        results = []
        implant_sets = self._audit_implant_sets(snapshot)
        # Where each fitting is needed across the character's roles: a ship at another of those places
        # serves that requirement, so it isn't also offered as away for this one (as D2 did for bindings).
        self._homes: Dict[int, List[tuple]] = {}
        for role_uid in snapshot.assigned_role_uids:
            for req in (self.role_manager.get_role(role_uid) or {}).get('requirements', []):
                self._homes.setdefault(fitting_in_use(req), []).append(
                    (req['req_uid'], req.get('system_id'), req.get('location_id')))
        self._shared = self._share_homes(snapshot, tracking)
        for role_uid in snapshot.assigned_role_uids:
            role_data = self.role_manager.get_role(role_uid)
            if not role_data:
                continue
            doctrine_info = self._get_doctrine_info(role_uid)
            results.append(self._audit_role(snapshot, role_data, doctrine_info, tracking, implant_sets))
        if tracking is not None and tracking.designations is not None:
            tracking.designations.save()            # where each assigned ship was seen
        logger.debug(f"Audited {len(results)} role(s) for character {snapshot.character_id}")
        return results

    def _share_homes(self, snapshot: AuditSnapshot, tracking: Optional[Any]) -> Dict[int, List[Placed]]:
        """
        The character's ships with a Home, shared out among their Home system's requirements for
        the same fitting by where they're parked (23.3, H10; app/services/audit/homes.py).
        Returns req_uid -> the ships listed under it. Any system requirements aren't shared:
        they take every ship of the fitting (H8).
        """
        if tracking is None or tracking.designations is None:
            return {}
        owner = {"kind": "character", "id": int(snapshot.character_id)}
        groups: Dict[tuple, List[tuple]] = {}
        for role_uid in snapshot.assigned_role_uids:
            role = self.role_manager.get_role(role_uid) or {}
            for req in role.get('requirements', []):
                if req.get('system_id') is not None:
                    groups.setdefault((fitting_in_use(req), req['system_id']), []).append((req, role))
        shared: Dict[int, List[Placed]] = {}
        for (fit_uid, system_id), entries in groups.items():
            ships = []
            for d in tracking.designations.assigned_to(fit_uid, owner):
                if home_system(d.get("home")) == system_id:
                    sighting = tracking.universe.find(d["item_id"])
                    ships.append((d, sighting if sighting is not None and tracking.universe.is_ship(sighting) else None))
            if not ships:
                continue
            requirements = list({req['req_uid']: req for req, _ in entries}.values())
            covered = {req['req_uid'] for req, role in entries if covering(role, req) is not None}
            shared.update(share_out(
                system_id, requirements, ships, lambda r: r['req_uid'] in covered,
                lambda r: self.evedb_loader.location_label(r['location_id'], r.get('location_name'))
                if r.get('location_id') else ""))
        return shared

    def check_carried_ships(self, snapshot: AuditSnapshot, results: Optional[List[AuditResult]] = None) -> List[PackedShipWarning]:
        """
        Ships still packed inside other ships (design §9.5). Called once per
        character after audit(); packed ships are warnings, never failures.
        What each carrier's matched fittings ask for is exempt.
        """
        fittings_by_carrier = self._fittings_by_carrier(results or [])
        ships = {s.asset.item_id: s for s in snapshot.ships}
        checked: Dict[tuple, Optional[tuple]] = {}

        def fit_check(carried, fit_uid):
            """None when the carried ship matches the saved fitting (a deleted fitting accepts any)."""
            if (carried.item_id, fit_uid) not in checked:
                checked[(carried.item_id, fit_uid)] = self._carried_fit_difference(ships.get(carried.item_id), fit_uid)
            return checked[(carried.item_id, fit_uid)]

        return check_carried_ships(snapshot, results or [], self._expected_carried(fittings_by_carrier),
                                   escape_covered_carriers(fittings_by_carrier),
                                   self._carried_fit_entries(fittings_by_carrier), fit_check)

    def _carried_fit_entries(self, fittings_by_carrier: Dict[int, List[Dict[str, Any]]]) -> Dict[tuple, list]:
        """(carrier, hull) -> its maintenance bay entries (fit_uid or None, quantity), from the fitting
        asking for the most of that hull (as _expected_carried does)."""
        found: Dict[tuple, list] = {}
        for carrier, fittings in fittings_by_carrier.items():
            for fitting in fittings:
                entries: Dict[int, list] = {}
                for r in ((fitting.get("doctrine_metadata") or {}).get("bays") or {}).get("ship_maintenance_bay") or []:
                    if isinstance(r, dict) and r.get("type_id") and (r.get("match") or "type") in ("type", "fit"):
                        uid = r.get("fit_uid") if r.get("match") == "fit" else None
                        entries.setdefault(int(r["type_id"]), []).append((uid, int(r.get("min_quantity", 1) or 0)))
                for type_id, listed in entries.items():
                    key = (carrier, type_id)
                    if sum(q for _, q in listed) > sum(q for _, q in found.get(key, [])):
                        found[key] = listed
        return found

    def _carried_fit_difference(self, ship: Optional[ShipAsset], fit_uid: int) -> Optional[tuple]:
        """(fitting name, what differs) when a carried ship doesn't match a saved fitting; None when it does."""
        fitting = self.fitting_manager.get_fitting(fit_uid)
        if fitting is None:
            return None
        name = fitting.get("fit_name", f"fitting {fit_uid}")
        if ship is None:
            return name, "packaged"
        result = self._evaluate_ship(ship, build_expectations(fitting, self.rules.squadron_size), fitting)
        if result.status == RequirementStatus.PASS:
            return None
        if result.shortfalls:
            missing = [f"{s.missing}× {s.name}" for s in result.shortfalls[:3]]
            more = len(result.shortfalls) - 3
            return name, "missing " + ", ".join(missing) + (f" and {more} more" if more > 0 else "")
        if result.status == RequirementStatus.WARN:
            return None                     # a refit or bling: still that fitting, as for any ship
        return name, "doesn't match"

    def _fittings_by_carrier(self, results: List[AuditResult]) -> Dict[int, List[Dict[str, Any]]]:
        """Each audited ship's matched fittings (a ship can match requirements in several roles, D14)."""
        found: Dict[int, List[Dict[str, Any]]] = {}
        for result in results:
            for requirement in result.requirement_results:
                fitting = self.fitting_manager.get_fitting(fitting_in_use(requirement.requirement_details))
                if not fitting:
                    continue
                for ship in requirement.ship_results:
                    if ship.ship_item_id is not None:
                        found.setdefault(ship.ship_item_id, []).append(fitting)
        return found

    def _expected_carried(self, fittings_by_carrier: Dict[int, List[Dict[str, Any]]]) -> Expected:
        """
        (carrier, bay, ship type) -> how many ships the carrier's metadata asks
        for there; the highest across its fittings. Ship types asked for in the
        fleet hangar also cover the cargo (one pooled space, D2).
        """
        expected: Expected = {}
        for carrier, fittings in fittings_by_carrier.items():
            for fitting in fittings:
                bays = (fitting.get("doctrine_metadata") or {}).get("bays") or {}
                for bay_key, requirements in bays.items():
                    if bay_key not in BAYS or bay_key == "escape_bay":
                        continue
                    totals: Dict[int, int] = {}
                    for r in requirements or []:
                        if isinstance(r, dict) and r.get("type_id") and (r.get("match") or "type") in ("type", "fit"):
                            totals[int(r["type_id"])] = totals.get(int(r["type_id"]), 0) + int(r.get("min_quantity", 1) or 0)
                    for type_id, quantity in totals.items():
                        if bay_key != "ship_maintenance_bay" and self.rules.category(type_id) != CATEGORY_SHIP:
                            continue
                        key = (carrier, pooled(bay_key), type_id)
                        expected[key] = max(expected.get(key, 0), quantity)
        return expected

    def _get_doctrine_info(self, role_uid: int) -> Dict[str, Any]:
        """
        Determines if a role is part of a doctrine and returns its info.
        """
        doctrine = self.doctrine_manager.get_doctrine_by_role_uid(role_uid)
        if doctrine:
            return {
                "uid": doctrine['doctrine_uid'],
                "name": doctrine['doctrine_name']
            }
        return {}

    def _audit_implant_sets(self, snapshot: AuditSnapshot) -> Dict[Any, RequirementResult]:
        """
        Every implant set requirement in the character's roles (Capsule fittings, D5), matched to
        their clones together: each clone covers one requirement across all roles (D7, plan 15.2).
        """
        needs = []
        for role_uid in sorted(snapshot.assigned_role_uids):
            for req in (self.role_manager.get_role(role_uid) or {}).get('requirements', []):
                fitting = self.fitting_manager.get_fitting(fitting_in_use(req))
                if is_implant_set(fitting):
                    needs.append(ImplantNeed((role_uid, req['req_uid']), req, fitting))
        if not needs:
            return {}
        return audit_implants(needs, snapshot.clones, self.rules, self._system_of,
                              lambda location_id: self.evedb_loader.location_label(location_id))

    def _audit_role(self, snapshot: AuditSnapshot, role_data: Dict[str, Any], doctrine_info: Dict[str, Any],
                    tracking: Optional[Any] = None,
                    implant_sets: Optional[Dict[Any, RequirementResult]] = None) -> AuditResult:
        """
        Audits a single role. Implant set requirements come already matched to clones.
        """
        implant_sets = implant_sets or {}
        requirements = []
        for req in role_data.get('requirements', []):
            key = (role_data['role_uid'], req['req_uid'])
            if key in implant_sets:
                result = implant_sets[key]
            elif tracking is not None:
                result = self._evaluate_assigned(snapshot, req, tracking)
            else:
                result = self._evaluate_requirement(snapshot, req)
            result.priority = effective_priority(role_data, req)
            if requirement_priority(req) == "hard":
                result.covered_by = covering(role_data, req)
            self._check_skills(snapshot, req, result)
            requirements.append(result)
        return AuditResult(
            character_id=snapshot.character_id,
            role_uid=role_data['role_uid'],
            role_name=role_data['role_name'],
            doctrine_uid=doctrine_info.get("uid"),
            doctrine_name=doctrine_info.get("name"),
            requirement_results=requirements,
        )

    # --- the skill check (ESI features plan 26.5) --------------------------------------------

    def skill_requirements(self) -> SkillRequirements:
        if self._skill_requirements is None:
            self._skill_requirements = SkillRequirements(self.evedb_loader.db_path)
        return self._skill_requirements

    def _check_skills(self, snapshot: AuditSnapshot, req: Dict[str, Any], result: RequirementResult) -> None:
        """Can the pilot fly the requirement's fitting? Not for implant sets (their clones are the check)."""
        if not esi_features.enabled("skills") or result.implant_set is not None:
            return
        fitting = self.fitting_manager.get_fitting(fitting_in_use(req))
        if fitting is None:
            return
        if snapshot.skills is None:
            result.skill_state = "unchecked"
            return
        result.skill_state = "checked"
        result.skills = self.skill_requirements().check_fitting(fitting, snapshot.skills)

    # --- one requirement -----------------------------------------------------------------

    def _evaluate_requirement(self, snapshot: AuditSnapshot, requirement: Dict[str, Any]) -> RequirementResult:
        """
        Audits every ship of the fitting's hull at the requirement's location (D13, D16).
        A requirement the pilot replaced is audited with the replacement fitting (step 11.1).
        """
        req_uid = requirement['req_uid']
        fit_uid = fitting_in_use(requirement)
        replaced = fit_uid != requirement['fit_uid']
        system_id = requirement.get('system_id')         # None: any system
        location_id = requirement.get('location_id')     # None: any station or structure

        fitting = self.fitting_manager.get_fitting(fit_uid)
        if fitting is None:
            which = "replacement fitting" if replaced else "fitting"
            return RequirementResult(req_uid=req_uid, status=RequirementStatus.FAIL, requirement_details=requirement,
                                     message=f"The {which} for this requirement (UID {fit_uid}) no longer exists")

        hull_type_id = (None if replaced else requirement.get('expected_hull_type_id')) or fitting.get('hull_type_id')
        ships = [ship for ship in snapshot.ships
                 if ship.asset.type_id == hull_type_id and self._location_matches(ship, system_id, location_id)]
        if not ships:
            if location_id:
                where = self.evedb_loader.location_label(location_id, requirement.get('location_name'))
            else:
                where = self.evedb_loader.get_system_name(system_id) if system_id else None
            hull = fitting.get('hull') or "ship"
            return RequirementResult(req_uid=req_uid, status=RequirementStatus.FAIL, requirement_details=requirement,
                                     shortfalls=self._replacement(fitting, hull_type_id),
                                     message=f"No {hull} in {where}" if where else f"No {hull} found")

        expectations = build_expectations(fitting, self.rules.squadron_size)
        context = BayContext(carried=list(snapshot.carried_ships),
                             ships_by_item={s.asset.item_id: s for s in snapshot.ships},
                             get_fitting=self.fitting_manager.get_fitting)
        results = listing_order([self._evaluate_ship(ship, expectations, fitting, context) for ship in ships])
        status = requirement_status(results)
        return RequirementResult(
            req_uid=req_uid,
            status=status,
            requirement_details=requirement,
            ship_results=results,
        )

    def _evaluate_assigned(self, snapshot: AuditSnapshot, requirement: Dict[str, Any],
                           tracking: Any) -> RequirementResult:
        """
        The requirement with the ships assigned to it (UI thoughts plan 18.3; A1-A4): ships the
        character owns that are assigned the fitting in use, followed by item ID wherever they
        are. At the requirement's place: audited, best first (the requirement takes the best,
        A3). Elsewhere: AWAY (WARN, ready unless its fit fails, D1). Found nowhere: MISSING
        (FAIL, last seen, the replacement on its row, D10). Corporation-owned ships never count
        for a character (U5). With none assigned but an unassigned hull of the type held by the
        character at the place: NOT CHECKED (A4), offering to assign it.
        """
        req_uid = requirement['req_uid']
        fit_uid = fitting_in_use(requirement)
        replaced = fit_uid != requirement['fit_uid']
        system_id, location_id = requirement.get('system_id'), requirement.get('location_id')
        fitting = self.fitting_manager.get_fitting(fit_uid)
        if fitting is None:
            return self._evaluate_requirement(snapshot, requirement)       # the "no longer exists" failure
        hull_type_id = (None if replaced else requirement.get('expected_hull_type_id')) or fitting.get('hull_type_id')
        owner = {"kind": "character", "id": int(snapshot.character_id)}
        universe, designations = tracking.universe, tracking.designations

        expectations = build_expectations(fitting, self.rules.squadron_size)
        own_ships = {s.asset.item_id: s for s in snapshot.ships}
        own_context = BayContext(carried=list(snapshot.carried_ships), ships_by_item=own_ships,
                                 get_fitting=self.fitting_manager.get_fitting)
        home, away, missing, misplaced = [], [], [], []

        def found(d, sighting) -> Optional[ShipRequirementResult]:
            """The ship audited where it is, or None when it can't be (then it counts as missing)."""
            if sighting is None or not universe.is_ship(sighting):
                return None
            if sighting.holder == {"kind": "character", "id": owner["id"]} and d["item_id"] in own_ships:
                ship, context = own_ships[d["item_id"]], own_context
            else:
                ship, carried, ships = tracking.ships_of(sighting)
                context = BayContext(carried=carried, ships_by_item=ships, get_fitting=self.fitting_manager.get_fitting)
            if ship is None:
                return None
            result = self._evaluate_ship(ship, expectations, fitting, context)
            result.bound = True
            result.holder = tracking.holder_info(sighting)
            result.where = tracking.where(sighting)
            designations.record_sighting(d["item_id"], result.where, result.holder)
            return result

        def lost(d) -> ShipRequirementResult:
            last = {"where": d.get("seen_where", ""), "holder": d.get("seen_holder")}
            return ShipRequirementResult(
                ship_name=fitting.get('hull') or "ship", status=RequirementStatus.FAIL,
                custom_name=d.get("custom_name") or None, ship_item_id=d["item_id"], placement="MISSING",
                bound=True, holder=last["holder"], where=last["where"], last_seen=last if last["where"] else None,
                missing_since=d.get("last_seen"), shortfalls=self._replacement(fitting, hull_type_id))

        # Ships with no Home (assigned before Homes, H7): as before Homes. A system requirement's
        # ships with a Home were shared out among the system's requirements (_share_homes, 23.3).
        for d in designations.assigned_to(fit_uid, owner) if designations is not None else []:
            if system_id is not None and d.get("home"):
                continue
            sighting = universe.find(d["item_id"])
            if sighting is not None and universe.is_ship(sighting) and not universe.at(sighting, system_id, location_id) \
                    and any(universe.at(sighting, s, l) for r, s, l in getattr(self, "_homes", {}).get(fit_uid, [])
                            if r != req_uid):
                continue            # at the place of another requirement for this fitting: it serves that one
            result = found(d, sighting)
            if result is not None:
                result.adopt_system_id = sighting.system_id         # no Home yet: Adopt makes this one (23.4)
            if result is None:
                missing.append(lost(d))
            elif universe.at(sighting, system_id, location_id):
                result.placement = "HOME"
                home.append(result)
            else:
                result.placement = "AWAY"
                away.append(result)
        for placed in getattr(self, "_shared", {}).get(req_uid, []):
            result = found(placed.designation, placed.sighting)
            if result is None:
                missing.append(lost(placed.designation))
                continue
            result.placement = placed.placement
            if placed.placement == IN_SYSTEM:
                result.placement_note = f"move it to {self.evedb_loader.location_label(location_id, requirement.get('location_name'))}"
            elif placed.placement == DEPLOYED:
                result.placement_note = f"bring it back to {self.evedb_loader.get_system_name(system_id)}"
            (home if placed.placement == HOME else misplaced).append(result)

        home, away, misplaced = listing_order(home), listing_order(away), listing_order(misplaced)
        results = home + misplaced + away + missing
        message, shortfalls, is_misplaced = None, [], False
        hull = fitting.get('hull') or "ship"
        # Check before buying (23.4, H5): hulls the character holds with no fitting (not <Personal>),
        # anywhere in the requirement's system (anywhere at all for an Any system one).
        unassigned = [s for s in snapshot.ships
                      if s.asset.type_id == hull_type_id and self._location_matches(s, system_id, None)
                      and (designations is None or designations.get(s.asset.item_id) is None)]
        unassigned_hulls = [{"item_id": s.asset.item_id, "type_id": s.asset.type_id,
                             "custom_name": s.asset.custom_name or "", "fit_uid": fit_uid,
                             "fit_name": fitting.get('fit_name', ''),
                             "system_id": self._system_of(s.root_location_id or s.asset.location_id)}
                            for s in unassigned]
        if system_id:
            where = self.evedb_loader.get_system_name(system_id)
        else:
            where = ""
        found_here = (f"{len(unassigned)} {hull}s" if len(unassigned) > 1 else hull) + \
            (f" at {where}" if where else "") + " with no fitting assigned"

        if home:
            status = requirement_status(home)
        elif misplaced:
            # The ships are there, just not here: a move, not a purchase (H9).
            status, is_misplaced = RequirementStatus.FAIL, True
            note = misplaced[0].placement_note
            message = note[:1].upper() + note[1:]
        elif away:
            status = RequirementStatus.FAIL if away[0].status == RequirementStatus.FAIL else RequirementStatus.WARN
        elif missing:
            since = missing[0].missing_since or ""
            message = f"Missing since {since[:10]}" + (f": last seen {missing[0].where}" if missing[0].where else "")
            if unassigned:
                # Maybe its replacement, already bought: assign it rather than buy another (H5).
                return RequirementResult(req_uid=req_uid, status=RequirementStatus.NOT_CHECKED,
                                         requirement_details=requirement, ship_results=results,
                                         message=f"{found_here}: not checked", unassigned_hulls=unassigned_hulls)
            status = RequirementStatus.FAIL
            shortfalls = self._replacement(fitting, hull_type_id)
        else:
            if location_id:
                where = self.evedb_loader.location_label(location_id, requirement.get('location_name'))
            if not unassigned:
                return RequirementResult(req_uid=req_uid, status=RequirementStatus.FAIL, requirement_details=requirement,
                                         shortfalls=self._replacement(fitting, hull_type_id),
                                         message=f"No {hull} in {where}" if where else f"No {hull} found")
            return RequirementResult(
                req_uid=req_uid, status=RequirementStatus.NOT_CHECKED, requirement_details=requirement,
                message=f"{found_here}: not checked", unassigned_hulls=unassigned_hulls)
        return RequirementResult(req_uid=req_uid, status=status, requirement_details=requirement,
                                 ship_results=results, message=message, shortfalls=shortfalls, misplaced=is_misplaced)

    @staticmethod
    def _replacement(fitting: Dict[str, Any], hull_type_id: Optional[int]) -> List[ItemShortfall]:
        """
        Everything a replacement ship needs when none is at the location: the hull,
        every item its fitting and metadata expect aboard (modules, drones, fighters,
        cargo, fleet hangar stock, fuel), and the ships its maintenance bay must hold.
        The escape ship isn't included: escape bay problems only ever warn (D8).
        """
        needs: Dict[int, List] = {}
        if hull_type_id:
            needs[hull_type_id] = [fitting.get("hull") or str(hull_type_id), 1]
        for items in build_expectations(fitting).values():
            for item in items.values():
                needs.setdefault(item.type_id, [item.name, 0])[1] += item.quantity
        bays = (fitting.get("doctrine_metadata") or {}).get("bays") or {}
        for requirement in bays.get("ship_maintenance_bay") or []:
            if isinstance(requirement, dict) and requirement.get("type_id"):
                entry = needs.setdefault(int(requirement["type_id"]), [requirement.get("name", ""), 0])
                entry[1] += int(requirement.get("min_quantity", 1) or 0)
        return [ItemShortfall(type_id, name, quantity, 0) for type_id, (name, quantity) in needs.items() if quantity > 0]

    # --- one ship ------------------------------------------------------------------------

    def audit_ship(self, ship: ShipAsset, fitting: Dict[str, Any], context: Optional[BayContext] = None) -> ShipRequirementResult:
        """One ship against one fitting: both questions and the bays (the Ships tab, plan 16.2)."""
        return self._evaluate_ship(ship, build_expectations(fitting, self.rules.squadron_size), fitting, context)

    def audit_container(self, item_id: int, name: str, custom_name: str, location_id: Optional[int],
                        aboard: List[AboardItem], fitting: Dict[str, Any]) -> ShipRequirementResult:
        """
        A container against a fitting (1.7.4): a container is just cargo, so everything the fitting
        lists (modules, charges, drones; not the hull) is expected in it as one pool.
        """
        expectations: Expectations = {"cargo": {}}
        cargo = expectations["cargo"]
        for items in build_expectations(fitting).values():
            for item in items.values():
                previous = cargo.get(item.type_id)
                cargo[item.type_id] = ExpectedItem(item.type_id, item.name,
                                                   item.quantity + (previous.quantity if previous else 0))
        inventory = evaluate_inventory(expectations, aboard, self.rules)
        result = ShipRequirementResult(
            ship_name=name,
            status=RequirementStatus.PASS,
            custom_name=custom_name,
            unexpected_items=[n for _, _, n, _ in inventory.unexpected],
            ship_item_id=item_id,
            location_id=location_id,
            shortfalls=inventory.shortfalls,
            substitutions=inventory.substitutions,
            contents=[EftItem(i.location, i.type_id, self.rules.equivalence_key(i.type_id), i.name, i.quantity,
                              i.loaded) for i in aboard],
            expected=[EftItem("cargo", item.type_id, self.rules.equivalence_key(item.type_id), item.name, item.quantity)
                      for item in cargo.values()],
            unexpected_aboard=[EftItem(location, type_id, self.rules.equivalence_key(type_id), n, quantity)
                               for location, type_id, n, quantity in inventory.unexpected],
        )
        result.status = ship_status(result)
        return result

    def _evaluate_ship(self, ship: ShipAsset, expectations: Expectations, fitting: Optional[Dict[str, Any]] = None,
                       context: Optional[BayContext] = None) -> ShipRequirementResult:
        aboard = items_aboard(ship, self.rules, split_tubes=FIGHTER_TUBES in expectations)
        inventory = evaluate_inventory(expectations, aboard, self.rules)
        moves = evaluate_configuration(expectations, aboard, inventory, self.rules)
        # A fitted item the fitting doesn't call for appears as a "remove" move, not here.
        strict = strict_locations(expectations)
        unexpected = [name for location, _, name, _ in inventory.unexpected if location not in strict]

        result = ShipRequirementResult(
            ship_name=ship.asset.name,
            status=RequirementStatus.PASS,
            custom_name=ship.asset.custom_name,
            unexpected_items=unexpected,
            ship_item_id=ship.asset.item_id,
            location_id=ship.root_location_id or ship.asset.location_id,
            carrier_item_id=ship.carrier_item_id,
            shortfalls=inventory.shortfalls,
            refit_moves=moves,
            substitutions=inventory.substitutions,
            bay_results=evaluate_bays(ship, fitting or {}, inventory, moves, context or BayContext(), self.rules),
            contents=[EftItem(i.location, i.type_id, self.rules.equivalence_key(i.type_id), i.name, i.quantity,
                              i.loaded) for i in aboard],
            expected=[EftItem(location, item.type_id, self.rules.equivalence_key(item.type_id), item.name, item.quantity)
                      for location, items in expectations.items() for item in items.values()],
            unexpected_aboard=[EftItem(location, type_id, self.rules.equivalence_key(type_id), name, quantity)
                               for location, type_id, name, quantity in inventory.unexpected],
            unverified=ship.asset.manual_at,
        )
        result.status = ship_status(result)
        return result

    # --- locations -------------------------------------------------------------------------

    def _location_matches(self, ship: ShipAsset, system_id: Optional[int], location_id: Optional[int]) -> bool:
        """
        Whether the ship is where the requirement wants it, by ID: at the station or
        structure, and in the solar system (None means any). A ship carried inside a
        capital (or a container) is at the outermost item's location.
        """
        if system_id is None and location_id is None:
            return True
        ship_location = ship.root_location_id or ship.asset.location_id
        if location_id is not None and ship_location != location_id:
            return False
        return system_id is None or self._system_of(ship_location) == system_id

    def _system_of(self, location_id: int) -> Optional[int]:
        """The location's solar system, remembered once known (a station or structure never moves)."""
        if location_id not in self._systems:
            system_id = self.evedb_loader.system_of(location_id)
            if system_id is None:
                return None               # maybe a structure the next pull names
            self._systems[location_id] = system_id
        return self._systems[location_id]
