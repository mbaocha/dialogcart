#!/usr/bin/env python3
"""
Test script to verify the utils.coreutil migration works correctly
"""

import sys
import os
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "src"))

from utils.coreutil import convert_floats_for_dynamodb, split_name

def test_utils_migration():
    """Test that the migrated functions work correctly."""
    
    print("Testing utils.coreutil migration")
    print("=" * 50)
    
    # Test convert_floats_for_dynamodb
    print(f"\n1. Testing convert_floats_for_dynamodb:")
    test_data = {
        "total_tokens": 815.0,
        "score": 3.14,
        "turns": 3.0
    }
    
    converted = convert_floats_for_dynamodb(test_data)
    print(f"   Original: {test_data}")
    print(f"   Converted: {converted}")
    
    # Verify conversion
    assert isinstance(converted["total_tokens"], int)
    assert isinstance(converted["score"], str)
    assert isinstance(converted["turns"], int)
    print(f"   SUCCESS: Float conversion works!")
    
    # Test split_name
    print(f"\n2. Testing split_name:")
    full_name = "John Doe Smith"
    first, last = split_name(full_name)
    print(f"   Full name: '{full_name}'")
    print(f"   First: '{first}', Last: '{last}'")
    
    assert first == "John"
    assert last == "Doe Smith"
    print(f"   SUCCESS: Name splitting works!")
    
    print(f"\nSUCCESS: All utils.coreutil functions work correctly!")

if __name__ == "__main__":
    test_utils_migration() 