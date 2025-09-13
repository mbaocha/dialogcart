#!/usr/bin/env python3
"""
Test the updated slot memory right now
"""
import requests
import json
import time

def test_slot_memory_now():
    """Test the slot memory that should now be working"""
    url = "http://localhost:9000/classify"
    sender_id = "test_now"
    
    print("🧪 Testing Updated Slot Memory")
    print("=" * 40)
    
    # Test 1: Inquire about a product
    print("\n1. Inquiring about rice...")
    response1 = requests.post(url, json={
        "text": "How much is rice?",
        "sender_id": sender_id
    })
    
    if response1.status_code == 200:
        result1 = response1.json()
        slots1 = result1.get("result", {}).get("slots", {})
        print(f"✅ Intent: {result1.get('result', {}).get('intent')}")
        print(f"✅ Slots: {json.dumps(slots1, indent=2)}")
        
        if slots1.get("last_mentioned_product") == "rice":
            print("🎉 SUCCESS: Product memory working!")
        else:
            print("⚠️  Product memory not working yet")
    else:
        print(f"❌ Error: {response1.status_code}")
    
    # Test 2: Cross-intent memory
    print(f"\n2. Adding product to cart...")
    response2 = requests.post(url, json={
        "text": "Add 2kg to cart",
        "sender_id": sender_id  # Same sender_id
    })
    
    if response2.status_code == 200:
        result2 = response2.json()
        slots2 = result2.get("result", {}).get("slots", {})
        print(f"✅ Intent: {result2.get('result', {}).get('intent')}")
        print(f"✅ Slots: {json.dumps(slots2, indent=2)}")
        
        # Check if cross-intent memory worked
        if (slots2.get("last_product_added") == "rice" and 
            slots2.get("last_quantity") == 2.0):
            print("🎉 SUCCESS: Cross-intent memory working!")
        else:
            print("⚠️  Cross-intent memory needs improvement")
    else:
        print(f"❌ Error: {response2.status_code}")
    
    # Test 3: Contextual update
    print(f"\n3. Testing contextual update...")
    response3 = requests.post(url, json={
        "text": "make it 5kg",
        "sender_id": sender_id
    })
    
    if response3.status_code == 200:
        result3 = response3.json()
        slots3 = result3.get("result", {}).get("slots", {})
        print(f"✅ Intent: {result3.get('result', {}).get('intent')}")
        print(f"✅ Slots: {json.dumps(slots3, indent=2)}")
        
        if slots3.get("last_quantity") == 5.0:
            print("🎉 SUCCESS: Contextual update working!")
        else:
            print("⚠️  Contextual update needs improvement")
    else:
        print(f"❌ Error: {response3.status_code}")

if __name__ == "__main__":
    print("Waiting for service to start...")
    time.sleep(3)  # Give the service time to start
    
    try:
        test_slot_memory_now()
    except requests.exceptions.ConnectionError:
        print("❌ Cannot connect to API. Service might still be starting...")
        print("Try running this test again in a few seconds.")
