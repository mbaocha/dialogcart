#!/usr/bin/env python3
"""
Rasa and Validator test runner for all modify_cart_200_scenarios.jsonl tests.

This test executes all 200 scenarios and shows results for both Rasa and validator.
"""

import sys
from pathlib import Path

# Add the src directory to the path
sys.path.append(str(Path(__file__).parent.parent.parent))

def run_rasa_validator_test():
    """Run all 200 scenarios and show Rasa + validator results."""
    try:
        from intents.core.validator import (
            validate_modify_cart,
            extract_verbs_with_synonyms,
            extract_quantities,
            extract_products,
            call_rasa_api,
            load_test_scenarios,
            _ensure_action_synonyms_loaded,
            _ensure_normalization_data_loaded,
            ACTION_SYNONYMS,
            PRODUCTS,
            PRODUCT_SYNONYMS
        )
        from intents.simple_slot_memory import SimpleSlotMemory
        
        print("=== Rasa, Validator, and Slot Memory Test ===")
        print("Testing all modify_cart_200_scenarios.jsonl")
        print("=" * 60)
        
        # Setup
        print("\n🔍 Loading action synonyms...")
        
        # Debug: Test direct loading and manual assignment
        try:
            from intents.core.validator import load_action_synonyms
            synonyms = load_action_synonyms()
            print(f"✅ Direct load_action_synonyms() returned: {synonyms is not None}")
            if synonyms:
                print(f"   Direct synonyms: {list(synonyms.keys())}")
                
                # Manually set the global variable as a workaround
                import intents.core.validator as validator_module
                validator_module.ACTION_SYNONYMS = synonyms
                validator_module.CANONICAL_ACTIONS = set(synonyms.keys())
                print(f"✅ Manually set ACTION_SYNONYMS with {len(synonyms)} actions")
        except Exception as e:
            print(f"❌ Direct loading failed: {e}")
            return False
        
        # Now try the normal loading
        _ensure_action_synonyms_loaded()
        
        # Check the module's global variable directly
        import intents.core.validator as validator_module
        if validator_module.ACTION_SYNONYMS is None:
            print("❌ Failed to load ACTION_SYNONYMS even after manual assignment")
            return False
        else:
            print(f"✅ Loaded ACTION_SYNONYMS with {len(validator_module.ACTION_SYNONYMS)} actions")
            print(f"   Available actions: {list(validator_module.ACTION_SYNONYMS.keys())}")
            
            # Update the imported variable for use in the rest of the function
            global ACTION_SYNONYMS
            ACTION_SYNONYMS = validator_module.ACTION_SYNONYMS
        
        print("\n🔍 Loading normalization data...")
        _ensure_normalization_data_loaded()
        
        # Initialize slot memory
        slot_memory = SimpleSlotMemory()
        sender_id = "test_user"
        print(f"✅ Slot memory initialized: {id(slot_memory)}")
        
        # Load test scenarios
        print("\n🔍 Loading test scenarios...")
        scenarios = load_test_scenarios()
        if not scenarios:
            print("❌ No test scenarios loaded. Exiting.")
            return False
        
        print(f"✅ Loaded {len(scenarios)} test scenarios")
        
        # Test API connection
        print("\n🔍 Testing API connection...")
        try:
            test_resp = call_rasa_api("test connection")
            if test_resp and test_resp.get("success"):
                print("✅ API connection successful")
                api_available = True
            else:
                print("⚠️  API not available (this is expected in test environment)")
                api_available = False
        except (ConnectionError, TimeoutError, OSError) as e:
            print(f"⚠️  API not available: {e}")
            api_available = False
        
        # Initialize counters
        total_cases = 0
        passed_cases = 0
        validator_failed_cases = 0  # Cases where validator disagreed with Rasa
        slot_memory_failed_cases = 0  # Cases where slot memory failed
        api_failed_cases = 0        # Cases where Rasa API call failed
        skipped_cases = 0
        
        validator_failures = []     # Detailed validator failures
        slot_memory_failures = []   # Detailed slot memory failures
        
        print(f"\n🧪 Running all {len(scenarios)} scenarios...")
        print("=" * 60)
        
        for i, scenario in enumerate(scenarios, 1):
            text = scenario.get("text", "")
            expected_actions = scenario.get("expected_actions", [])
            
            if not text:
                print(f"⚠️  Skipping scenario {i}: empty text")
                skipped_cases += 1
                continue
            
            total_cases += 1
            
            # Call Rasa API
            if api_available:
                try:
                    rasa_resp = call_rasa_api(text)
                    if not rasa_resp or not rasa_resp.get("success"):
                        print(f"❌ API call failed for scenario {i}: {text}")
                        api_failed_cases += 1
                        continue
                except (ConnectionError, TimeoutError, OSError) as e:
                    print(f"❌ API call failed for scenario {i}: {e}")
                    api_failed_cases += 1
                    continue
            else:
                # Mock Rasa response for testing without API
                rasa_resp = {
                    "success": True,
                    "result": {
                        "intent": "modify_cart",
                        "entities": [
                            {"entity": "product", "value": "rice"},
                            {"entity": "quantity", "value": "2.0"},
                            {"entity": "unit", "value": "kg"}
                        ],
                        "actions": [
                            {"action": "add", "product": "rice", "quantity": 2.0, "unit": "kg"}
                        ]
                    }
                }
            
            # Extract entities and update slot memory
            entities = rasa_resp.get("result", {}).get("entities", [])
            intent = rasa_resp.get("result", {}).get("intent", "modify_cart")
            rasa_actions = rasa_resp.get("result", {}).get("actions", [])
            
            # Update slot memory
            slots_before = slot_memory.get_slots(sender_id).copy()  # Make a copy to avoid reference issues
            
            # Debug: Check if slot memory is being reset
            if i <= 10 and slots_before.get("conversation_turn", 0) < i - 1:
                print(f"⚠️  WARNING: Slot memory seems to be reset! Case {i}, slots_before: {slots_before}")
            
            slots_after = slot_memory.update_slots(sender_id, intent, entities, text)
            
            # Debug: Verify the update worked
            if i <= 10:
                verification_slots = slot_memory.get_slots(sender_id)
                if verification_slots != slots_after:
                    print(f"⚠️  WARNING: Slot memory update didn't persist! Case {i}")
                    print(f"   slots_after: {slots_after}")
                    print(f"   verification: {verification_slots}")
            
            # Run validator
            validator_result = validate_modify_cart(text, rasa_resp)
            
            # Extract additional data
            # Ensure synonyms are loaded before using them
            _ensure_action_synonyms_loaded()
            
            # Use the module's ACTION_SYNONYMS directly
            import intents.core.validator as validator_module
            if validator_module.ACTION_SYNONYMS is None:
                print(f"❌ ACTION_SYNONYMS is still None after loading attempt")
                print(f"   Skipping verb extraction for case {i}")
                verbs = []
                quantities = extract_quantities(text)
                products = extract_products(text, PRODUCTS, PRODUCT_SYNONYMS)
            else:
                modify_cart_synonyms = {
                    "add": validator_module.ACTION_SYNONYMS["add"], 
                    "remove": validator_module.ACTION_SYNONYMS["remove"], 
                    "set": validator_module.ACTION_SYNONYMS["set"]
                }
                verbs = extract_verbs_with_synonyms(text, modify_cart_synonyms)
                quantities = extract_quantities(text)
                products = extract_products(text, PRODUCTS, PRODUCT_SYNONYMS)
            
            # Debug: Print slot memory info for first few cases
            if i <= 10:
                print(f"\n--- DEBUG Case {i} ---")
                print(f"Text: '{text}'")
                print(f"Intent: {intent}")
                print(f"Entities: {entities}")
                print(f"Slots before: {slots_before}")
                print(f"Slots after: {slots_after}")
                print(f"Products extracted: {products}")
                print(f"Quantities extracted: {quantities}")
                print(f"Sender ID: {sender_id}")
                print(f"Slot memory instance: {id(slot_memory)}")
            
            # Test slot memory functionality (very basic tests only)
            slot_issues = []
            
            # Test 1: Conversation turn should increment (but not necessarily match case number due to API failures)
            expected_turn = slots_before.get("conversation_turn", 0) + 1
            if slots_after.get("conversation_turn", 0) != expected_turn:
                slot_issues.append(f"Conversation turn mismatch: expected {expected_turn}, got {slots_after.get('conversation_turn', 0)}")
            
            # Test 2: Last intent should be set
            if slots_after.get("last_intent") != intent:
                slot_issues.append(f"Last intent mismatch: expected {intent}, got {slots_after.get('last_intent')}")
            
            # Debug: Print slot issues for first few cases
            if i <= 5 and slot_issues:
                print(f"   Slot issues for case {i}: {slot_issues}")
            
            # Determine if this case passed or failed
            validator_failed = validator_result["route"] == "llm"
            slot_memory_failed = len(slot_issues) > 0
            
            if validator_failed:
                validator_failed_cases += 1
                validator_failures.append({
                    "case": i,
                    "text": text,
                    "expected_actions": expected_actions,
                    "rasa_response": rasa_resp,
                    "validator_result": validator_result,
                    "verbs": verbs,
                    "quantities": quantities,
                    "products": products,
                    "failure_type": "VALIDATOR_DISAGREED"
                })
            
            if slot_memory_failed:
                slot_memory_failed_cases += 1
                slot_memory_failures.append({
                    "case": i,
                    "text": text,
                    "intent": intent,
                    "entities": entities,
                    "slots_before": slots_before,
                    "slots_after": slots_after,
                    "slot_issues": slot_issues,
                    "failure_type": "SLOT_MEMORY_FAILED"
                })
            
            if not validator_failed and not slot_memory_failed:
                passed_cases += 1
            
            # Print progress every 50 cases
            if i % 50 == 0:
                print(f"   Processed {i}/{len(scenarios)} scenarios...")
        
        # Print detailed failures
        print("\n" + "=" * 60)
        print("=== VALIDATOR FAILURES ===")
        if not validator_failures:
            print("🎉 No validator failures — validator fully agrees with Rasa!")
        else:
            print(f"Found {len(validator_failures)} VALIDATOR FAILURES out of {total_cases} cases")
            print("These are cases where the validator disagreed with Rasa's response and routed to LLM.")
            print()
            
            for f in validator_failures[:20]:  # Show first 20 failures
                print(f"--- VALIDATOR FAILURE #{f['case']} ---")
                print(f"User text: '{f['text']}'")
                print(f"Expected actions: {f['expected_actions']}")
                print(f"Rasa response: {f['rasa_response']}")
                print(f"Validator route: {f['validator_result']['route']} (DISAGREED)")
                print(f"Failed checks: {f['validator_result'].get('failed_checks', [])}")
                print("Extracted:")
                print(f"  Verbs: {f['verbs']}")
                print(f"  Quantities: {f['quantities']}")
                print(f"  Products: {f['products']}")
                print()
            
            if len(validator_failures) > 20:
                print(f"... and {len(validator_failures) - 20} more validator failures")
        
        # Print detailed slot memory failures
        print("\n" + "=" * 60)
        print("=== SLOT MEMORY FAILURES ===")
        if not slot_memory_failures:
            print("🎉 No slot memory failures — slot memory working perfectly!")
        else:
            print(f"Found {len(slot_memory_failures)} SLOT MEMORY FAILURES out of {total_cases} cases")
            print("These are cases where slot memory functionality failed.")
            print()
            
            for f in slot_memory_failures[:10]:  # Show first 10 failures
                print(f"--- SLOT MEMORY FAILURE #{f['case']} ---")
                print(f"User text: '{f['text']}'")
                print(f"Intent: {f['intent']}")
                print(f"Entities: {f['entities']}")
                print(f"Slots before: {f['slots_before']}")
                print(f"Slots after: {f['slots_after']}")
                print(f"Issues: {f['slot_issues']}")
                print()
            
            if len(slot_memory_failures) > 10:
                print(f"... and {len(slot_memory_failures) - 10} more slot memory failures")
        
        # Print slot memory summary
        print("\n" + "=" * 60)
        print("=== SLOT MEMORY SUMMARY ===")
        final_slots = slot_memory.get_slots(sender_id)
        print(f"Final conversation turn: {final_slots.get('conversation_turn', 0)}")
        print(f"Final last intent: {final_slots.get('last_intent', 'N/A')}")
        print(f"Final last mentioned product: {final_slots.get('last_mentioned_product', 'N/A')}")
        print(f"Final last product added: {final_slots.get('last_product_added', 'N/A')}")
        print(f"Final last quantity: {final_slots.get('last_quantity', 'N/A')}")
        print(f"Final shopping list: {final_slots.get('shopping_list', [])}")
        print(f"Total products in shopping list: {len(final_slots.get('shopping_list', []))}")
        
        # Analyze slot memory failures
        if slot_memory_failures:
            print(f"\n=== SLOT MEMORY FAILURE ANALYSIS ===")
            conversation_turn_failures = sum(1 for f in slot_memory_failures if "Conversation turn mismatch" in str(f.get('slot_issues', [])))
            intent_failures = sum(1 for f in slot_memory_failures if "Last intent mismatch" in str(f.get('slot_issues', [])))
            print(f"Conversation turn failures: {conversation_turn_failures}")
            print(f"Intent storage failures: {intent_failures}")
            
            # Show first few failure details
            print(f"\nFirst 3 slot memory failures:")
            for f in slot_memory_failures[:3]:
                print(f"  Case {f['case']}: {f['slot_issues']}")
        
        # Summary
        print("\n" + "=" * 60)
        print("=== SUMMARY ===")
        print(f"Total cases: {total_cases}")
        print(f"✅ Passed (All systems working): {passed_cases}")
        print(f"❌ VALIDATOR FAILURES (Validator disagreed with Rasa): {validator_failed_cases}")
        print(f"🧠 SLOT MEMORY FAILURES (Slot memory issues): {slot_memory_failed_cases}")
        print(f"⚠️  API FAILURES (Rasa API call failed): {api_failed_cases}")
        print(f"⏭️  Skipped (empty text): {skipped_cases}")
        
        if total_cases > 0:
            success_rate = (passed_cases / total_cases) * 100
            validator_failure_rate = (validator_failed_cases / total_cases) * 100
            slot_memory_failure_rate = (slot_memory_failed_cases / total_cases) * 100
            api_failure_rate = (api_failed_cases / total_cases) * 100
            print(f"\n📊 RATES:")
            print(f"   Success rate: {success_rate:.1f}%")
            print(f"   Validator failure rate: {validator_failure_rate:.1f}%")
            print(f"   Slot memory failure rate: {slot_memory_failure_rate:.1f}%")
            print(f"   API failure rate: {api_failure_rate:.1f}%")
        
        print("\n=== RASA, VALIDATOR, AND SLOT MEMORY TEST COMPLETE ===")
        return True
        
    except ImportError as e:
        print(f"❌ Import error: {e}")
        print("Make sure you're running from the project root directory")
        return False
    except (OSError, ValueError) as e:
        print(f"❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = run_rasa_validator_test()
    sys.exit(0 if success else 1)