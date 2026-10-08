"""
Options ▸ ESI Features (ESI features plan 26.2): one switch per feature, saved in
data/config/ui_settings.json beside the theme. A feature that's off makes no calls and
shows nothing. Losses and its sub-switches are off by default (D7.1, D7.6); the rest on.
Options lists a feature once its phase is built ("built"), so no switch does nothing.
"""
import json
from pathlib import Path
from typing import Any, Dict, Optional

from app import paths

SETTINGS_FILE = "ui_settings.json"      # shared with the theme (app/gui/style.py)
PREFIX = "esi_feature."

FEATURES: Dict[str, Dict[str, Any]] = {
    "skills": {"label": "Skill check", "default": True, "built": True},
    "client": {"label": "Open in the game client", "default": True, "built": True},
    "contracts": {"label": "Contracts", "default": True, "built": True},
    # Find a Hull… reads public contracts too (plan 28.6, Q28.2): slow the first time, so off unless chosen.
    "public_contracts": {"label": "Public contracts in Find a Hull (slow the first time)", "default": False,
                         "under": "contracts"},
    "prices": {"label": "Prices", "default": True, "built": True},
    "fittings": {"label": "Fitting sync with the game", "default": True, "built": True},
    "losses": {"label": "Losses", "default": False, "built": True},
    "losses_srp": {"label": "SRP items", "default": False, "under": "losses"},
    "losses_insurance": {"label": "Insurance estimate", "default": False, "under": "losses"},
    "losses_corporation": {"label": "Corporation losses (Directors)", "default": False, "under": "losses"},
}

HUBS: Dict[str, Dict[str, int]] = {          # D3.3: the trade hub prices come from
    "Jita 4-4": {"station_id": 60003760, "region_id": 10000002},
    "Amarr VIII": {"station_id": 60008494, "region_id": 10000043},
    "Dodixie": {"station_id": 60011866, "region_id": 10000032},
    "Rens": {"station_id": 60004588, "region_id": 10000030},
    "Hek": {"station_id": 60005686, "region_id": 10000042},
}
DEFAULT_HUB = "Jita 4-4"


def _read(config_dir: Path) -> Dict[str, Any]:
    try:
        settings = json.loads((Path(config_dir) / SETTINGS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return settings if isinstance(settings, dict) else {}


def _write(config_dir: Path, key: str, value: Any) -> None:
    path = Path(config_dir) / SETTINGS_FILE
    settings = _read(config_dir)
    settings[key] = value
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2), encoding="utf-8")


def enabled(feature: str, config_dir: Optional[Path] = None) -> bool:
    """Is the feature on? A sub-switch is on only while its feature is."""
    config_dir = config_dir or paths.CONFIG_DIR
    spec = FEATURES[feature]
    if spec.get("under") and not enabled(spec["under"], config_dir):
        return False
    return bool(_read(config_dir).get(PREFIX + feature, spec["default"]))


def stored(feature: str, config_dir: Optional[Path] = None) -> bool:
    """The switch as the user left it, whatever its parent's (for drawing Options)."""
    return bool(_read(config_dir or paths.CONFIG_DIR).get(PREFIX + feature, FEATURES[feature]["default"]))


def shown() -> Dict[str, Dict[str, Any]]:
    """The features Options lists: the built ones."""
    return {k: v for k, v in FEATURES.items() if v.get("built") or FEATURES.get(v.get("under", ""), {}).get("built")}


def set_enabled(feature: str, on: bool, config_dir: Optional[Path] = None) -> None:
    _write(config_dir or paths.CONFIG_DIR, PREFIX + feature, bool(on))


def hub(config_dir: Optional[Path] = None) -> str:
    name = _read(config_dir or paths.CONFIG_DIR).get(PREFIX + "hub", DEFAULT_HUB)
    return name if name in HUBS else DEFAULT_HUB


def set_hub(name: str, config_dir: Optional[Path] = None) -> None:
    _write(config_dir or paths.CONFIG_DIR, PREFIX + "hub", name if name in HUBS else DEFAULT_HUB)
