"""
The Fittings tab's tree (UI rework step 6.3): saved fittings grouped by ship
class, then hull, so a long list can be collapsed.

    groups = FittingTreeService(sde_loader).group(fitting_manager.list_fittings())
    # [("Carrier", [("Archon", [record, ...]), ...]), ...]

A class is the hull's SDE group name (Frigate, Heavy Assault Cruiser, Carrier...).
Classes, hulls and fittings are sorted by name, ignoring case. A fitting whose
hull can't be found in the SDE goes under UNKNOWN_CLASS.
"""
from typing import Any, Dict, List, Optional, Tuple

UNKNOWN_CLASS = "Unknown"

Groups = List[Tuple[str, List[Tuple[str, List[Dict[str, Any]]]]]]


def fitting_label(record: Dict[str, Any]) -> str:
    return record.get("fit_name") or record.get("hull") or "Unknown Fitting"


def filter_groups(groups: Groups, query: str) -> Groups:
    """
    The groups whose class, hull or fitting name contains the query, ignoring case.
    A matching class keeps all its hulls and a matching hull all its fittings;
    otherwise only the matching fittings are kept. An empty query keeps everything.
    """
    query = query.strip().casefold()
    if not query:
        return groups
    kept: Groups = []
    for class_name, hulls in groups:
        if query in class_name.casefold():
            kept.append((class_name, hulls))
            continue
        class_hulls = []
        for hull, records in hulls:
            if query not in hull.casefold():
                records = [r for r in records if query in fitting_label(r).casefold()]
            if records:
                class_hulls.append((hull, records))
        if class_hulls:
            kept.append((class_name, class_hulls))
    return kept


class FittingTreeService:
    def __init__(self, sde_loader: Any):
        self.sde = sde_loader
        self._class_names: Dict[Any, str] = {}          # hull type ID or name -> class, per instance

    def class_of(self, record: Dict[str, Any]) -> str:
        hull_type_id = record.get("hull_type_id")
        key = hull_type_id or record.get("hull")
        if key in self._class_names:
            return self._class_names[key]
        name = self._lookup_class(hull_type_id, record.get("hull"))
        self._class_names[key] = name
        return name

    def _lookup_class(self, hull_type_id: Optional[Any], hull: Optional[str]) -> str:
        try:
            if not hull_type_id and hull:
                hull_type_id = self.sde.get_typeid_by_name(hull)
            if not hull_type_id:
                return UNKNOWN_CLASS
            group_id = self.sde.get_type_group(int(hull_type_id))
            group = self.sde.get_group_data(group_id) if group_id is not None else None
            return (group or {}).get("groupName") or UNKNOWN_CLASS
        except Exception:
            return UNKNOWN_CLASS

    def group(self, fittings: List[Dict[str, Any]]) -> Groups:
        tree: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
        for record in fittings:
            hull = record.get("hull") or "Unknown Hull"
            tree.setdefault(self.class_of(record), {}).setdefault(hull, []).append(record)

        def by_name(text: str) -> str:
            return text.casefold()

        return [(class_name,
                 [(hull, sorted(records, key=lambda r: by_name(fitting_label(r))))
                  for hull, records in sorted(tree[class_name].items(), key=lambda h: by_name(h[0]))])
                for class_name in sorted(tree, key=lambda c: (c == UNKNOWN_CLASS, by_name(c)))]
