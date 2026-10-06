"""
Requirements a package update would remove (doctrine tweaks plan, step 11.4).
Shown before the confirm dialog: one row per requirement, with a Keep tick box,
or, when the fitting manager retired its fitting, Move to personal (decision 11-C).
Nothing is written here; the choices go on the plan.
"""
import tkinter as tk
from tkinter import ttk
from typing import Callable, Dict, Tuple

from app.services.doctrine_import_service import ImportPlan

RETIRED = "Retired by the fitting manager"


REMOVED_TEXT = ("This update removes these requirements from your roles. Tick Keep to keep a requirement "
                "through this and later updates. A fitting the fitting manager retired can't be kept, but "
                "Move to personal copies it to your own fittings, ready to add to one of your roles.")
ADDED_TEXT = ("Choose which of the requirements you added to keep. Kept ones stay through this and later "
              "updates without asking again; unticked ones are removed.")


class RemovedRequirementsDialog:
    """
    Rows of requirements with a tick box each. By default, the ones the update removes
    (step 11.4); with added=True, the pilot's own added requirements, all ticked to start.
    """

    def __init__(self, app, plan: ImportPlan, on_continue: Callable[[list, list], None], added: bool = False):
        self.app = app
        self.plan = plan
        self.on_continue = on_continue
        self.rows = plan.added_requirements if added else plan.removed_requirements
        self.choices: Dict[Tuple[int, int], tk.BooleanVar] = {}

        self.window = tk.Toplevel(app.root)
        self.window.title(f"Your added requirements in {plan.package_name}" if added
                          else f"Requirements {plan.package_name} removes")
        self.window.transient(app.root)
        body = ttk.Frame(self.window, padding=(10, 10))
        body.pack(fill=tk.BOTH, expand=True)

        ttk.Label(body, justify=tk.LEFT, wraplength=640, text=ADDED_TEXT if added else REMOVED_TEXT).pack(fill=tk.X)

        rows = ttk.Frame(body)
        rows.pack(fill=tk.BOTH, expand=True, pady=10)
        for index, row in enumerate(self.rows):
            text = row.label() + ("" if row.role_stays else " (role removed)")
            ttk.Label(rows, text=text).grid(row=index, column=0, sticky=tk.W, padx=(0, 10), pady=2)
            var = tk.BooleanVar(value=added)
            self.choices[row.key] = var
            if row.retired:
                ttk.Label(rows, text=RETIRED, foreground="#8a5a00").grid(row=index, column=1, sticky=tk.W, padx=(0, 10))
                ttk.Checkbutton(rows, text="Move to personal", variable=var).grid(row=index, column=2, sticky=tk.W)
            else:
                ttk.Checkbutton(rows, text="Keep", variable=var).grid(row=index, column=2, sticky=tk.W)

        buttons = ttk.Frame(body)
        buttons.pack(anchor=tk.E)
        ttk.Button(buttons, text="Cancel", command=self.close).pack(side=tk.LEFT, padx=5)
        ttk.Button(buttons, text="Continue", command=self.confirm).pack(side=tk.LEFT, padx=5)
        try:
            self.window.grab_set()
        except tk.TclError:
            pass

    def ticked(self):
        """(keep, move_to_personal): the ticked rows' keys."""
        rows = {r.key: r for r in self.rows}
        chosen = [key for key, var in self.choices.items() if var.get()]
        return ([k for k in chosen if not rows[k].retired], [k for k in chosen if rows[k].retired])

    def confirm(self):
        keep, move = self.ticked()
        self.close()
        self.on_continue(keep, move)

    def close(self):
        self.window.destroy()

    def describe(self) -> dict:
        return {"title": self.window.title(),
                "rows": [{"text": r.label(), "retired": r.retired, "role_stays": r.role_stays,
                          "ticked": self.choices[r.key].get()} for r in self.rows]}
