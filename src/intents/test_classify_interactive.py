#!/usr/bin/env python3
"""
Interactive test script for the classify endpoint
"""
import requests
import json
import sys

# Configuration
API_URL = "http://localhost:9000/classify"
HEADERS = {"Content-Type": "application/json"}

def test_classify(text, sender_id="test_user", validate=False):
    """Test the classify endpoint with given parameters"""
    payload = {
        "text": text,
        "sender_id": sender_id,
        "validate": validate
    }
    
    try:
        print(f"\n🔍 Testing: '{text}'")
        print(f"📤 Payload: {json.dumps(payload, indent=2)}")
        
        response = requests.post(API_URL, headers=HEADERS, json=payload)
        
        print(f"📊 Status: {response.status_code}")
        
        if response.status_code == 200:
            result = response.json()
            print("✅ Success!")
            print(f"📋 Result: {json.dumps(result, indent=2)}")
            
            # Pretty print actions if they exist
            if "result" in result and "actions" in result["result"]:
                actions = result["result"]["actions"]
                print(f"\n🎯 Actions ({len(actions)}):")
                for i, action in enumerate(actions, 1):
                    print(f"  {i}. {action.get('action', 'N/A')} {action.get('product', 'N/A')} "
                          f"(qty: {action.get('quantity', 'N/A')}, unit: {action.get('unit', 'N/A')})")
        else:
            print("❌ Error!")
            print(f"Response: {response.text}")
            
    except requests.exceptions.ConnectionError:
        print("❌ Connection Error: Make sure the server is running on http://localhost:9000")
    except Exception as e:
        print(f"❌ Error: {e}")

def interactive_mode():
    """Run in interactive mode"""
    print("🚀 Interactive Classify Endpoint Tester")
    print("=" * 50)
    print("Commands:")
    print("  - Type your text to test")
    print("  - 'validate' - toggle validation on/off")
    print("  - 'sender <id>' - set sender ID")
    print("  - 'examples' - show example inputs")
    print("  - 'quit' or 'exit' - exit")
    print("=" * 50)
    
    validate = False
    sender_id = "test_user"
    
    while True:
        try:
            user_input = input(f"\n[{sender_id}] {'[VALIDATE]' if validate else ''} > ").strip()
            
            if user_input.lower() in ['quit', 'exit', 'q']:
                print("👋 Goodbye!")
                break
            elif user_input.lower() == 'validate':
                validate = not validate
                print(f"🔄 Validation: {'ON' if validate else 'OFF'}")
            elif user_input.lower().startswith('sender '):
                sender_id = user_input[7:].strip()
                print(f"👤 Sender ID: {sender_id}")
            elif user_input.lower() == 'examples':
                show_examples()
            elif user_input:
                test_classify(user_input, sender_id, validate)
            else:
                print("❓ Please enter some text to test")
                
        except KeyboardInterrupt:
            print("\n👋 Goodbye!")
            break
        except EOFError:
            print("\n👋 Goodbye!")
            break

def show_examples():
    """Show example test cases"""
    examples = [
        "add rice to cart",
        "remove 2 apples",
        "change rice to 4 cartons",
        "+ ancarton flour, change rice to 4 carton; dec noodles 2 carton",
        "remove yam, add 2g garri to cart",
        "remove yam, 2g garri to cart",
        "add 3kg sugar and 2 bottles of water",
        "set rice to 5kg",
        "clear cart",
        "show me my cart"
    ]
    
    print("\n📝 Example test cases:")
    for i, example in enumerate(examples, 1):
        print(f"  {i}. {example}")

def main():
    """Main function"""
    if len(sys.argv) > 1:
        # Command line mode
        text = " ".join(sys.argv[1:])
        test_classify(text)
    else:
        # Interactive mode
        interactive_mode()

if __name__ == "__main__":
    main()
