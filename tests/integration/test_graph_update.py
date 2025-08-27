#!/usr/bin/env python3
"""
Test script to verify the updated graph.py works with new update_user function
"""

import sys
import os
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "src"))

from agents.state import AgentState
from features.user import update_user

def test_graph_update():
    """Test that the graph.py update_user call works correctly."""
    
    print("Testing graph.py update_user integration")
    print("=" * 50)
    
    # Create a sample AgentState (similar to what would be in graph.py)
    agent_state = AgentState(
        user_id="test_user_123",
        phone_number="+447399368793",
        user_profile={
            "first_name": "John",
            "last_name": "Doe",
            "email": "john.doe@example.com"
        },
        is_registered=True,
        just_registered=True,
        messages=["Hello", "How can I help?"],
        all_time_history=["Hello", "How can I help?", "Show me products"],
        turns=3,
        display_output=["Debug info"],
        previous_message_count=2,
        is_disabled=False,
        user_input=""
    )
    
    print(f"\n1. Created AgentState with user_id: {agent_state.user_id}")
    print(f"   Phone: {agent_state.phone_number}")
    print(f"   Profile: {agent_state.user_profile}")
    print(f"   Registered: {agent_state.is_registered}")
    
    # Simulate the graph.py update_user call
    print(f"\n2. Converting to dictionary and calling update_user")
    agent_state_dict = agent_state.model_dump()
    
    # Simulate updating user_profile (like in graph.py)
    updated_user_profile = {
        "first_name": "Updated",
        "last_name": "User",
        "email": "updated@example.com"
    }
    agent_state_dict["user_profile"] = updated_user_profile
    
    print(f"   Updated profile: {agent_state_dict['user_profile']}")
    
    # Call update_user with the dictionary
    try:
        update_result = update_user(agent_state_dict=agent_state_dict)
        print(f"\n3. Update result: {update_result}")
        
        if update_result.get("success"):
            print("   SUCCESS: update_user call successful!")
            print(f"   User ID: {update_result['data']['user_id']}")
            print(f"   Status: {update_result['data']['status']}")
        else:
            print(f"   ❌ Update failed: {update_result.get('error')}")
            
    except Exception as e:
        print(f"   ❌ Exception occurred: {e}")
    
    print(f"\nSUCCESS: Test completed!")

if __name__ == "__main__":
    test_graph_update() 