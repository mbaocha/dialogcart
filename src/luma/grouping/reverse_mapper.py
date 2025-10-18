"""
Reverse mapping utilities for converting indexed tokens back to original entities.

Handles mapping producttoken_1 → rice, brandtoken_2 → nike, etc.
Ported from semantics/entity_extraction_pipeline.py lines 212-307.
"""
import re
import json
from typing import Dict, Any, List, Optional

# Configuration
from luma.config import config, debug_print


def map_tokens_to_original_values(
    grouped_result: Dict[str, Any],
    nlp_result: Dict[str, Any],
    index_map: Optional[Dict[str, str]] = None
) -> Dict[str, Any]:
    """
    Map parameterized tokens back to their original values.
    
    Converts indexed tokens (producttoken_1, brandtoken_2) back to actual
    entity values (rice, nike) using the entity pools from NLP extraction.
    
    Args:
        grouped_result: Result from grouper with indexed tokens
        nlp_result: Original NLP extraction result with entity lists
        index_map: Optional mapping of indexed → base tokens
        
    Returns:
        Grouped result with original entity values
        
    NOTE: Ported from semantics/entity_extraction_pipeline.py lines 212-279
    """
    def dbg(msg, data=None):
        if not config.DEBUG_ENABLED:
            return
        debug_print(f"[DEBUG] {msg}")
        if data is not None:
            try:
                debug_print(json.dumps(data, indent=2))
            except TypeError:
                debug_print(data)
        debug_print("-" * 60)
    
    # Step 1: Extract raw NLP results
    nlp_products = nlp_result.get("products", [])
    nlp_brands = nlp_result.get("brands", [])
    nlp_units = nlp_result.get("units", [])
    nlp_variants = nlp_result.get("variants", [])
    
    # Create entity pools (copy ensures safe reads)
    product_pool = nlp_products.copy()
    brand_pool = nlp_brands.copy()
    unit_pool = nlp_units.copy()
    variant_pool = nlp_variants.copy()
    
    dbg("map_tokens_to_original_values INPUT", {
        "nlp_products": nlp_products,
        "nlp_brands": nlp_brands,
        "nlp_units": nlp_units,
        "nlp_variants": nlp_variants
    })
    
    # Step 2: Iterate over groups and map tokens
    if grouped_result.get("groups"):
        for gi, entity_group in enumerate(grouped_result["groups"]):
            dbg(f"Mapping group {gi+1} (before)", entity_group)
            
            # Map products
            if "products" in entity_group:
                entity_group["products"] = [
                    _map_indexed_token(token, "producttoken", product_pool, index_map)
                    for token in entity_group["products"]
                ]
            
            # Map brands
            if "brands" in entity_group:
                entity_group["brands"] = [
                    _map_indexed_token(token, "brandtoken", brand_pool, index_map)
                    for token in entity_group["brands"]
                ]
            
            # Map units
            if "units" in entity_group:
                entity_group["units"] = [
                    _map_indexed_token(token, "unittoken", unit_pool, index_map)
                    for token in entity_group["units"]
                ]
            
            # Map variants
            if "variants" in entity_group:
                entity_group["variants"] = [
                    _map_indexed_token(token, "varianttoken", variant_pool, index_map)
                    for token in entity_group["variants"]
                ]
            
            dbg(f"Mapping group {gi+1} (after)", entity_group)
    
    return grouped_result


def _map_indexed_token(
    token: str,
    base_type: str,
    entity_pool: List[str],
    index_map: Optional[Dict[str, str]] = None
) -> str:
    """
    Map an indexed token back to its original value using index-based mapping.
    
    Handles:
    - producttoken_2 → entity_pool[1] (0-based indexing)
    - producttoken → entity_pool[0] (default to first)
    - If index out of range, fallback to first entity
    - If no pool, return token as-is
    
    Args:
        token: Token to map (e.g., "producttoken_2", "brandtoken_1")
        base_type: Base token type (e.g., "producttoken", "brandtoken")
        entity_pool: List of original entities from NLP extraction
        index_map: Optional index mapping dict
        
    Returns:
        Original entity value or token if no mapping found
        
    NOTE: Ported from semantics/entity_extraction_pipeline.py lines 282-307
    """
    # Handle indexed tokens (e.g., 'producttoken_2')
    m = re.match(rf"^{base_type}_(\d+)$", token)
    if m:
        idx = int(m.group(1)) - 1  # Convert 1-based → 0-based
        if 0 <= idx < len(entity_pool):
            debug_print(f"[DEBUG] _map_indexed_token: {token} → {entity_pool[idx]}")
            return entity_pool[idx]
        elif entity_pool:
            debug_print(f"[WARN] _map_indexed_token: {token} index out of range, fallback to {entity_pool[0]}")
            return entity_pool[0]
    
    # Handle non-indexed base token (e.g., 'producttoken')
    if token == base_type and entity_pool:
        debug_print(f"[DEBUG] _map_indexed_token: {token} (non-indexed) → {entity_pool[0]}")
        return entity_pool[0]
    
    # Return as-is if no mapping found
    debug_print(f"[DEBUG] _map_indexed_token: {token} no match, return as-is")
    return token

