import json
import os
import glob
import logging
from pathlib import Path
from typing import Callable, Dict, List, Tuple
from app.paths import RAW_DIR, GENERATED_DIR

logger = logging.getLogger("AggregateAssets")

# Places only a ship or container has: an item in one of these sits inside another item, which
# must be in the same asset list. (A structure's contents use Hangar, CorpSAG, Deliveries,
# AssetSafety and the like, and the structure itself is never in the list: those are kept.)
INSIDE_AN_ITEM = ("HiSlot", "MedSlot", "LoSlot", "RigSlot", "SubSystemSlot", "SubSystemBay", "Cargo",
                  "DroneBay", "FighterBay", "FighterTube", "ShipHangar", "FleetHangar", "Specialized",
                  "FrigateEscapeBay", "BoosterBay", "CorpseBay", "Locked", "Unlocked")


def drop_orphans(assets: List[dict]) -> Tuple[List[dict], Dict[int, List[dict]]]:
    """
    A failsafe for ESI asset lists that name a ship or container that isn't there (old rigs left
    behind by a ship long gone; the modules of a ship the character is in, when it couldn't be
    read). Leaves out items inside an item that isn't in the list, and anything inside those.
    Returns (the assets kept, {missing parent ID: the items left out under it}).
    """
    present = {a.get("item_id") for a in assets}
    dropped: Dict[int, List[dict]] = {}
    gone = set()
    for asset in assets:
        parent = asset.get("location_id")
        if asset.get("location_type") == "item" and parent not in present \
                and str(asset.get("location_flag") or "").startswith(INSIDE_AN_ITEM):
            dropped.setdefault(parent, []).append(asset)
            gone.add(asset.get("item_id"))
    while True:             # what's inside a dropped item (charges in a dropped launcher, say)
        inside = [a for a in assets if a.get("location_id") in gone and a.get("item_id") not in gone]
        if not inside:
            break
        for asset in inside:
            root = next(p for p, items in dropped.items() if any(i.get("item_id") == asset["location_id"] for i in items))
            dropped[root].append(asset)
            gone.add(asset.get("item_id"))
    return [a for a in assets if a.get("item_id") not in gone], dropped

def aggregate_assets(output_file_name: str, log_callback: Callable[[str], None] = None):
    """
    Reads all .json files in RAW_DIR and combines them into a single list in GENERATED_DIR.
    """
    if log_callback:
        class CallbackHandler(logging.Handler):
            def emit(self, record):
                log_callback(self.format(record))
        
        handler = CallbackHandler()
        handler.setFormatter(logging.Formatter('%(levelname)s: %(message)s'))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)

    all_assets = []
    
    # Find all .json files in the raw directory
    search_pattern = os.path.join(RAW_DIR, "*.json")
    json_files = glob.glob(search_pattern)
    
    logger.info(f"Found {len(json_files)} JSON files in {RAW_DIR}")
    
    output_path = GENERATED_DIR / output_file_name
    
    for file_path in json_files:
        # Skip the output file if it happens to be in the same directory
        if Path(file_path).resolve() == output_path.resolve():
            continue
            
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, list):
                    data, dropped = drop_orphans([a for a in data if isinstance(a, dict)])
                    for parent, items in dropped.items():
                        logger.warning(f"  - {Path(file_path).stem}: left out {len(items)} item(s) inside {parent}, "
                                       "which ESI's asset list doesn't include (if it's the ship the character is "
                                       "in, adding the character again lets the app read it).")
                    for asset in data:
                        if isinstance(asset, dict):
                            # Extract character_id from the filename (e.g., 123456789.json -> 123456789)
                            char_id_str = Path(file_path).stem
                            if char_id_str.isdigit():
                                asset['character_id'] = int(char_id_str)
                            all_assets.append(asset)
                    logger.debug(f"  - Added {len(data)} assets from {os.path.basename(file_path)}")
                elif isinstance(data, dict):
                    # If it's a dict, assume it might have an 'assets' key or just be one object
                    # But based on previous output, it's a list.
                    all_assets.append(data)
                    logger.debug(f"  - Added 1 asset object from {os.path.basename(file_path)}")
                else:
                    logger.warning(f"  - Unexpected data format in {os.path.basename(file_path)}")
        except Exception as e:
            logger.error(f"  - Error reading {os.path.basename(file_path)}: {e}")

    # Ensure output directory exists
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Write the aggregated results
    try:
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(all_assets, f, indent=4)
        logger.info(f"Successfully aggregated {len(all_assets)} total assets to {output_path}")
    except Exception as e:
        logger.error(f"Error writing aggregated file: {e}")
