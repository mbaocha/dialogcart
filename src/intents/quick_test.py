#!/usr/bin/env python3
"""
Quick test script for slot memory - tests basic functionality
"""
import requests
import json

def quick_test():
    """Quick test of slot memory functionality"""
    url = "http://localhost:9000/classify"
    sender_id = "quick_test"
    
    print("🧪 Quick Slot Memory Test")
    print("=" * 30)
    
    # Test 1: Basic product memory
    print("\n1. Testing product memory...")
    
    response = requests.post(url, json={
        "text": "How much is rice?",
        "sender_id": sender_id
    })
    
    if response.status_code == 200:
        result = response.json()
        slots = result.get("result", {}).get("slots", {})
        print(f"✅ Intent: {result.get('result', {}).get('intent')}")
        print(f"✅ Slots: {json.dumps(slots, indent=2)}")
    else:
        print(f"❌ Error: {response.status_code}")
    
    # Test 2: Cross-intent memory
    print("\n2. Testing cross-intent memory...")
    
    response = requests.post(url, json={
        "text": "Add 2kg to cart",
        "sender_id": sender_id  # Same sender_id
    })
    
    if response.status_code == 200:
        result = response.json()
        slots = result.get("result", {}).get("slots", {})
        print(f"✅ Intent: {result.get('result', {}).get('intent')}")
        print(f"✅ Slots: {json.dumps(slots, indent=2)}")
        
        # Check if rice was remembered
        if slots.get("last_product_added") == "rice":
            print("🎉 SUCCESS: Cross-intent memory working!")
        else:
            print("⚠️  Cross-intent memory may need training")
    else:
        print(f"❌ Error: {response.status_code}")

if __name__ == "__main__":
    try:
        quick_test()
    except requests.exceptions.ConnectionError:
        print("❌ Cannot connect to API. Make sure to start the service:")
        print("   python api/intent_classifier.py")
