from typing import Any, Dict

# EFT's section order. Fitted modules are one per line; bays list stacks as "Name xN".
EFT_SLOT_SECTIONS = ("low", "mid", "high", "rigs", "subsystem", "service")
EFT_STACK_SECTIONS = ("drones", "fighters", "cargo")


class FittingDisplayFormatter:
    """
    Service responsible for formatting fitting dictionaries into human-readable strings.
    """

    @staticmethod
    def eft(record: Dict[str, Any]) -> str:
        """
        A saved fitting as EFT text, the way the game exports it, so the Fittings tab's
        Edit can show it and parse it back. Items the parser couldn't resolve weren't
        saved, so they aren't here either.
        """
        fit = record.get("fit", record)
        hull = record.get("hull") or fit.get("hull", "Unknown")
        name = record.get("fit_name") or fit.get("fit_name", "Unknown")
        sections = []
        for key in EFT_SLOT_SECTIONS:
            items = (fit.get(key) or {}).values()
            sections.append([i.get("name", "Unknown") for i in items for _ in range(int(i.get("quantity", 1)))])
        for key in EFT_STACK_SECTIONS:
            items = (fit.get(key) or {}).values()
            sections.append([f"{i.get('name', 'Unknown')} x{int(i.get('quantity', 1))}" for i in items])
        body = "\n\n".join("\n".join(lines) for lines in sections if lines)
        return f"[{hull}, {name}]\n\n{body}\n"

    @staticmethod
    def format(contents: Dict[str, Any]) -> str:
        """
        Formats the fitting dictionary into a human-readable string.

        Args:
            contents: The dictionary containing fitting component information.

        Returns:
            A formatted string representation of the fitting.
        """
        sections = {
            "high": "HIGH SLOTS",
            "mid": "MID SLOTS",
            "low": "LOW SLOTS",
            "rigs": "RIGS",
            "subsystem": "SUBSYSTEMS",
            "drones": "DRONES",
            "fighters": "FIGHTERS",
            "cargo": "CARGO"
        }
        
        formatted_sections = []
        separator = "=" * 32

        for key, header in sections.items():
            items = contents.get(key)
            if not items:
                continue
            
            # Each section: [HEADER, "", ITEMS, "", SEPARATOR]
            section_lines = [header, ""]
            
            for item_id, item_info in items.items():
                name = item_info.get("name", "Unknown")
                quantity = item_info.get("quantity", 1)
                section_lines.append(f"{quantity}x {name}")
            
            section_lines.append("")
            section_lines.append(separator)
            formatted_sections.append("\n".join(section_lines))
            
        if not formatted_sections:
            return ""

        # Prepend separator to the first section and join with single newline 
        # to avoid double-separator issues between sections.
        return separator + "\n\n" + "\n\n".join(formatted_sections)
