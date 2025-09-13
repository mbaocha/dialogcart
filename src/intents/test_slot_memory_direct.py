#!/usr/bin/env python3
"""
Test slot memory directly without API
"""
from simple_slot_memory import slot_memory

def test_slot_memory_direct():
    """Test slot memory directly"""
    sender_id = "test_direct"
    
    print("🧪 Testing Slot Memory Directly")
    print("=" * 40)
    
    # Test 1: Inquire about rice
    print("\n1. Inquiring about rice...")
    entities1 = [{"entity": "product", "value": "rice"}, {"entity": "action", "value": "how much"}]
    slots1 = slot_memory.update_slots(sender_id, "inquire_product", entities1, "How much is rice?")
    
    print(f"✅ Slots after inquiry: {slots1}")
    
    if slots1.get("last_mentioned_product") == "rice":
        print("🎉 SUCCESS: Product memory working!")
    else:
        print("⚠️  Product memory not working")
    
    # Test 2: Add to cart
    print(f"\n2. Adding to cart...")
    entities2 = [{"entity": "quantity", "value": "2"}, {"entity": "unit", "value": "kg"}]
    slots2 = slot_memory.update_slots(sender_id, "modify_cart", entities2, "Add 2kg to cart")
    
    print(f"✅ Slots after cart add: {slots2}")
    
    if (slots2.get("last_product_added") == "rice" and 
        slots2.get("last_quantity") == 2.0):
        print("🎉 SUCCESS: Cross-intent memory working!")
    else:
        print("⚠️  Cross-intent memory needs improvement")
    
    # Test 3: Contextual update
    print(f"\n3. Contextual update...")
    entities3 = [{"entity": "quantity", "value": "5"}, {"entity": "unit", "value": "kg"}]
    slots3 = slot_memory.update_slots(sender_id, "modify_cart", entities3, "make it 5kg")
    
    print(f"✅ Slots after contextual update: {slots3}")
    
    if slots3.get("last_quantity") == 5.0:
        print("🎉 SUCCESS: Contextual update working!")
    else:
        print("⚠️  Contextual update needs improvement")
    
    # Test 4: Show current state
    print(f"\n4. Current slot state:")
    current_slots = slot_memory.get_slots(sender_id)
    print(f"✅ All slots: {current_slots}")

if __name__ == "__main__":
    test_slot_memory_direct()
