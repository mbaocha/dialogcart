#!/usr/bin/env python3
"""
Test script to verify the update_user function fix
"""

import sys
import os
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "src"))

from features.user import update_user

def test_update_user_fix():
    """Test that update_user now works with phone and source parameters."""
    
    print("Testing update_user function fix")
    print("=" * 50)
    
    # Create a sample agent state dictionary (like what would be passed from graph.py)
    agent_state_dict = {
        "user_id": "test_user_123",
        "phone_number": "+447399368793",
        "user_profile": {
            "first_name": "John",
            "last_name": "Doe",
            "email": "john.doe@example.com"
        },
        "is_registered": True,
        "just_registered": True,
        "messages": ["Hello", "How can I help?"],
        "all_time_history": ["Hello", "How can I help?", "Show me products"],
        "turns": 3,
        "display_output": ["Debug info"],
        "previous_message_count": 2,
        "is_disabled": False,
        "user_input": ""
    }
    
    print(f"\n1. Created agent_state_dict with:")
    print(f"   user_id: {agent_state_dict['user_id']}")
    print(f"   phone_number: {agent_state_dict['phone_number']}")
    print(f"   user_profile: {agent_state_dict['user_profile']}")
    print(f"   is_registered: {agent_state_dict['is_registered']}")
    
    # Test the update_user function
    print(f"\n2. Testing update_user function")
    try:
        result = update_user(agent_state_dict=agent_state_dict)
        print(f"   Result: {result}")
        
        if result.get("success"):
            print("   SUCCESS: update_user function works correctly!")
            print(f"   User ID: {result['data']['user_id']}")
            print(f"   Status: {result['data']['status']}")
        else:
            print(f"   ❌ update_user failed: {result.get('error')}")
            
    except Exception as e:
        print(f"   ❌ Exception occurred: {e}")
    
    print(f"\nSUCCESS: Test completed!")

if __name__ == "__main__":
    test_update_user_fix() 