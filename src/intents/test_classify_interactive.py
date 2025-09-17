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

def test_classify(text, sender_id="test_user0", validate=False):
    """Test the classify endpoint with given parameters
    
    Args:
        text: Text to classify
        sender_id: Sender identifier
        validate: Validation mode - False, True, or "force"
    """
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
            
            # Show validation info if available
            if "result" in result:
                result_data = result["result"]
                if "validation_performed" in result_data:
                    print(f"\n🔍 Validation: {'Performed' if result_data['validation_performed'] else 'Not performed'}")
                if "source" in result_data:
                    print(f"📡 Source: {result_data['source']}")
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
    print("  - 'validate' - cycle through validation modes (false -> true -> force -> false)")
    print("  - 'validate false' - set validation to false")
    print("  - 'validate true' - set validation to true")
    print("  - 'validate force' - force LLM validation")
    print("  - 'sender <id>' - set sender ID")
    print("  - 'examples' - show example inputs")
    print("  - 'quit' or 'exit' - exit")
    print("=" * 50)
    
    validate = False
    sender_id = "test_user0"
    validation_modes = [False, True, "force"]
    current_mode_index = 0
    
    while True:
        try:
            # Display current validation mode
            validate_display = f"[{validate}]" if validate else "[OFF]"
            user_input = input(f"\n[{sender_id}] {validate_display} > ").strip()
            
            if user_input.lower() in ['quit', 'exit', 'q']:
                print("👋 Goodbye!")
                break
            elif user_input.lower() == 'validate':
                # Cycle through validation modes
                current_mode_index = (current_mode_index + 1) % len(validation_modes)
                validate = validation_modes[current_mode_index]
                print(f"🔄 Validation: {validate}")
            elif user_input.lower().startswith('validate '):
                # Set specific validation mode
                mode = user_input[9:].strip().lower()
                if mode == 'false':
                    validate = False
                    current_mode_index = 0
                    print("🔄 Validation: False")
                elif mode == 'true':
                    validate = True
                    current_mode_index = 1
                    print("🔄 Validation: True")
                elif mode == 'force':
                    validate = "force"
                    current_mode_index = 2
                    print("🔄 Validation: Force")
                else:
                    print("❓ Invalid validation mode. Use: false, true, or force")
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
        args = sys.argv[1:]
        
        # Parse arguments for validation mode
        validate = False
        sender_id = "test_user0"
        text_args = []
        
        i = 0
        while i < len(args):
            if args[i] == "--validate" and i + 1 < len(args):
                validate_arg = args[i + 1].lower()
                if validate_arg == "false":
                    validate = False
                elif validate_arg == "true":
                    validate = True
                elif validate_arg == "force":
                    validate = "force"
                else:
                    print(f"❌ Invalid validation mode: {args[i + 1]}. Use: false, true, or force")
                    return
                i += 2
            elif args[i] == "--sender" and i + 1 < len(args):
                sender_id = args[i + 1]
                i += 2
            else:
                text_args.append(args[i])
                i += 1
        
        if not text_args:
            print("❌ Please provide text to classify")
            print("Usage: python test_classify_interactive.py [--validate false|true|force] [--sender <id>] <text>")
            return
            
        text = " ".join(text_args)
        test_classify(text, sender_id, validate)
    else:
        # Interactive mode
        interactive_mode()

if __name__ == "__main__":
    main()
