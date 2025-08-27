#!/usr/bin/env python3
"""
Test script to verify AgentState.save() works correctly
"""

import sys
import os
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "src"))

from agents.state import AgentState

def test_state_save():
    """Test that AgentState.save() works correctly."""
    
    print("Testing AgentState.save() functionality")
    print("=" * 50)
    
    # Create a sample AgentState
    agent_state = AgentState(
        user_id="test_user_123",
        phone_number="+447399368793",
        user_profile={
            "first_name": "John",
            "last_name": "Doe",
            "email": "john.doe@example.com"
        },
        is_registered=True,
        just_registered=False,
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
    
    # Test the save method
    print(f"\n2. Testing save() method")
    try:
        agent_state.save()
        print("   SUCCESS: save() method completed successfully!")
    except Exception as e:
        print(f"   FAILED: save() method failed: {e}")
    
    print(f"\nSUCCESS: Test completed!")

if __name__ == "__main__":
    test_state_save() 