#!/usr/bin/env python3
"""
CLI script to run validator tests.

This script provides the same functionality as the old main() function
but as a standalone CLI tool.
"""

import sys
from pathlib import Path

# Add the src directory to the path
sys.path.append(str(Path(__file__).parent.parent.parent))

from intents.core.validator import (
    validate_modify_cart,
    extract_verbs_with_synonyms,
    extract_quantities,
    extract_products,
    call_rasa_api,
    load_test_scenarios,
    setup_validator_logging,
    write_detailed_results_file,
    _ensure_action_synonyms_loaded,
    _ensure_normalization_data_loaded,
    ACTION_SYNONYMS,
    PRODUCTS,
    PRODUCT_SYNONYMS
)


def main():
    """Run validator tests on modify_cart scenarios."""
    _ensure_action_synonyms_loaded()
    _ensure_normalization_data_loaded()
    print("=== Validator Test on modify_cart_200_scenarios.jsonl ===")
    logger, log_file = setup_validator_logging()
    logger.info("=== VALIDATOR TEST SESSION STARTED ===")
    logger.info("Available actions: %s", list(ACTION_SYNONYMS.keys()))
    print("Available actions:", list(ACTION_SYNONYMS.keys()))
    print()

    for action, synonyms in ACTION_SYNONYMS.items():
        print(f"  {action}: {synonyms[:5]}...")

    print("\n🔍 Testing API connection...")
    test = call_rasa_api("test connection")
    if not test or not test.get("success"):
        print("❌ API unavailable. Exiting.")
        logger.error("API unavailable - exiting")
        return
    print("✅ API connection successful!\n")
    logger.info("API connection successful")

    # Load test scenarios
    scenarios = load_test_scenarios()
    if not scenarios:
        print("❌ No test scenarios loaded. Exiting.")
        logger.error("No test scenarios loaded - exiting")
        return

    failures = []
    passed = 0
    failed = 0
    failed_check_counts = {}
    all_test_results = []  # Store all test results for logging

    print(f"🧪 Running validation on {len(scenarios)} scenarios...\n")
    logger.info("Starting validation on %d scenarios", len(scenarios))

    for i, scenario in enumerate(scenarios, 1):
        text = scenario.get("text", "")
        expected_actions = scenario.get("expected_actions", [])
        
        if not text:
            print(f"⚠️  Skipping scenario {i}: empty text")
            logger.warning("Skipping scenario %d: empty text", i)
            all_test_results.append({
                "case": i,
                "text": text,
                "expected_actions": expected_actions,
                "status": "skipped",
                "reason": "empty text",
                "result": None,
                "verbs": [],
                "quantities": [],
                "products": set()
            })
            continue
            
        rasa_resp = call_rasa_api(text)
        if not rasa_resp or not rasa_resp.get("success"):
            print(f"❌ API call failed for scenario {i}: {text}")
            logger.error("API call failed for scenario %d: %s", i, text)
            failed += 1
            all_test_results.append({
                "case": i,
                "text": text,
                "expected_actions": expected_actions,
                "status": "api_failed",
                "reason": "API call failed",
                "result": None,
                "verbs": [],
                "quantities": [],
                "products": set()
            })
            continue
            
        result = validate_modify_cart(text, rasa_resp)
        # Use filtered synonyms for verb extraction to avoid extracting 'clear'
        modify_cart_synonyms = {"add": ACTION_SYNONYMS["add"], "remove": ACTION_SYNONYMS["remove"], "set": ACTION_SYNONYMS["set"]}
        verbs = extract_verbs_with_synonyms(text, modify_cart_synonyms)
        quantities = extract_quantities(text)
        products = extract_products(text, PRODUCTS, PRODUCT_SYNONYMS)
        
        test_result = {
            "case": i,
            "text": text,
            "expected_actions": expected_actions,
            "result": result,
            "verbs": verbs,
            "quantities": quantities,
            "products": products
        }
        
        if result["route"] == "llm":
            failed += 1
            test_result["status"] = "failed"
            test_result["reason"] = f"Failed checks: {result.get('failed_checks', [])}"
            
            # Track failed check counts
            for check in result.get("failed_checks", []):
                failed_check_counts[check] = failed_check_counts.get(check, 0) + 1
            
            failures.append(test_result)
        else:
            passed += 1
            test_result["status"] = "passed"
            test_result["reason"] = "All validation checks passed"
        
        all_test_results.append(test_result)

    # Print failures
    print("\n=== FAILURES ===")
    if not failures:
        print("🎉 No failures — validator fully agrees with Rasa!")
    else:
        for f in failures:
            print(f"\n--- Test Case {f['case']} ---")
            print(f"User text: '{f['text']}'")
            print(f"Expected actions: {f['expected_actions']}")
            print("⚠️  Route decision: llm")
            print(f"Rasa actions: {f['result']['rasa_actions']}")
            print("Extracted:")
            print(f"  Verbs: {f['verbs']}")
            print(f"  Quantities: {f['quantities']}")
            print(f"  Products: {f['products']}")

    # Summary
    total = passed + failed
    print("\n=== SUMMARY ===")
    print(f"Total cases: {total}")
    print(f"Passed: {passed}")
    print(f"Failed: {failed}")
    if total > 0:
        print(f"Success rate: {passed/total*100:.1f}%")
    
    # Log comprehensive summary
    logger.info("=== VALIDATION SUMMARY ===")
    logger.info("Total cases: %d", total)
    logger.info("Passed: %d", passed)
    logger.info("Failed: %d", failed)
    if total > 0:
        logger.info("Success rate: %.1f%%", passed/total*100)
    
    # Log failed check breakdown
    if failed_check_counts:
        logger.info("=== FAILED CHECK BREAKDOWN ===")
        for check, count in sorted(failed_check_counts.items(), key=lambda x: x[1], reverse=True):
            logger.info("%s: %d failures", check, count)
    
    # Log detailed failure analysis
    if failures:
        logger.info("=== DETAILED FAILURE ANALYSIS ===")
        for f in failures:
            logger.warning("FAILURE Case %d: '%s' | Failed checks: %s | Rasa actions: %s", 
                          f['case'], f['text'], f['result'].get('failed_checks', []), f['result']['rasa_actions'])
    
    # Log comprehensive test results to file
    logger.info("=== COMPREHENSIVE TEST RESULTS ===")
    for test in all_test_results:
        if test['status'] == 'passed':
            logger.info("PASSED Case %d: '%s' | Rasa actions: %s | Extracted: verbs=%s, quantities=%s, products=%s", 
                       test['case'], test['text'], 
                       test['result']['rasa_actions'] if test['result'] else 'N/A',
                       test['verbs'], test['quantities'], test['products'])
        elif test['status'] == 'failed':
            logger.warning("FAILED Case %d: '%s' | %s | Rasa actions: %s | Extracted: verbs=%s, quantities=%s, products=%s", 
                          test['case'], test['text'], test['reason'],
                          test['result']['rasa_actions'] if test['result'] else 'N/A',
                          test['verbs'], test['quantities'], test['products'])
        elif test['status'] == 'skipped':
            logger.warning("SKIPPED Case %d: '%s' | %s", test['case'], test['text'], test['reason'])
        elif test['status'] == 'api_failed':
            logger.error("API_FAILED Case %d: '%s' | %s", test['case'], test['text'], test['reason'])
    
    # Write detailed results to a separate JSON file
    write_detailed_results_file(all_test_results, log_file)
    
    logger.info("=== VALIDATION SESSION COMPLETED - Log file: %s ===", log_file)


if __name__ == "__main__":
    main()
