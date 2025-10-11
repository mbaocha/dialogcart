#!/usr/bin/env python3
"""
Test script to debug the bottles issue with a specific test case.
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from nlp_processor import init_nlp_with_entities, extract_entities_with_parameterization

def test_bottles_debug():
    print("=== TESTING BOTTLES DEBUG ===\n")
    
    # Initialize NLP and entities
    print("1. Initializing NLP and entities...")
    nlp, entities = init_nlp_with_entities()
    print(f"   Loaded {len(entities)} entities")
    
    # Test case
    test_text = "Add 20kg Dangote rice, 10 Peak milk bottles, but not the condensed milk"
    print(f"\n2. Testing text: '{test_text}'")
    
    # Run extraction with debug enabled
    print("\n3. Running extraction with debug...")
    result = extract_entities_with_parameterization(nlp, test_text, entities, debug_units=True)
    
    print(f"\n4. Final result:")
    print(f"   Units: {result['units']}")
    print(f"   Products: {result['products']}")
    print(f"   Brands: {result['brands']}")
    print(f"   Variants: {result['variants']}")

if __name__ == "__main__":
    test_bottles_debug()
