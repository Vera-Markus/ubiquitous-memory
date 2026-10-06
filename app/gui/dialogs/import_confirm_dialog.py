"""
The package import confirmation (design §11.4): what the import will add,
update and remove, anything that blocks it, and the assignment choice.
Nothing is written until Import is pressed.
"""
import tkinter as tk
from tkinter import ttk
from typing import Callable, List

from app.services.doctrine_import_service import LABELS, NAME_KEYS, SECTIONS, UID_KEYS, ImportPlan
from app.gui import style as ui_style



def plan_lines(plan: ImportPlan) -> List[str]:
    """The plan's details, one heading and its items per section, as the dialog lists them."""
    names = {kind: {r[UID_KEYS[kind]]: r.get(NAME_KEYS[kind], "?") for r in plan.package.get(SECTIONS[kind]) or []}
             for kind in SECTIONS}
    lines: List[str] = []

    def section(title, items):
        if items:
            lines.append(title)
            lines.extend(f"  • {item}" for item in items)

    section("Can't import until these are fixed:", plan.conflicts)
    for verb, changes in (("Adds", plan.add), ("Updates", plan.update)):
        section(f"{verb}:", [f"{LABELS[kind]} {names[kind].get(uid, uid)}" for kind in ("doctrine", "role", "fit")
                              for uid in changes[kind]])
    section("Removes (no longer in the package):", plan.removed_names)
    section("Fittings with changes:", plan.fits_changed)
    section("Fittings replaced, because the hull changed:", plan.fits_hull_changed)
    section("Keeps your names for:", plan.kept_names)
    section("Your own roles that will lose a fitting, because its hull changed:", plan.local_requirements_on_new_hull)
    section(f"Your replacements kept: {len(plan.corrections_kept)}", plan.corrections_kept)
    section("Your replacements kept, though the package changed the fitting:", plan.corrections_changed)
    section("Your replacements dropped (the original applies again):", plan.corrections_dropped)
    section("Requirements removed from your roles:", plan.removed_lines())
    section("Requirements you're keeping:", plan.kept_lines())
    section("Moving to your own fittings (in no role yet):", plan.moved_lines())
    section("Character assignments that will be dropped:", plan.dropped_assignments)
    section("Your own roles that will lose a fitting:", plan.orphaned_local_requirements)
    section("Warnings:", plan.warnings)
    return lines


class ImportConfirmDialog:
    def __init__(self, app, plan: ImportPlan, on_confirm: Callable[[bool], None]):
        self.app = app
        self.plan = plan
        self.on_confirm = on_confirm

        self.window = tk.Toplevel(app.root)
        self.window.title(f"Import Doctrine Package: {plan.package_name}")
        self.window.transient(app.root)
        body = ttk.Frame(self.window, padding=(10, 10))
        body.pack(fill=tk.BOTH, expand=True)

        self.summary = ttk.Label(body, text=plan.summary(), justify=tk.LEFT, anchor=tk.W, wraplength=640,
                                foreground=ui_style.ERROR if plan.conflicts else ui_style.TEXT)
        self.summary.pack(fill=tk.X)
        self.details = ui_style.ScrolledText(body, height=14, width=80)
        self.details.insert("1.0", "\n".join(plan_lines(plan)) or "Nothing changes.")
        self.details.configure(state="disabled")
        self.details.pack(fill=tk.BOTH, expand=True, pady=5)

        self.use_package_assignments = tk.BooleanVar(value=False)
        if plan.package_has_assignments:
            choice = ttk.LabelFrame(body, text="This package includes character assignments", padding=(5, 5))
            choice.pack(fill=tk.X)
            ttk.Radiobutton(choice, text="Keep mine", variable=self.use_package_assignments, value=False).pack(anchor=tk.W)
            ttk.Radiobutton(choice, text="Use the package's", variable=self.use_package_assignments, value=True).pack(anchor=tk.W)
            if plan.unlinked_assignments:
                n = len(plan.unlinked_assignments)
                ttk.Label(choice, wraplength=620, justify=tk.LEFT, style=ui_style.HINT_LABEL,
                          text=f"Using the package's skips {n} assignment{'s' if n != 1 else ''} for characters "
                               "you haven't added: " + ", ".join(plan.unlinked_assignments)).pack(anchor=tk.W)

        buttons = ttk.Frame(body)
        buttons.pack(anchor=tk.E, pady=(10, 0))
        ttk.Button(buttons, text="Cancel", command=self.close).pack(side=tk.LEFT, padx=5)
        self.btn_import = ttk.Button(buttons, text="Import", command=self.confirm,
                                    state=tk.DISABLED if plan.conflicts else tk.NORMAL)
        self.btn_import.pack(side=tk.LEFT, padx=5)
        try:
            self.window.grab_set()
        except tk.TclError:
            pass

    def confirm(self):
        if self.plan.conflicts:
            return
        use_package = bool(self.use_package_assignments.get())
        self.close()
        self.on_confirm(use_package)

    def close(self):
        self.window.destroy()

    def describe(self) -> dict:
        return {"title": self.window.title(), "summary": self.summary["text"],
                "details": self.details.get("1.0", tk.END).rstrip("\n").splitlines(),
                "assignment_choice": self.plan.package_has_assignments, "import_button": str(self.btn_import["state"])}
