from dataclasses import dataclass, field
from typing import List
import re

@dataclass
class ValidationResult:
    is_valid: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

class fittingValidator:
    def __init__(self, db_loader=None):
        """
        :param db_loader: An optional EVEdbLoader instance for advanced validation.
        """
        self.db_loader = db_loader

    def validate(self, fit_text: str) -> ValidationResult:
        errors = []
        warnings = []

        raw_lines = fit_text.splitlines()
        non_empty_lines = [l.strip() for l in raw_lines if l.strip()]

        if not non_empty_lines:
            return ValidationResult(is_valid=False, errors=["No content found. Please paste a valid fitting fit."])

        # 1. Validate Header: [Ship Name, fitting Name]
        first_line = non_empty_lines[0]
        header_match = re.match(r"^\[\s*(.+?)\s*,\s*(.+?)\s*\]$", first_line)

        if not header_match:
            return ValidationResult(
                is_valid=False, 
                errors=[
                    "Invalid fitting format. The first line must be: [Ship Name, fitting Name]",
                    "Example: [Vexor, Test Fit]"
                ]
            )

        ship_name = header_match.group(1).strip()
        fitting_name = header_match.group(2).strip()

        if not ship_name:
            errors.append("Ship name is missing in the header.")
        if not fitting_name:
            errors.append("fitting name is missing in the header.")

        # 2. Validate Fit contains at least one additional line after header
        if len(non_empty_lines) < 2:
            errors.append("Fit contains no modules or cargo after the header.")

        # 3. Unknown module names (when an SDE loader is available); the header is skipped
        module_lines = non_empty_lines[1:]

        if self.db_loader:
            for line in module_lines:
                match = re.match(r"(.+?)(?:\s+x(\d+))?$", line)
                if match:
                    mod_name = match.group(1).strip()
                    # Check if it's a known typeID
                    if not self.db_loader.get_typeid_by_name(mod_name):
                        # We only warn if it's not the header
                        warnings.append(f"Unknown module name: {mod_name}")

        return ValidationResult(
            is_valid=len(errors) == 0,
            errors=errors,
            warnings=warnings
        )
