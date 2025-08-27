#!/usr/bin/env python3
"""
Test runner for the Bulkpot application.
Runs all tests in the organized test structure.
"""

import os
import sys
import subprocess
from pathlib import Path

def run_tests():
    """Run all tests in the organized test structure."""
    
    # Add src to path
    src_path = Path(__file__).parent.parent / "src"
    sys.path.insert(0, str(src_path))
    
    print("Running Bulkpot Test Suite")
    print("=" * 50)
    
    # Define test categories
    test_categories = {
        "Unit Tests": [
            "tests/unit/test_utils/test_float_conversion.py",
            "tests/unit/test_utils/test_utils_migration.py",
            "tests/unit/test_api/test_update_user.py",
            "tests/unit/test_api/test_update_user_fix.py",
            "tests/unit/test_db/test_phone_lookup.py",
            "tests/unit/test_db/test_user.py",
            "tests/unit/test_db/test_address.py",
            "tests/unit/test_db/test_cart.py",
            "tests/unit/test_db/test_order.py",
            "tests/unit/test_db/test_payment.py",
            "tests/unit/test_db/test_product.py",
            "tests/unit/test_agents/test_graph.py",
        ],
        "Integration Tests": [
            "tests/integration/test_graph_update.py",
            "tests/integration/test_state_save.py",
            "tests/integration/test_api_gateway.py",
        ]
    }
    
    total_tests = 0
    passed_tests = 0
    
    for category, test_files in test_categories.items():
        print(f"\n{category}")
        print("-" * 30)
        
        for test_file in test_files:
            test_path = Path(__file__).parent.parent / test_file
            if test_path.exists():
                print(f"\nRunning: {test_file}")
                try:
                    result = subprocess.run(
                        [sys.executable, str(test_path)],
                        capture_output=True,
                        text=True,
                        cwd=Path(__file__).parent.parent
                    )
                    
                    if result.returncode == 0:
                        print("   SUCCESS: PASSED")
                        passed_tests += 1
                    else:
                        print("   FAILED: FAILED")
                        print(f"   Error: {result.stderr}")
                    
                    total_tests += 1
                    
                except Exception as e:
                    print(f"   ERROR: {e}")
                    total_tests += 1
            else:
                print(f"   SKIPPED: {test_file} (not found)")
    
    print(f"\nTest Summary")
    print("=" * 30)
    print(f"Total Tests: {total_tests}")
    print(f"Passed: {passed_tests}")
    print(f"Failed: {total_tests - passed_tests}")
    
    if passed_tests == total_tests:
        print("SUCCESS: All tests passed!")
        return 0
    else:
        print("FAILED: Some tests failed!")
        return 1

if __name__ == "__main__":
    exit_code = run_tests()
    sys.exit(exit_code) 