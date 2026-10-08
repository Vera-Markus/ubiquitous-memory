"""
How library fittings and a pilot's in-game fits line up (ESI features plan 29.2-29.4).

A game fit belongs to a library fitting by its marker (`[EFMT <fit_uid> v<version>]`, D4.6) or,
without one, by having the same hull and contents (the library's duplicate check, identical-stat
twins counted as the same item).
"""
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from app.esi_service.game_fittings import from_game, read_marker
from app.loaders.fitting_manager import fittingManager

SAVED, OLDER, SAME, NOT_SAVED = "saved", "older", "same", "not saved"


@dataclass
class Standing:
    """A library fitting against one pilot's game fits: its state, and the game fits behind it."""
    state: str                  # SAVED, OLDER, SAME or NOT_SAVED
    fits: List[dict]            # the matching game fits (OLDER: the older marked copies)

    def text(self, fitting: dict) -> str:
        version = fitting.get("version", 1)
        if self.state == SAVED:
            return f"Saved (v{version})"
        if self.state == OLDER:
            older = ", ".join(f"v{read_marker(f.get('description', ''))[1]}" for f in self.fits)
            return f"Older copy saved ({older}; the library has v{version})"
        if self.state == SAME:
            return "Saved (same hull and modules, no marker)"
        return "Not saved"


def standing(fitting: dict, game_fits: List[dict], type_name: Callable[[int], str],
             key: Optional[Callable[[int], int]] = None) -> Standing:
    uid, version = int(fitting["fit_uid"]), int(fitting.get("version", 1))
    marked = [(f, read_marker(f.get("description", ""))) for f in game_fits]
    current = [f for f, m in marked if m and m[0] == uid and m[1] >= version]
    if current:
        return Standing(SAVED, current)
    older = [f for f, m in marked if m and m[0] == uid]
    if older:
        return Standing(OLDER, older)
    wanted = fittingManager.fit_signature(fitting, key)
    same = [f for f, m in marked if m is None and fittingManager.fit_signature(from_game(f, type_name), key) == wanted]
    return Standing(SAME, same) if same else Standing(NOT_SAVED, [])


def library_match(game_fit: dict, library: List[dict], type_name: Callable[[int], str],
                  key: Optional[Callable[[int], int]] = None) -> Optional[dict]:
    """The library fitting a game fit is (by marker, then by contents), or None: for Import from Game."""
    marked = read_marker(game_fit.get("description", ""))
    if marked:
        found = next((f for f in library if f.get("fit_uid") == marked[0]), None)
        if found is not None:
            return found
    wanted = fittingManager.fit_signature(from_game(game_fit, type_name), key)
    return next((f for f in library if fittingManager.fit_signature(f, key) == wanted), None)


def holders_of_older(fitting: dict, fits_by_pilot: Dict[str, List[dict]]) -> Dict[str, List[dict]]:
    """{pilot: their older marked copies} of a fitting just changed (29.4)."""
    uid, version = int(fitting["fit_uid"]), int(fitting.get("version", 1))
    out = {}
    for pilot, fits in fits_by_pilot.items():
        older = [f for f in fits if (read_marker(f.get("description", "")) or (None, None))[0] == uid
                 and read_marker(f["description"])[1] < version]
        if older:
            out[pilot] = older
    return out
