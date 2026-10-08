"""
In-game fitting sync windows (ESI features plan 29.2-29.5):

- ImportFromGameDialog (Fittings tab ▸ Import from Game…): a character's saved fits, a hull filter and
  search; fits already in the library are marked and unticked; Import adds the ticked ones.
- SaveToGameDialog (Doctrines audit ▸ a pilot ▸ Save Fits to Game…): the pilot's doctrine fittings and
  whether each is saved in game; Save saves the ticked ones (an older copy is replaced: backed up, deleted,
  saved again) after a confirm.
- DeletedFromGameDialog (Fittings tab ▸ Deleted from Game…): fits deleted in the last 14 days ▸ Restore.

offer_update (29.4) runs after a library fitting changes: pilots with an older marked copy are offered
"Replace the in-game fitting with the update?".
"""
import tkinter as tk
from tkinter import ttk
from typing import Dict, List, Optional

from app.esi_service.game_fittings import from_game, restore_body, to_game
from app.gui import style as ui_style
from app.gui import themed_dialogs as messagebox
from app.gui.dialogs.send_to_client_dialog import run_async
from app.gui.window_placement import place_window
from app.services.fitting_sync import OLDER, NOT_SAVED, holders_of_older, library_match, standing

TICK, UNTICK = "☑", "☐"


def _later(window, func, *args):
    try:
        window.after(0, lambda: window.winfo_exists() and func(*args))
    except (tk.TclError, RuntimeError):
        pass


class _Window:
    def __init__(self, app, title: str):
        self.app = app
        self.window = tk.Toplevel(app.root)
        self.window.title(title)
        self.window.transient(app.root)
        self.body = ttk.Frame(self.window, padding=(12, 10))
        self.body.pack(fill=tk.BOTH, expand=True)
        self.status = None

    def _tree(self, columns, height=14):
        frame = ttk.Frame(self.body)
        frame.pack(fill=tk.BOTH, expand=True, pady=(6, 0))
        tree = ttk.Treeview(frame, columns=[c[0] for c in columns], show="headings", height=height)
        for key, text, width in columns:
            tree.heading(key, text=text, anchor=tk.W)
            tree.column(key, width=width, anchor=tk.CENTER if key == "tick" else tk.W, stretch=key != "tick")
        scroll = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        return tree

    def _say(self, text, error=False):
        if self.status is not None:
            self.status.config(text=text, foreground=ui_style.ERROR if error else ui_style.MUTED)

    def _allowed(self) -> bool:
        allowed, why = self.app._may_call_ccp()
        if not allowed:
            self._say(why, error=True)
        return allowed

    def close(self):
        self.window.destroy()


# --- Import from Game… (29.2) ------------------------------------------------------------------------------

class ImportFromGameDialog(_Window):
    def __init__(self, app, pilot: Optional[str] = None):
        super().__init__(app, "Import from Game")
        self.fits: List[dict] = []
        self.ticked: Dict[int, bool] = {}
        self.in_library: Dict[int, dict] = {}
        top = ttk.Frame(self.body)
        top.pack(fill=tk.X)
        ttk.Label(top, text="Character:").pack(side=tk.LEFT)
        self.characters = {app.auth_service.index.get(str(c), f"Character {c}"): str(c) for c in app.auth_service.profiles}
        self.char_combo = ttk.Combobox(top, values=list(self.characters), state="readonly", width=22)
        self.char_combo.set(next((n for n, c in self.characters.items() if c == str(pilot)), next(iter(self.characters), "")))
        self.char_combo.pack(side=tk.LEFT, padx=(4, 12))
        self.char_combo.bind("<<ComboboxSelected>>", lambda e: self.load())
        ttk.Label(top, text="Hull:").pack(side=tk.LEFT)
        self.hull_combo = ttk.Combobox(top, state="readonly", width=22)
        self.hull_combo.pack(side=tk.LEFT, padx=(4, 12))
        self.hull_combo.bind("<<ComboboxSelected>>", lambda e: self._show())
        ttk.Label(top, text="Search:").pack(side=tk.LEFT)
        self.search_var = tk.StringVar()
        ttk.Entry(top, textvariable=self.search_var, width=20).pack(side=tk.LEFT, padx=4)
        self.search_var.trace_add("write", lambda *a: self._show())
        self.status = ttk.Label(self.body, text="", style=ui_style.HINT_LABEL)
        self.status.pack(anchor=tk.W, pady=(6, 0))
        self.tree = self._tree((("tick", "", 30), ("name", "Fit", 260), ("hull", "Hull", 150), ("note", "", 260)))
        self.tree.bind("<Button-1>", self._toggle)
        buttons = ttk.Frame(self.body)
        buttons.pack(anchor=tk.E, pady=(8, 0))
        self.btn_import = ttk.Button(buttons, text="Import Ticked", command=self.import_ticked, state=tk.DISABLED)
        self.btn_import.pack(side=tk.LEFT, padx=5)
        ttk.Button(buttons, text="Close", command=self.close).pack(side=tk.LEFT, padx=5)
        place_window(self.window)
        self.load()

    def load(self):
        char_id = self.characters.get(self.char_combo.get())
        if not char_id or not self._allowed():
            return
        self._say(f"Reading {self.char_combo.get()}'s fittings…")
        run_async(self.app.game_fittings.list(char_id), lambda result: _later(self.window, self._loaded, result))

    def _loaded(self, result):
        fits, message = result if isinstance(result, tuple) else (None, getattr(result, "message", "Couldn't read them."))
        if fits is None:
            self._say(message, error=True)
            return
        library = self.app.fitting_manager.list_fittings()
        name = self.app.evedb_loader.get_type_name
        key = self.app.fittings_view._equivalence_key()
        self.fits = sorted(fits, key=lambda f: (name(f["ship_type_id"]).casefold(), f.get("name", "").casefold()))
        self.in_library = {}
        for f in self.fits:
            match = library_match(f, library, name, key)
            if match is not None:
                self.in_library[int(f["fitting_id"])] = match
        self.ticked = {int(f["fitting_id"]): False for f in self.fits}
        self.hull_combo["values"] = ["All hulls"] + sorted({name(f["ship_type_id"]) for f in self.fits}, key=str.casefold)
        self.hull_combo.set("All hulls")
        self._show()
        self._say(f"{len(self.fits)} fits saved in game; {len(self.in_library)} already in the library (marked). "
                  "Click a fit to tick it.")

    def visible(self) -> List[dict]:
        name = self.app.evedb_loader.get_type_name
        hull = self.hull_combo.get()
        text = self.search_var.get().strip().casefold()
        return [f for f in self.fits if (hull in ("", "All hulls") or name(f["ship_type_id"]) == hull)
                and (not text or text in f.get("name", "").casefold() or text in name(f["ship_type_id"]).casefold())]

    def _show(self):
        self.tree.delete(*self.tree.get_children())
        name = self.app.evedb_loader.get_type_name
        for f in self.visible():
            fid = int(f["fitting_id"])
            match = self.in_library.get(fid)
            self.tree.insert("", tk.END, iid=str(fid), values=(
                TICK if self.ticked.get(fid) else UNTICK, f.get("name", ""), name(f["ship_type_id"]),
                f"In the library: {match['fit_name']}" if match else ""))
        self.btn_import.config(state=tk.NORMAL if any(self.ticked.values()) else tk.DISABLED)

    def _toggle(self, event):
        row = self.tree.identify_row(event.y)
        if row:
            self.tick(int(row), not self.ticked.get(int(row)))

    def tick(self, fitting_id: int, on: bool = True):
        self.ticked[int(fitting_id)] = on
        self._show()

    def import_ticked(self):
        chosen = [f for f in self.fits if self.ticked.get(int(f["fitting_id"]))]
        if not chosen:
            return
        doubles = [f for f in chosen if int(f["fitting_id"]) in self.in_library]
        if doubles and not messagebox.askyesno(
                "Import from Game", f"{len(doubles)} of the ticked fits {'is' if len(doubles) == 1 else 'are'} already in the library "
                                    f"({', '.join(f['name'] for f in doubles[:3])}{'…' if len(doubles) > 3 else ''}). "
                                    "Import them anyway, as new fittings?", parent=self.window):
            return
        name = self.app.evedb_loader.get_type_name
        made = [self.app.fitting_manager.create_fitting(from_game(f, name), source="local") for f in chosen]
        self.app._log(f"[SUCCESS] Imported {len(made)} fit(s) from {self.char_combo.get()}'s game fittings: "
                      + ", ".join(m["fit_name"] for m in made) + ".")
        self.app.fittings_view._refresh_fitting_list()
        self.app.fittings_view._refresh_library_fittings()
        for f, m in zip(chosen, made):
            self.in_library[int(f["fitting_id"])] = m
            self.ticked[int(f["fitting_id"])] = False
        self._show()
        self._say(f"Imported {len(made)} fit(s).")


# --- Save Fits to Game… (29.3) -----------------------------------------------------------------------------

class SaveToGameDialog(_Window):
    def __init__(self, app, pilot: str, fittings: List[dict], doctrine: str = ""):
        who = app.auth_service.index.get(str(pilot), f"Character {pilot}")
        super().__init__(app, f"Save Fits to Game: {who}")
        self.pilot, self.who = str(pilot), who
        self.fittings = fittings
        self.standings = {}
        self.ticked: Dict[int, bool] = {}
        ttk.Label(self.body, text=f"{doctrine}: the fittings {who} flies, and whether each is saved in game."
                  if doctrine else f"The fittings {who} flies, and whether each is saved in game.",
                  wraplength=560).pack(anchor=tk.W)
        self.status = ttk.Label(self.body, text="", style=ui_style.HINT_LABEL)
        self.status.pack(anchor=tk.W, pady=(6, 0))
        self.tree = self._tree((("tick", "", 30), ("fit", "Fitting", 260), ("hull", "Hull", 130), ("state", "In game", 280)),
                               height=10)
        self.tree.bind("<Button-1>", self._toggle)
        buttons = ttk.Frame(self.body)
        buttons.pack(anchor=tk.E, pady=(8, 0))
        self.btn_save = ttk.Button(buttons, text="Save Ticked…", command=self.save_ticked, state=tk.DISABLED)
        self.btn_save.pack(side=tk.LEFT, padx=5)
        ttk.Button(buttons, text="Close", command=self.close).pack(side=tk.LEFT, padx=5)
        place_window(self.window)
        self.load()

    def load(self):
        if not self._allowed():
            return
        self._say(f"Reading {self.who}'s fittings…")
        run_async(self.app.game_fittings.list(self.pilot), lambda result: _later(self.window, self._loaded, result))

    def _loaded(self, result):
        fits, message = result if isinstance(result, tuple) else (None, "Couldn't read them.")
        if fits is None:
            self._say(message, error=True)
            return
        key = self.app.fittings_view._equivalence_key()
        name = self.app.evedb_loader.get_type_name
        self.standings = {f["fit_uid"]: standing(f, fits, name, key) for f in self.fittings}
        self.ticked = {uid: s.state in (NOT_SAVED, OLDER) for uid, s in self.standings.items()}
        self._show()
        todo = sum(self.ticked.values())
        self._say(f"{todo} to save (ticked)." if todo else "All saved in game.")

    def _show(self):
        self.tree.delete(*self.tree.get_children())
        for f in self.fittings:
            s = self.standings.get(f["fit_uid"])
            self.tree.insert("", tk.END, iid=str(f["fit_uid"]), values=(
                TICK if self.ticked.get(f["fit_uid"]) else UNTICK, f["fit_name"], f.get("hull", ""),
                s.text(f) if s else "…"))
        self.btn_save.config(state=tk.NORMAL if any(self.ticked.values()) else tk.DISABLED)

    def _toggle(self, event):
        row = self.tree.identify_row(event.y)
        if row:
            self.ticked[int(row)] = not self.ticked.get(int(row))
            self._show()

    def save_ticked(self):
        chosen = [f for f in self.fittings if self.ticked.get(f["fit_uid"])]
        older = [f for f in chosen if self.standings[f["fit_uid"]].state == OLDER]
        if not chosen or not messagebox.askyesno(
                "Save Fits to Game", f"Save {len(chosen)} fit(s) to {self.who}'s fittings in game?"
                + (f"\n\n{len(older)} older cop{'ies' if len(older) != 1 else 'y'} will be replaced: backed up for "
                   "14 days (Fittings ▸ Deleted from Game…), deleted, and saved again." if older else ""),
                parent=self.window):
            return
        if not self._allowed():
            return
        self._say("Saving…")
        jobs = [(f, self.standings[f["fit_uid"]].fits if self.standings[f["fit_uid"]].state == OLDER else [])
                for f in chosen]
        run_async(save_fits(self.app, self.pilot, jobs), lambda results: _later(self.window, self._saved, results))

    def _saved(self, results):
        if not isinstance(results, list):
            self._say(getattr(results, "message", "Saving stopped."), error=True)
            return
        failed = [r for r in results if not r.ok]
        for r in results:
            self.app._log(("[SUCCESS] " if r.ok else "[WARNING] ") + r.message)
        self._say(f"Saved {len(results) - len(failed)} of {len(results)}."
                  + (" " + " ".join(r.message for r in failed) if failed else ""), error=bool(failed))
        self.load()


async def save_fits(app, pilot: str, jobs) -> list:
    """Each (library fitting, older game copies to replace): delete the older ones (backed up), then save.
    One fit's error doesn't stop the rest (29.1)."""
    results = []
    for fitting, older in jobs:
        replaced_ok = True
        for old in older:
            result = await app.game_fittings.delete(pilot, old, app.fitting_backups)
            if not result.ok:
                results.append(result)
                replaced_ok = False
        if replaced_ok:
            results.append(await app.game_fittings.save(pilot, to_game(fitting, _description(older))))
    return results


def _description(older: List[dict]) -> str:
    """Keep the text a pilot wrote on the older copy (the marker is replaced)."""
    from app.esi_service.game_fittings import MARKER
    return MARKER.sub("", older[0].get("description", "")).strip() if older else ""


# --- Deleted from Game… (29.5) -----------------------------------------------------------------------------

class DeletedFromGameDialog(_Window):
    def __init__(self, app):
        super().__init__(app, "Deleted from Game")
        ttk.Label(self.body, text="Fits deleted from the game by this app in the last 14 days. Restore saves one "
                                  "again (with a new ID in game).", wraplength=560).pack(anchor=tk.W)
        self.status = ttk.Label(self.body, text="", style=ui_style.HINT_LABEL)
        self.status.pack(anchor=tk.W, pady=(6, 0))
        self.tree = self._tree((("pilot", "Pilot", 140), ("name", "Fit", 240), ("hull", "Hull", 130),
                                ("when", "Deleted", 130), ("left", "Kept", 70)), height=10)
        buttons = ttk.Frame(self.body)
        buttons.pack(anchor=tk.E, pady=(8, 0))
        self.btn_restore = ttk.Button(buttons, text="Restore", command=self.restore_selected)
        self.btn_restore.pack(side=tk.LEFT, padx=5)
        ttk.Button(buttons, text="Close", command=self.close).pack(side=tk.LEFT, padx=5)
        place_window(self.window)
        self.refresh()

    def refresh(self):
        self.entries = self.app.fitting_backups.list()
        self.tree.delete(*self.tree.get_children())
        names = self.app.auth_service.index
        for n, (path, entry) in enumerate(self.entries):
            fit = entry.get("fit", {})
            self.tree.insert("", tk.END, iid=str(n), values=(
                names.get(str(entry.get("character_id")), entry.get("character_id")), fit.get("name", ""),
                self.app.evedb_loader.get_type_name(fit.get("ship_type_id", 0)),
                entry.get("deleted_at", "")[:16].replace("T", " "), f"{self.app.fitting_backups.days_left(entry)} d"))
        self.btn_restore.config(state=tk.NORMAL if self.entries else tk.DISABLED)
        self._say("" if self.entries else "Nothing deleted in the last 14 days.")

    def restore_selected(self):
        selected = self.tree.selection() or self.tree.get_children()[:1]
        if not selected or not self._allowed():
            return
        path, entry = self.entries[int(selected[0])]
        self._say("Restoring…")
        run_async(self.app.game_fittings.save(str(entry["character_id"]), restore_body(entry["fit"])),
                  lambda result: _later(self.window, self._restored, path, result))

    def _restored(self, path, result):
        self.app._log(("[SUCCESS] Restored: " if result.ok else "[WARNING] ") + result.message)
        if result.ok:
            self.app.fitting_backups.remove(path)
        self.refresh()
        self._say(result.message, error=not result.ok)


# --- the update prompt (29.4) ------------------------------------------------------------------------------

def offer_update(app, fitting: dict) -> None:
    """After a library fitting's contents change: pilots holding an older marked copy are offered the update."""
    from app.services import esi_features
    if getattr(app, "game_fittings", None) is None or not esi_features.enabled("fittings")             or int(fitting.get("version", 1)) <= 1:
        return
    pilots = [str(c) for c in app.auth_service.profiles if app.game_fittings.can(c)]
    if not pilots or not app._may_call_ccp()[0]:
        return

    async def read_all():
        out = {}
        for pilot in pilots:
            fits, _ = await app.game_fittings.list(pilot)
            if fits:
                out[pilot] = fits
        return out

    def ask(fits_by_pilot):
        if not isinstance(fits_by_pilot, dict):
            return
        holders = holders_of_older(fitting, fits_by_pilot)
        if not holders:
            return
        names = app.auth_service.index
        lines = "\n".join(f"  • {names.get(p, p)}: " + ", ".join(f"{f['name']}" for f in fits) for p, fits in holders.items())
        app.update_prompt_text = lines
        if not messagebox.askyesno("Replace the In-Game Fitting?",
                                   f"{fitting['fit_name']} changed (now v{fitting.get('version', 1)}). These pilots have "
                                   f"an older copy saved in game:\n\n{lines}\n\nReplace the in-game fitting with the "
                                   "update? The older copy is backed up for 14 days."):
            return

        async def replace_all():
            results = []
            for pilot, fits in holders.items():
                results += await save_fits(app, pilot, [(fitting, fits)])
            return results

        def done(results):
            for r in results if isinstance(results, list) else []:
                app._log(("[SUCCESS] " if r.ok else "[WARNING] ") + r.message)
        run_async(replace_all(), lambda results: app.root.after(0, done, results))
    run_async(read_all(), lambda result: app.root.after(0, ask, result))

