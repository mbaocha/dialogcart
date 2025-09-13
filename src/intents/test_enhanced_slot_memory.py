#!/usr/bin/env python3
"""
Enhanced test script for cross-intent slot memory functionality
Tests memory across multiple intents: modify_cart, inquire_product, cart_action, etc.
"""
import requests
import json
import time

def test_cross_intent_memory():
    """Test slot memory across different intents"""
    url = "http://localhost:9000/classify"
    sender_id = "test_cross_intent"
    
    print("🧪 Testing Enhanced Cross-Intent Slot Memory")
    print("=" * 60)
    
    # Test Case 1: Cross-intent product memory
    print("\n📝 Test 1: Cross-Intent Product Memory")
    print("-" * 40)
    
    # Inquire about a product
    print("Message 1: 'How much is rice?'")
    response1 = requests.post(url, json={
        "text": "How much is rice?",
        "sender_id": sender_id,
        "validate": False
    })
    
    if response1.status_code == 200:
        result1 = response1.json()
        slots1 = result1.get("result", {}).get("slots", {})
        intent1 = result1.get("result", {}).get("intent")
        
        print(f"✅ Intent: {intent1}")
        print(f"✅ Last Inquired Product: {slots1.get('last_inquired_product', 'None')}")
        print(f"✅ Last Mentioned Product: {slots1.get('last_mentioned_product', 'None')}")
        print(f"✅ Last Inquiry Type: {slots1.get('last_inquiry_type', 'None')}")
        
        # Check if rice was stored in inquiry slots
        if slots1.get("last_inquired_product") == "rice":
            print("✅ Product correctly stored in inquiry slot!")
        else:
            print("❌ Product NOT stored in inquiry slot")
    else:
        print(f"❌ Error: {response1.status_code} - {response1.text}")
        return
    
    # Now add the same product to cart (should remember rice from inquiry)
    print(f"\nMessage 2: 'Add 2kg to cart'")
    response2 = requests.post(url, json={
        "text": "Add 2kg to cart",
        "sender_id": sender_id,
        "validate": False
    })
    
    if response2.status_code == 200:
        result2 = response2.json()
        slots2 = result2.get("result", {}).get("slots", {})
        intent2 = result2.get("result", {}).get("intent")
        
        print(f"✅ Intent: {intent2}")
        print(f"✅ Last Product Added: {slots2.get('last_product_added', 'None')}")
        print(f"✅ Last Mentioned Product: {slots2.get('last_mentioned_product', 'None')}")
        print(f"✅ Last Quantity: {slots2.get('last_quantity', 'None')}")
        print(f"✅ Shopping List: {slots2.get('shopping_list', [])}")
        
        # Check if contextual update worked (should use rice from previous inquiry)
        if (slots2.get("last_product_added") == "rice" and 
            slots2.get("last_quantity") == 2.0):
            print("✅ Cross-intent memory successful! System remembered 'rice' from inquiry and added 2kg")
        else:
            print("❌ Cross-intent memory failed")
    else:
        print(f"❌ Error: {response2.status_code} - {response2.text}")
    
    # Test Case 2: Cart action memory
    print("\n\n📝 Test 2: Cart Action Memory")
    print("-" * 35)
    
    sender_id2 = "test_cart_memory"
    
    # Show cart
    print("Message 1: 'Show my cart'")
    response1 = requests.post(url, json={
        "text": "Show my cart",
        "sender_id": sender_id2,
        "validate": False
    })
    
    if response1.status_code == 200:
        result1 = response1.json()
        slots1 = result1.get("result", {}).get("slots", {})
        intent1 = result1.get("result", {}).get("intent")
        
        print(f"✅ Intent: {intent1}")
        print(f"✅ Last Cart Action: {slots1.get('last_cart_action', 'None')}")
        print(f"✅ Cart State: {slots1.get('cart_state', 'None')}")
        print(f"✅ Last Container: {slots1.get('last_container', 'None')}")
        
        if slots1.get("cart_state") == "has_items":
            print("✅ Cart state correctly updated to 'has_items'!")
    else:
        print(f"❌ Error: {response1.status_code} - {response1.text}")
    
    # Clear cart
    print(f"\nMessage 2: 'Clear it'")
    response2 = requests.post(url, json={
        "text": "Clear it",
        "sender_id": sender_id2,
        "validate": False
    })
    
    if response2.status_code == 200:
        result2 = response2.json()
        slots2 = result2.get("result", {}).get("slots", {})
        intent2 = result2.get("result", {}).get("intent")
        
        print(f"✅ Intent: {intent2}")
        print(f"✅ Last Cart Action: {slots2.get('last_cart_action', 'None')}")
        print(f"✅ Cart State: {slots2.get('cart_state', 'None')}")
        
        if slots2.get("cart_state") == "empty":
            print("✅ Cart state correctly updated to 'empty'!")
    else:
        print(f"❌ Error: {response2.status_code} - {response2.text}")
    
    # Test Case 3: Checkout memory
    print("\n\n📝 Test 3: Checkout Memory")
    print("-" * 30)
    
    sender_id3 = "test_checkout"
    
    # Checkout with payment method
    print("Message 1: 'Checkout with credit card'")
    response1 = requests.post(url, json={
        "text": "Checkout with credit card",
        "sender_id": sender_id3,
        "validate": False
    })
    
    if response1.status_code == 200:
        result1 = response1.json()
        slots1 = result1.get("result", {}).get("slots", {})
        intent1 = result1.get("result", {}).get("intent")
        
        print(f"✅ Intent: {intent1}")
        print(f"✅ Payment Method: {slots1.get('payment_method', 'None')}")
        
        if slots1.get("payment_method") == "credit_card":
            print("✅ Payment method correctly stored!")
    else:
        print(f"❌ Error: {response1.status_code} - {response1.text}")
    
    # Test Case 4: Order tracking memory
    print("\n\n📝 Test 4: Order Tracking Memory")
    print("-" * 35)
    
    sender_id4 = "test_tracking"
    
    # Track order
    print("Message 1: 'Track order #12345'")
    response1 = requests.post(url, json={
        "text": "Track order #12345",
        "sender_id": sender_id4,
        "validate": False
    })
    
    if response1.status_code == 200:
        result1 = response1.json()
        slots1 = result1.get("result", {}).get("slots", {})
        intent1 = result1.get("result", {}).get("intent")
        
        print(f"✅ Intent: {intent1}")
        print(f"✅ Last Order ID: {slots1.get('last_order_id', 'None')}")
        
        if slots1.get("last_order_id") == "12345":
            print("✅ Order ID correctly extracted and stored!")
    else:
        print(f"❌ Error: {response1.status_code} - {response1.text}")
    
    # Test Case 5: Universal conversation context
    print("\n\n📝 Test 5: Universal Conversation Context")
    print("-" * 45)
    
    sender_id5 = "test_universal"
    
    test_messages = [
        ("How much is beans?", "inquire_product"),
        ("Add 3kg to cart", "modify_cart"),
        ("Show my cart", "cart_action"),
        ("Checkout with mobile money", "checkout")
    ]
    
    for i, (message, expected_intent) in enumerate(test_messages, 1):
        print(f"\nMessage {i}: '{message}'")
        response = requests.post(url, json={
            "text": message,
            "sender_id": sender_id5,
            "validate": False
        })
        
        if response.status_code == 200:
            result = response.json()
            slots = result.get("result", {}).get("slots", {})
            intent = result.get("result", {}).get("intent")
            turn = slots.get("conversation_turn", 0)
            
            print(f"✅ Intent: {intent}")
            print(f"✅ Conversation Turn: {turn}")
            print(f"✅ Last Intent: {slots.get('last_intent', 'None')}")
            print(f"✅ Last Mentioned Product: {slots.get('last_mentioned_product', 'None')}")
        else:
            print(f"❌ Error: {response.status_code} - {response.text}")
    
    print("\n" + "=" * 60)
    print("🎉 Enhanced Cross-Intent Slot Memory Testing Complete!")

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
    print("🚀 Starting Enhanced Slot Memory Tests")
    print("=" * 60)
    
    # Check if API is available
    if test_api_endpoints():
        time.sleep(1)  # Brief pause
        test_cross_intent_memory()
    else:
        print("\n❌ Cannot proceed with tests - API not available")
        print("\n📋 To start the service:")
        print("   cd src/intents")
        print("   python api/intent_classifier.py")
        print("\n   Then run this test script again.")
