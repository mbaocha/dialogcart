#!/usr/bin/env python3
"""
Test script for update_user functionality
"""

import sys
import os
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "src"))

from features.user import register_user, update_user, lookup_user_by_phone

def test_update_user():
    """Test the update_user functionality."""
    phone_number = "+447399368793"
    
    print("Testing update_user functionality")
    print("=" * 50)
    
    # Step 1: First, check if user exists
    print(f"\n1. Checking if user exists: {phone_number}")
    user_result = lookup_user_by_phone(phone_number)
    print(f"   Result: {user_result}")
    
    if not user_result.get("success"):
        print("   ❌ User not found, cannot test update")
        return
    
    existing_user = user_result["data"]
    user_id = existing_user["user_id"]
    print(f"   ✅ Found existing user: {user_id}")
    
    # Step 2: Update the user with an AgentState dictionary
    print(f"\n2. Updating user with AgentState dictionary")
    
    agent_state_dict = {
        "user_id": user_id,
        "phone_number": phone_number,
        "user_profile": {
            "first_name": "Updated",
            "last_name": "User", 
            "email": "updated@example.com"
        },
        "is_registered": True,
        "just_registered": False,
        "messages": ["Hello", "How can I help?"],
        "all_time_history": ["Hello", "How can I help?", "Show me products"],
        "turns": 5,
        "display_output": ["Debug info"],
        "previous_message_count": 3,
        "is_disabled": False,
        "user_input": ""
    }
    
    update_result = update_user(agent_state_dict=agent_state_dict)
    
    print(f"   Update result: {update_result}")
    
    if update_result.get("success"):
        print("   SUCCESS: User updated successfully")
        
        # Step 3: Verify the update
        print(f"\n3. Verifying the update")
        verify_result = lookup_user_by_phone(phone_number)
        print(f"   Verification result: {verify_result}")
        
        if verify_result.get("success"):
            updated_user = verify_result["data"]
            print(f"   SUCCESS: User verified:")
            print(f"      - First Name: {updated_user.get('first_name')}")
            print(f"      - Last Name: {updated_user.get('last_name')}")
            print(f"      - Email: {updated_user.get('email')}")
            print(f"      - Status: {updated_user.get('status')}")
            print(f"      - State Data: {updated_user.get('state_data')}")
            print(f"      - Chat Summary: {updated_user.get('chat_summary')}")
    else:
        print(f"   ❌ Update failed: {update_result.get('error')}")

if __name__ == "__main__":
    test_update_user() 