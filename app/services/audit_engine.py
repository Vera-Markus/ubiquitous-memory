"""
The doctrine audit (design: docs/DOCTRINE_METADATA_V2.md §9).

For each role requirement (one fitting at one location), every ship of the
fitting's hull at that location is audited and listed (D13). Each ship is
asked two questions:

1. Is everything the fitting needs on board? Only a genuine shortfall fails.
2. Is each item where the fitting puts it? Anything else is a refit warning.

The parts live in app/services/audit/; this class finds the ships and puts
the answers together.
"""
import logging
from typing import Any, Dict, List, Optional

from app.loaders.role_manager import fitting_in_use
from app.loaders.sde_rules import SdeRules
from app.models.asset_models import AuditSnapshot, ShipAsset
from app.models.audit_models import (AuditResult, ItemShortfall, PackedShipWarning, RequirementResult, RequirementStatus,
                                     ShipRequirementResult)
from app.services.audit.bays import BayContext, escape_covered_carriers, evaluate_bays
from app.services.audit.carried import Expected, check_carried_ships, pooled
from app.services.audit.configuration import evaluate_configuration
from app.services.audit.expectations import SLOT_LOCATIONS, Expectations, build_expectations
from app.models.bay_registry import BAYS, CATEGORY_SHIP
from app.services.audit.inventory import evaluate_inventory, items_aboard
from app.services.audit.ranking import listing_order, requirement_status, ship_status

logger = logging.getLogger("AuditEngine")


class AuditEngine:
    """
    Evaluates a character's AuditSnapshot against Doctrine and Role requirements.
    """

    def __init__(self, doctrine_manager, role_manager, fitting_manager, evedb_loader, sde_rules: Optional[SdeRules] = None):
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

    def audit(self, snapshot: AuditSnapshot) -> List[AuditResult]:
        """
        Performs a full audit of all assigned roles for the character in the snapshot.
        """
        results = []
        for role_uid in snapshot.assigned_role_uids:
            role_data = self.role_manager.get_role(role_uid)
            if not role_data:
                continue
            doctrine_info = self._get_doctrine_info(role_uid)
            results.append(self._audit_role(snapshot, role_data, doctrine_info))
        logger.debug(f"Audited {len(results)} role(s) for character {snapshot.character_id}")
        return results

    def check_carried_ships(self, snapshot: AuditSnapshot, results: Optional[List[AuditResult]] = None) -> List[PackedShipWarning]:
        """
        Ships still packed inside other ships (design §9.5). Called once per
        character after audit(); packed ships are warnings, never failures.
        What each carrier's matched fittings ask for is exempt.
        """
        fittings_by_carrier = self._fittings_by_carrier(results or [])
        return check_carried_ships(snapshot, results or [], self._expected_carried(fittings_by_carrier),
                                   escape_covered_carriers(fittings_by_carrier))

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
                        if isinstance(r, dict) and r.get("type_id") and (r.get("match") or "type") == "type":
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

    def _audit_role(self, snapshot: AuditSnapshot, role_data: Dict[str, Any], doctrine_info: Dict[str, Any]) -> AuditResult:
        """
        Audits a single role.
        """
        return AuditResult(
            character_id=snapshot.character_id,
            role_uid=role_data['role_uid'],
            role_name=role_data['role_name'],
            doctrine_uid=doctrine_info.get("uid"),
            doctrine_name=doctrine_info.get("name"),
            requirement_results=[self._evaluate_requirement(snapshot, req) for req in role_data.get('requirements', [])],
        )

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

        expectations = build_expectations(fitting)
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

    def _evaluate_ship(self, ship: ShipAsset, expectations: Expectations, fitting: Optional[Dict[str, Any]] = None,
                       context: Optional[BayContext] = None) -> ShipRequirementResult:
        aboard = items_aboard(ship, self.rules)
        inventory = evaluate_inventory(expectations, aboard, self.rules)
        moves = evaluate_configuration(expectations, aboard, inventory, self.rules)
        # A fitted item the fitting doesn't call for appears as an "unfit" move, not here.
        unexpected = [name for location, _, name, _ in inventory.unexpected if location not in SLOT_LOCATIONS]

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
