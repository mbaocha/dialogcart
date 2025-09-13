#!/usr/bin/env python3
"""
Test script to verify slot memory functionality
Tests the conversation: "add 4kg rice to cart" → "make it 8kg"
"""
import requests
import json
import time

def test_slot_memory():
    """Test slot memory functionality with contextual updates"""
    url = "http://localhost:9000/classify"
    sender_id = "test_user_slot_memory"
    
    print("🧪 Testing Slot Memory Functionality")
    print("=" * 50)
    
    # Test Case 1: Basic slot memory
    print("\n📝 Test 1: Basic Slot Memory")
    print("-" * 30)
    
    # First message: "add 4kg rice to cart"
    print(f"Message 1: 'add 4kg rice to cart'")
    response1 = requests.post(url, json={
        "text": "add 4kg rice to cart",
        "sender_id": sender_id,
        "validate": False
    })
    
    if response1.status_code == 200:
        result1 = response1.json()
        slots1 = result1.get("result", {}).get("slots", {})
        intent1 = result1.get("result", {}).get("intent")
        entities1 = result1.get("result", {}).get("entities", [])
        
        print(f"✅ Intent: {intent1}")
        print(f"✅ Entities: {[e.get('entity') + '=' + e.get('value') for e in entities1]}")
        print(f"✅ Slots: {json.dumps(slots1, indent=2)}")
        
        # Check if rice was stored in slots
        if slots1.get("last_product_added") == "rice":
            print("✅ Product correctly stored in slot!")
        else:
            print("❌ Product NOT stored in slot")
            
    else:
        print(f"❌ Error: {response1.status_code} - {response1.text}")
        return
    
    # Second message: "make it 8kg"
    print(f"\nMessage 2: 'make it 8kg'")
    response2 = requests.post(url, json={
        "text": "make it 8kg",
        "sender_id": sender_id,  # Same sender_id for memory
        "validate": False
    })
    
    if response2.status_code == 200:
        result2 = response2.json()
        slots2 = result2.get("result", {}).get("slots", {})
        intent2 = result2.get("result", {}).get("intent")
        entities2 = result2.get("result", {}).get("entities", [])
        
        print(f"✅ Intent: {intent2}")
        print(f"✅ Entities: {[e.get('entity') + '=' + e.get('value') for e in entities2]}")
        print(f"✅ Slots: {json.dumps(slots2, indent=2)}")
        
        # Check if contextual update worked
        if (slots2.get("last_product_added") == "rice" and 
            slots2.get("last_quantity") == 8.0 and 
            slots2.get("last_unit") == "kg"):
            print("✅ Contextual update successful! System remembered 'rice' and updated to '8kg'")
        else:
            print("❌ Contextual update failed")
            print(f"   Expected: last_product_added='rice', last_quantity=8.0, last_unit='kg'")
            print(f"   Got: last_product_added='{slots2.get('last_product_added')}', last_quantity={slots2.get('last_quantity')}, last_unit='{slots2.get('last_unit')}'")
            
    else:
        print(f"❌ Error: {response2.status_code} - {response2.text}")
    
    # Test Case 2: Multiple products and updates
    print("\n\n📝 Test 2: Multiple Products and Updates")
    print("-" * 40)
    
    sender_id2 = "test_user_multi"
    
    # Add multiple products
    products = [
        "add 2kg beans",
        "add 5 bottles of milk", 
        "make it 10 bottles"
    ]
    
    for i, product_msg in enumerate(products, 1):
        print(f"\nMessage {i}: '{product_msg}'")
        response = requests.post(url, json={
            "text": product_msg,
            "sender_id": sender_id2,
            "validate": False
        })
        
        if response.status_code == 200:
            result = response.json()
            slots = result.get("result", {}).get("slots", {})
            intent = result.get("result", {}).get("intent")
            
            print(f"✅ Intent: {intent}")
            print(f"✅ Last Product: {slots.get('last_product_added', 'None')}")
            print(f"✅ Last Quantity: {slots.get('last_quantity', 'None')}")
            print(f"✅ Last Unit: {slots.get('last_unit', 'None')}")
            print(f"✅ Shopping List: {slots.get('shopping_list', [])}")
        else:
            print(f"❌ Error: {response.status_code} - {response.text}")
    
    # Test Case 3: Conversation turn tracking
    print("\n\n📝 Test 3: Conversation Turn Tracking")
    print("-" * 40)
    
    sender_id3 = "test_user_turns"
    
    for i in range(3):
        message = f"add {i+1}kg rice" if i == 0 else f"make it {i+2}kg"
        print(f"\nTurn {i+1}: '{message}'")
        
        response = requests.post(url, json={
            "text": message,
            "sender_id": sender_id3,
            "validate": False
        })
        
        if response.status_code == 200:
            result = response.json()
            slots = result.get("result", {}).get("slots", {})
            turn_count = slots.get("conversation_turn", 0)
            
            print(f"✅ Conversation Turn: {turn_count}")
            print(f"✅ Last Intent: {slots.get('last_intent', 'None')}")
        else:
            print(f"❌ Error: {response.status_code} - {response.text}")
    
    print("\n" + "=" * 50)
    print("🎉 Slot Memory Testing Complete!")

def test_api_endpoints():
    """Test that the API is running and responsive"""
    url = "http://localhost:9000/classify"
    
    print("🔍 Testing API Availability")
    print("-" * 30)
    
    try:
        response = requests.post(url, json={
            "text": "hello",
            "sender_id": "test_api",
            "validate": False
        }, timeout=5)
        
        if response.status_code == 200:
            print("✅ API is running and responsive")
            return True
        else:
            print(f"❌ API returned status {response.status_code}")
            return False
            
    except requests.exceptions.RequestException as e:
        print(f"❌ API not available: {e}")
        print("💡 Make sure to start the service with: python src/intents/api/intent_classifier.py")
        return False

if __name__ == "__main__":
    print("🚀 Starting Slot Memory Tests")
    print("=" * 50)
    
    # Check if API is available
    if test_api_endpoints():
        time.sleep(1)  # Brief pause
        test_slot_memory()
    else:
        print("\n❌ Cannot proceed with tests - API not available")
        print("\n📋 To start the service:")
        print("   cd src/intents")
        print("   python api/intent_classifier.py")
        print("\n   Then run this test script again.")
