"""
The Ships tab's data (plan step 16.2): who can be shown, and every ship they have.

A holder is a linked character, or a corporation that a linked Director pulled with full
read access (Phase 13), which then acts like a character. Each of the holder's assembled
ships is listed with where it is and, when it has been given a fitting (its designation,
ShipDesignations), how it audits against that fitting: the same two questions and bays as
the doctrine audit. Capsules and packaged hulls aren't listed (they can't be fitted).
"""
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

from app.models.audit_models import ShipRequirementResult
from app.services.audit.bays import BayContext
from app.services.audit.inventory import items_in_container


@dataclass
class ShipRow:
    item_id: int
    type_id: int
    hull: str
    custom_name: str
    location_id: Optional[int]          # the station, structure or system
    location: str                       # its name
    aboard: str                         # "hangar 2 (Doctrine Subcaps)", "ship maintenance bay of 'Big Brother'"
    carrier_item_id: Optional[int] = None       # the ship it's packed in (plan 19.1: nested under it)
    system_id: Optional[int] = None     # its solar system (a first assignment makes it the Home, H2)
    fit_uid: Optional[int] = None
    fit_name: str = ""
    owner: Optional[Dict[str, Any]] = None      # {kind, id}: who the ship belongs to (plan 18.2)
    owner_name: str = ""
    away_from_owner: bool = False               # held by someone other than its owner: give it back
    result: Optional[ShipRequirementResult] = None
    note: str = ""                      # why a designated ship wasn't audited
    home: Optional[Dict[str, Any]] = None       # {"system_id"} or {"anywhere": True}; None: no Home (H1)
    personal: bool = False                      # <Personal>: never audited (S5)
    loss: Optional[Dict[str, Any]] = None       # a lost ship's row (ESI features plan 30.3): {state, date, killmail_id}
    unverified: Optional[str] = None            # contents pasted from the game (1.7.4): when, until the next pull


def hangar_of(aboard: str) -> str:
    """The hangar a ship is in, or its carrier is in (plan 19.1): "Personal hangar", "Hangar 2 (Doctrine Subcaps)",
    "Deliveries", "Asset safety"; "In space" when it isn't in a station or structure."""
    first = aboard.split(" / ")[0] if aboard else ""
    if not first or " of '" in first:
        return "In space"
    if first == "hangar":
        return "Personal hangar"
    return first[:1].upper() + first[1:]


def bay_of(aboard: str) -> str:
    """Where aboard its carrier a carried ship is: "ship maintenance bay", "fleet hangar"."""
    last = aboard.split(" / ")[-1] if aboard else ""
    return last.split(" of '")[0] if " of '" in last else last


def holders(character_names: Dict[str, str], corporations: Iterable[dict]) -> List[Dict[str, Any]]:
    """Linked characters by name, then each pulled corporation: {kind, id, name, label}."""
    found = [{"kind": "character", "id": int(cid), "name": name, "label": name}
             for cid, name in sorted(character_names.items(), key=lambda c: (c[1] or "").casefold())]
    for corp in sorted(corporations, key=lambda c: (c.get("name") or "").casefold()):
        name = corp.get("name") or f"Corporation {corp['corporation_id']}"
        found.append({"kind": "corporation", "id": int(corp["corporation_id"]), "name": name,
                      "label": f"{name} (corporation)"})
    return found


def owner_choices(character_names: Dict[str, str], corporations: Iterable[dict],
                  memberships: Dict[str, dict]) -> List[Dict[str, Any]]:
    """
    What a ship's owner can be: the holders, then each linked character's corporation that
    no Director pulls (memberships: {character ID: {corporation_id, name}}), by name.
    """
    found = holders(character_names, corporations)
    pulled = {h["id"] for h in found if h["kind"] == "corporation"}
    for cid, corp in sorted(memberships.items(), key=lambda m: (m[1].get("name") or "").casefold()):
        if str(cid) in character_names and int(corp["corporation_id"]) not in pulled:
            pulled.add(int(corp["corporation_id"]))
            name = corp.get("name") or f"Corporation {corp['corporation_id']}"
            found.append({"kind": "corporation", "id": int(corp["corporation_id"]), "name": name,
                          "label": f"{name} (corporation)"})
    return found


def holder_name(holder: Optional[Dict[str, Any]], names: Dict[tuple, str]) -> str:
    if not holder:
        return ""
    return names.get((holder.get("kind"), int(holder.get("id") or 0))) or f"{holder.get('kind', '')} {holder.get('id')}"


def _set_owner(row: ShipRow, designations: Any, here: Dict[str, Any], names: Dict[tuple, str]) -> None:
    """The designated item's owner (the holder until changed) and whether someone else holds it."""
    owner = designations.owner(row.item_id) or here
    row.owner = {"kind": owner.get("kind"), "id": int(owner.get("id") or 0)}
    row.owner_name = holder_name(row.owner, names)
    row.away_from_owner = row.owner != here


def _row_for(sighting: Any, sde: Any) -> ShipRow:
    return ShipRow(item_id=sighting.item_id, type_id=sighting.type_id, hull=sde.get_type_name(sighting.type_id),
                   custom_name=sighting.custom_name, location_id=sighting.root_location_id,
                   system_id=sighting.system_id,
                   location=sde.location_label(sighting.root_location_id)
                   + (" (in space)" if sde.is_solar_system(sighting.root_location_id) else ""),
                   aboard=sighting.aboard,
                   carrier_item_id=sighting.carrier_item_id)


def container_rows(context: Any, holder: Dict[str, Any], designations: Any, fitting_manager: Any,
                   engine: Any, names: Optional[Dict[tuple, str]] = None) -> List[ShipRow]:
    """
    Every assembled container the holder has (1.7.4), as rows like ships': hull is the container
    type. One given a fitting is audited as cargo: everything in it is one pool, packaged or not.
    """
    sde = context.sde
    names = names or {}
    here = {"kind": holder["kind"], "id": int(holder["id"])}
    rows = []
    for sighting in context.universe.containers_held_by(here):
        row = _row_for(sighting, sde)
        fit_uid = designations.fit_uid(sighting.item_id) if designations is not None else None
        if fit_uid is not None:
            _set_owner(row, designations, here, names)
            fitting = fitting_manager.get_fitting(fit_uid)
            row.fit_uid = fit_uid
            row.home = designations.home(sighting.item_id)
            row.fit_name = (fitting or {}).get("fit_name", f"fitting {fit_uid}")
            if fitting is None:
                row.note = "Its fitting no longer exists"
            else:
                row.result = engine.audit_container(sighting.item_id, row.hull, row.custom_name,
                                                    sighting.root_location_id,
                                                    container_items(context, sighting, engine.rules), fitting)
        rows.append(row)
    rows.sort(key=lambda r: (r.location.casefold(), r.hull.casefold(), (r.custom_name or "").casefold(), r.item_id))
    return rows


def container_items(context: Any, sighting: Any, rules: Any) -> list:
    """What's in a container, one cargo stack per type (AboardItems)."""
    return items_in_container(context.contents_of(sighting), rules, context.sde.get_type_name)


def ship_rows(context: Any, holder: Dict[str, Any], designations: Any, fitting_manager: Any,
              engine: Any, names: Optional[Dict[tuple, str]] = None) -> List[ShipRow]:
    """
    Every assembled ship the holder has, sorted by place, hull and name, designated ones audited.
    names: (kind, id) -> name, for the Owner column.
    """
    sde = context.sde
    names = names or {}
    here = {"kind": holder["kind"], "id": int(holder["id"])}
    rows = []
    for sighting in context.universe.ships_held_by({"kind": holder["kind"], "id": int(holder["id"])}):
        row = _row_for(sighting, sde)
        row.unverified = getattr(context, "manual_at", {}).get(sighting.item_id)
        if designations is not None and designations.is_personal(sighting.item_id):
            row.personal = True
            _set_owner(row, designations, here, names)
            rows.append(row)
            continue
        fit_uid = designations.fit_uid(sighting.item_id) if designations is not None else None
        if fit_uid is not None:
            _set_owner(row, designations, here, names)
            fitting = fitting_manager.get_fitting(fit_uid)
            row.fit_uid = fit_uid
            row.home = designations.home(sighting.item_id)
            row.fit_name = (fitting or {}).get("fit_name", f"fitting {fit_uid}")
            if fitting is None:
                row.note = "Its fitting no longer exists"
            elif fitting.get("hull_type_id") not in (None, sighting.type_id):
                row.note = f"Its fitting is for another hull ({fitting.get('hull')})"
            else:
                ship, carried, ships = context.ships_of(sighting)
                if ship is not None:
                    bay_context = BayContext(carried=carried, ships_by_item=ships,
                                             get_fitting=fitting_manager.get_fitting)
                    row.result = engine.audit_ship(ship, fitting, bay_context)
        rows.append(row)
    rows.sort(key=lambda r: (r.location.casefold(), r.hull.casefold(), (r.custom_name or "").casefold(), r.item_id))
    return rows
