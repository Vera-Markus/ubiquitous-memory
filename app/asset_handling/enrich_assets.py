import json
import os
import logging
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger("EnrichAssets")

async def enrich_assets_with_custom_names(
    raw_dir: str, 
    output_file: str, 
    esi_client: Any, 
    auth_service: Any
) -> bool:
    """
    Reads all .json files in raw_dir, aggregates them, enriches them with custom names 
    from ESI, and saves the result.
    """
    all_assets = []
    
    # 1. Aggregate assets from raw files
    json_files = list(Path(raw_dir).glob("*.json"))
    
    logger.info(f"Found {len(json_files)} JSON files in {raw_dir}")
    
    for file_path in json_files:
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, list):
                    for asset in data:
                        if isinstance(asset, dict):
                            char_id_str = file_path.stem
                            if char_id_str.isdigit():
                                asset['character_id'] = int(char_id_str)
                            all_assets.append(asset)
                elif isinstance(data, dict):
                    all_assets.append(data)
        except Exception as e:
            logger.error(f"Error reading {file_path.name}: {e}")

    if not all_assets:
        logger.warning("No assets found to enrich.")
        return False

    logger.info(f"Total assets aggregated: {len(all_assets)}")

    # 2. Prepare for enrichment
    # Group item_ids by character_id to make ESI calls
    char_to_item_ids: Dict[int, List[int]] = {}
    for asset in all_assets:
        char_id = asset.get('character_id')
        item_id = asset.get('item_id')
        if char_id is not None and item_id is not None:
            if char_id not in char_to_item_ids:
                char_to_item_ids[char_id] = []
            char_to_item_ids[char_id].append(item_id)

    # 3. Fetch custom names from ESI in batches
    logger.info("Starting ESI custom name enrichment...")
    
    # lookup: (character_id, item_id) -> custom_name
    name_lookup: Dict[tuple, str] = {}

    for char_id, item_ids in char_to_item_ids.items():
        # Remove duplicates for the request
        unique_ids = list(set(item_ids))
        
        # Batching up to 1000
        for i in range(0, len(unique_ids), 1000):
            batch = unique_ids[i:i+1000]
            url = f"/characters/{char_id}/assets/names"
            headers = {"Authorization": f"Bearer {auth_service.get_access_token()}", "Content-Type": "application/json"}
            
            try:
                logger.info(f"  - Fetching names for {char_id} (batch {i//1000 + 1})")
                response = await esi_client.client.post(
                    f"{esi_client.base_url}{url}",
                    json=batch,
                    headers=headers
                )
                
                if response.status_code == 200:
                    names_data = response.json()
                    for entry in names_data:
                        # entry is {'item_id': ..., 'name': ...}
                        name_lookup[(char_id, entry['item_id'])] = entry['name']
                else:
                    logger.error(f"  - ESI Error for {char_id}: {response.status_code} - {response.text}")
                    
            except Exception as e:
                logger.error(f"  - Request exception for {char_id}: {e}")

    # 4. Merge results back into assets
    logger.info("Merging custom names into asset records...")
    enriched_count = 0
    for asset in all_assets:
        char_id = asset.get('character_id')
        item_id = asset.get('item_id')
        
        if char_id is not None and item_id is not None:
            custom_name = name_lookup.get((char_id, item_id))
            if custom_name:
                asset['custom_name'] = custom_name
                enriched_count += 1

    logger.info(f"Enrichment complete. {enriched_count} assets enriched with custom names.")

    # 5. Save enriched assets
    try:
        os.makedirs(os.path.dirname(output_file), exist_ok=True)
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(all_assets, f, indent=4)
        logger.info(f"Successfully saved enriched assets to {output_file}")
        return True
    except Exception as e:
        logger.error(f"Error writing enriched file: {e}")
        return False
