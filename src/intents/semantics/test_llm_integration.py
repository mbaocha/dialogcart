#!/usr/bin/env python3
"""
Test script for LLM integration in entity extraction pipeline.
This script tests both the original functionality and the new LLM fallback.
"""

import os
import sys

# Add the current directory to the path to import local modules
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(current_dir)

from entity_extraction_pipeline import (
    extract_entities, 
    extract_entities_smart, 
    extract_entities_with_llm_fallback,
    warmup_models
)

def test_basic_functionality():
    """Test that basic functionality still works without LLM."""
    print("🧪 Testing basic functionality (no LLM)...")
    
    # Simple case that should work with standard pipeline
    result = extract_entities("add 2 kg rice to cart")
    print(f"Status: {result['status']}")
    print(f"Groups: {len(result['grouped_entities'].get('groups', []))}")
    
    if result['status'] == 'success':
        print("✅ Basic functionality works")
        return True
    else:
        print("❌ Basic functionality failed")
        return False

def test_llm_fallback():
    """Test LLM fallback functionality."""
    print("\n🤖 Testing LLM fallback...")
    
    # Test case that might trigger needs_llm_fix
    test_cases = [
        "add some rice and 2 kg of beans",  # Complex case
        "add it to cart",  # Pronoun case
        "add rice and beans and yam",  # Multiple products
    ]
    
    for sentence in test_cases:
        print(f"\nTesting: '{sentence}'")
        
        # Test with LLM fallback
        result = extract_entities_smart(sentence)
        print(f"Status: {result['status']}")
        
        if result.get('llm_fallback', {}).get('used'):
            print("✅ LLM fallback was used")
            print(f"LLM processing time: {result['llm_fallback'].get('elapsed_seconds', 0)}s")
        else:
            print("ℹ️  Standard pipeline handled this case")
        
        if result['grouped_entities'].get('groups'):
            print(f"Extracted {len(result['grouped_entities']['groups'])} groups")
            for i, group in enumerate(result['grouped_entities']['groups']):
                print(f"  Group {i+1}: {group.get('intent', 'N/A')} - {group.get('products', [])}")
        else:
            print("No groups extracted")

def test_non_breaking():
    """Test that existing API is not broken."""
    print("\n🔒 Testing non-breaking changes...")
    
    # Test original function still works
    result1 = extract_entities("add rice to cart")
    result2 = extract_entities_smart("add rice to cart", use_llm_fallback=False)
    
    # Both should produce similar results
    if result1['status'] == result2['status']:
        print("✅ Original API still works")
        return True
    else:
        print("❌ Original API broken")
        return False

def main():
    """Run all tests."""
    print("🚀 Testing LLM Integration in Entity Extraction Pipeline")
    print("=" * 60)
    
    # Warm up models
    print("🔥 Warming up models...")
    warmup_models()
    
    # Run tests
    basic_ok = test_basic_functionality()
    test_llm_fallback()
    non_breaking_ok = test_non_breaking()
    
    print("\n" + "=" * 60)
    if basic_ok and non_breaking_ok:
        print("🎉 All tests passed! LLM integration is working correctly.")
    else:
        print("⚠️  Some tests failed. Check the output above.")

if __name__ == "__main__":
    main()
