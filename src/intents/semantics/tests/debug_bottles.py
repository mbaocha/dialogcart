#!/usr/bin/env python3
"""
Debug script to investigate why 'bottles' is not being captured as a unit.
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from nlp_processor import load_global_entities, build_support_maps, normalize_text_for_tokenization

def debug_bottles():
    print("=== DEBUGGING BOTTLES UNIT DETECTION ===\n")
    
    # Load entities
    print("1. Loading global entities...")
    entities = load_global_entities()
    print(f"   Loaded {len(entities)} entities")
    
    # Find bottle-related entities
    bottle_entities = [ent for ent in entities if ent.get("type") == "unit" and "bottle" in ent.get("canonical", "").lower()]
    print(f"   Found {len(bottle_entities)} bottle-related unit entities:")
    for ent in bottle_entities:
        print(f"     - {ent['canonical']}: {ent.get('synonyms', [])}")
    
    # Build support maps
    print("\n2. Building support maps...")
    unit_map, variant_map, product_map, brand_map, noise_set = build_support_maps(entities)
    
    # Check unit map
    bottle_keys = [k for k in unit_map.keys() if 'bottle' in k]
    print(f"   Unit map contains {len(bottle_keys)} bottle-related keys: {bottle_keys}")
    
    # Test text
    test_text = "Deliver 3kg Dangote rice tomorrow and 2 Peak milk bottles next week"
    print(f"\n3. Testing text: '{test_text}'")
    
    # Normalize text
    normalized = normalize_text_for_tokenization(test_text)
    print(f"   Normalized: '{normalized}'")
    
    # Check if bottles is in the normalized text
    tokens = normalized.split()
    print(f"   Tokens: {tokens}")
    
    # Check each token against unit_map
    print("\n4. Checking tokens against unit_map:")
    for i, token in enumerate(tokens):
        clean_token = token.rstrip('.,!?;:').lower()
        is_unit = clean_token in unit_map
        print(f"   Token {i}: '{token}' -> clean: '{clean_token}' -> is_unit: {is_unit}")
        if clean_token in ["bottles", "bottle"]:
            print(f"     -> Looking for '{clean_token}' in unit_map: {clean_token in unit_map}")
            print(f"     -> Unit map keys: {list(unit_map.keys())}")

if __name__ == "__main__":
    debug_bottles()
