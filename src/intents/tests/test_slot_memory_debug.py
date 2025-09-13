#!/usr/bin/env python3
"""
Debug script to test slot memory functionality.
"""

import sys
from pathlib import Path

# Add the src directory to the path
sys.path.append(str(Path(__file__).parent.parent.parent))

def test_slot_memory():
    """Test slot memory functionality."""
    try:
        from intents.simple_slot_memory import SimpleSlotMemory
        
        print("=== Slot Memory Debug Test ===")
        
        # Create slot memory
        slot_memory = SimpleSlotMemory()
        sender_id = "test_user"
        
        print(f"Slot memory instance: {id(slot_memory)}")
        
        # Test 1: Initial state
        print("\n--- Test 1: Initial state ---")
        slots1 = slot_memory.get_slots(sender_id)
        print(f"Initial slots: {slots1}")
        
        # Test 2: First update
        print("\n--- Test 2: First update ---")
        slots2 = slot_memory.update_slots(sender_id, "modify_cart", [], "add rice")
        print(f"After first update: {slots2}")
        
        # Test 3: Second update
        print("\n--- Test 3: Second update ---")
        slots3 = slot_memory.update_slots(sender_id, "modify_cart", [], "add beans")
        print(f"After second update: {slots3}")
        
        # Test 4: Third update
        print("\n--- Test 4: Third update ---")
        slots4 = slot_memory.update_slots(sender_id, "modify_cart", [], "add milk")
        print(f"After third update: {slots4}")
        
        # Test 5: Check final state
        print("\n--- Test 5: Final state ---")
        final_slots = slot_memory.get_slots(sender_id)
        print(f"Final slots: {final_slots}")
        
        # Test 6: Check if conversation turn is incrementing
        print("\n--- Test 6: Conversation turn check ---")
        expected_turns = [1, 2, 3]
        actual_turns = [slots2.get("conversation_turn", 0), slots3.get("conversation_turn", 0), slots4.get("conversation_turn", 0)]
        print(f"Expected turns: {expected_turns}")
        print(f"Actual turns: {actual_turns}")
        
        if actual_turns == expected_turns:
            print("✅ Slot memory is working correctly!")
            return True
        else:
            print("❌ Slot memory has issues!")
            return False
            
    except Exception as e:
        print(f"❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = test_slot_memory()
    sys.exit(0 if success else 1)
