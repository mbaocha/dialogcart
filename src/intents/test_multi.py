"""
Test script for multi-intent functionality
"""
import requests
import json

UNIFIED_URL = "http://localhost:9000"
LLM_URL = "http://localhost:9100"

def test_multi_intent():
    """Test multi-intent classification"""
    text = "remove yam from cart and add 8kg beans to cart"
    
    print(f"Testing: {text}")
    print("=" * 50)
    
    # Test unified API single-intent (backwards compatible)
    print("1. Unified API (single-intent):")
    try:
        response = requests.post(f"{UNIFIED_URL}/classify", json={"text": text})
        if response.status_code == 200:
            result = response.json()
            print(f"   Source: {result['result']['source']}")
            print(f"   Intent: {result['result']['intent_meta']['intent']}")
            print(f"   Confidence: {result['result']['intent_meta']['confidence']}")
            print(f"   Entities: {result['result']['intent_meta']['entities']}")
        else:
            print(f"   Error: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"   Error: {e}")
    
    print()
    
    # Test unified API multi-intent
    print("2. Unified API (multi-intent):")
    try:
        response = requests.post(f"{UNIFIED_URL}/classify-multi", json={"text": text})
        if response.status_code == 200:
            result = response.json()
            print(f"   Source: {result['result']['source']}")
            if 'intents' in result['result']:
                print(f"   Intents ({len(result['result']['intents'])}):")
                for i, intent in enumerate(result['result']['intents']):
                    print(f"     {i+1}. {intent['intent']} ({intent['confidence']})")
                    print(f"        Entities: {intent['entities']}")
            else:
                print(f"   Intent: {result['result']['intent_meta']['intent']}")
                print(f"   Confidence: {result['result']['intent_meta']['confidence']}")
        else:
            print(f"   Error: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"   Error: {e}")
    
    print()
    
    # Test LLM service directly
    print("3. LLM Service (multi-intent):")
    try:
        response = requests.post(f"{LLM_URL}/classify", json={"text": text})
        if response.status_code == 200:
            result = response.json()
            print(f"   Intents ({len(result['result']['intents'])}):")
            for i, intent in enumerate(result['result']['intents']):
                print(f"     {i+1}. {intent['intent']} ({intent['confidence']})")
                print(f"        Entities: {intent['entities']}")
        else:
            print(f"   Error: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"   Error: {e}")

def test_single_intent():
    """Test single-intent classification"""
    text = "add 5kg rice to cart"
    
    print(f"\nTesting single intent: {text}")
    print("=" * 50)
    
    # Test unified API single-intent
    print("1. Unified API (single-intent):")
    try:
        response = requests.post(f"{UNIFIED_URL}/classify", json={"text": text})
        if response.status_code == 200:
            result = response.json()
            print(f"   Source: {result['result']['source']}")
            print(f"   Intent: {result['result']['intent_meta']['intent']}")
            print(f"   Confidence: {result['result']['intent_meta']['confidence']}")
            print(f"   Entities: {result['result']['intent_meta']['entities']}")
        else:
            print(f"   Error: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"   Error: {e}")
    
    print()
    
    # Test LLM service single-intent
    print("2. LLM Service (single-intent):")
    try:
        response = requests.post(f"{LLM_URL}/classify-single", json={"text": text})
        if response.status_code == 200:
            result = response.json()
            print(f"   Intent: {result['result']['intent']}")
            print(f"   Confidence: {result['result']['confidence']}")
            print(f"   Entities: {result['result']['entities']}")
        else:
            print(f"   Error: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"   Error: {e}")

if __name__ == "__main__":
    print("Multi-Intent Classification Test")
    print("=" * 50)
    
    test_multi_intent()
    test_single_intent()
    
    print("\nTest completed!")
