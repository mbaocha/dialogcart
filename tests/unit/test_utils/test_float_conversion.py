#!/usr/bin/env python3
"""
Test script to verify float conversion for DynamoDB compatibility
"""

import sys
import os
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "src"))

from utils.coreutil import convert_floats_for_dynamodb

def test_float_conversion():
    """Test that float conversion works correctly."""
    
    print("Testing float conversion for DynamoDB")
    print("=" * 50)
    
    # Test data with floats (like LangChain message objects)
    test_data = {
        "messages": [
            {
                "usage_metadata": {
                    "total_tokens": 815.0,
                    "output_tokens": 38.0,
                    "input_tokens": 777.0
                },
                "content": "Hello",
                "type": "ai"
            }
        ],
        "turns": 3.0,
        "previous_message_count": 2.0,
        "is_registered": True,
        "user_profile": {
            "first_name": "John",
            "last_name": "Doe"
        }
    }
    
    print(f"\n1. Original data with floats:")
    print(f"   total_tokens: {test_data['messages'][0]['usage_metadata']['total_tokens']} (type: {type(test_data['messages'][0]['usage_metadata']['total_tokens'])})")
    print(f"   turns: {test_data['turns']} (type: {type(test_data['turns'])})")
    
    # Convert floats
    converted_data = convert_floats_for_dynamodb(test_data)
    
    print(f"\n2. Converted data:")
    print(f"   total_tokens: {converted_data['messages'][0]['usage_metadata']['total_tokens']} (type: {type(converted_data['messages'][0]['usage_metadata']['total_tokens'])})")
    print(f"   turns: {converted_data['turns']} (type: {type(converted_data['turns'])})")
    
    # Verify conversion
    assert isinstance(converted_data['messages'][0]['usage_metadata']['total_tokens'], int)
    assert isinstance(converted_data['turns'], int)
    assert isinstance(converted_data['previous_message_count'], int)
    
    print(f"\nSUCCESS: All floats converted to integers successfully!")
    print(f"SUCCESS: Test passed!")

if __name__ == "__main__":
    test_float_conversion() 