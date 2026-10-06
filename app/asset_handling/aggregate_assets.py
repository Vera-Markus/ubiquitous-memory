import json
import os
import glob
import logging
from pathlib import Path
from typing import Callable
from app.paths import RAW_DIR, GENERATED_DIR

logger = logging.getLogger("AggregateAssets")

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
