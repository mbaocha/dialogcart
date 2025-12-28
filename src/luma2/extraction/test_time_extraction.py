#!/usr/bin/env python3
"""
Test script for time extraction (24-hour and 12-hour formats with spaces).

Tests various time formats including edge cases with spaces around colons.
"""

import sys
import json
from pathlib import Path
from typing import Dict, Any, List

# Add src directory to path for imports FIRST, before any luma imports
script_dir = Path(__file__).parent.resolve()  # extraction/
luma_dir = script_dir.parent  # luma/
src_dir = luma_dir.parent  # src/

# Add src to path if not already there
src_path = str(src_dir)
if src_path not in sys.path:
    sys.path.insert(0, src_path)

# Import EntityMatcher AFTER path is set up
try:
    from luma.extraction.matcher import EntityMatcher
except ImportError as e:
    print(f"❌ Import Error: {e}")
    print("\n💡 Tip: You may need to install dependencies:")
    print("   pip install sentence-transformers")
    print("\n   Or run this script from an environment with all dependencies installed.")
    sys.exit(1)


def print_time_result(result: Dict[str, Any], test_input: str):
    """Pretty print time extraction result."""
    print("\n" + "=" * 70)
    print(f"Input: {test_input}")
    print("=" * 70)

    print(f"\n📝 Original Sentence:")
    print(f"   {result.get('osentence', 'N/A')}")

    print(f"\n🔤 Parameterized Sentence:")
    print(f"   {result.get('psentence', 'N/A')}")

    # Times
    times = result.get("times", [])
    if times:
        print(f"\n⏰ Times ({len(times)}):")
        for i, time in enumerate(times, 1):
            text = time.get("text", "N/A")
            start = time.get("start", "N/A")
            end = time.get("end", "N/A")
            print(f"   {i}. '{text}' [span: {start}-{end}]")
    else:
        print(f"\n⏰ Times: (none)")

    # Check if timetoken was injected
    psentence = result.get("psentence", "")
    has_timetoken = "timetoken" in psentence
    print(f"\n🎯 Has timetoken: {has_timetoken}")

    print()


def run_test_case(matcher: EntityMatcher, test_input: str, description: str = "", should_extract: bool = True):
    """Run a single test case and print results."""
    if description:
        print(f"\n{'='*70}")
        print(f"TEST: {description}")
        print('='*70)

    try:
        result = matcher.extract_with_parameterization(test_input)
        print_time_result(result, test_input)
        
        # Assertion: Check if time was extracted
        times = result.get("times", [])
        psentence = result.get("psentence", "")
        has_timetoken = "timetoken" in psentence
        
        if should_extract:
            assert len(times) > 0, f"Expected time extraction for '{test_input}', but got no times"
            assert has_timetoken, f"Expected timetoken in psentence for '{test_input}', but psentence='{psentence}'"
            print("✅ PASS: Time extracted correctly")
        else:
            assert len(times) == 0, f"Expected no time extraction for '{test_input}', but got times: {times}"
            assert not has_timetoken, f"Expected no timetoken for '{test_input}', but psentence='{psentence}'"
            print("✅ PASS: Correctly did not extract time")
        
        return result
    except AssertionError as e:
        print(f"\n❌ FAIL: {e}")
        return None
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return None


def main():
    """Run time extraction tests."""
    print("=" * 70)
    print("TIME EXTRACTION TEST SUITE")
    print("=" * 70)

    # Initialize matcher
    print("\n🔧 Initializing EntityMatcher...")
    try:
        matcher = EntityMatcher()
        print("✅ EntityMatcher initialized successfully")
    except Exception as e:
        print(f"❌ Failed to initialize EntityMatcher: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    # Test cases: 24-hour format with spaces
    print("\n" + "=" * 70)
    print("24-HOUR FORMAT TESTS (with spaces around colon)")
    print("=" * 70)
    
    run_test_case(matcher, "14 : 00", "24-hour format with spaces: '14 : 00'", should_extract=True)
    run_test_case(matcher, "14:00", "24-hour format without spaces: '14:00'", should_extract=True)
    run_test_case(matcher, "09 : 00", "24-hour format with leading zero and spaces: '09 : 00'", should_extract=True)
    run_test_case(matcher, "18 : 30", "24-hour format with minutes and spaces: '18 : 30'", should_extract=True)
    run_test_case(matcher, "18:30", "24-hour format with minutes: '18:30'", should_extract=True)
    
    # Test cases: 12-hour format with spaces
    print("\n" + "=" * 70)
    print("12-HOUR FORMAT TESTS (with spaces around colon)")
    print("=" * 70)
    
    run_test_case(matcher, "9 : 30 am", "12-hour format with spaces: '9 : 30 am'", should_extract=True)
    run_test_case(matcher, "9:30am", "12-hour format without spaces: '9:30am'", should_extract=True)
    run_test_case(matcher, "12 : 00 pm", "12-hour format noon with spaces: '12 : 00 pm'", should_extract=True)
    
    # Test cases: Negative cases (should not extract)
    print("\n" + "=" * 70)
    print("NEGATIVE TESTS (should NOT extract)")
    print("=" * 70)
    
    run_test_case(matcher, "14 :", "Incomplete time (missing minutes): '14 :'", should_extract=False)
    run_test_case(matcher, ": 00", "Incomplete time (missing hour): ': 00'", should_extract=False)
    run_test_case(matcher, "14", "Bare number without colon: '14'", should_extract=False)
    
    # Test cases: Time constraints (should still work)
    print("\n" + "=" * 70)
    print("TIME CONSTRAINT TESTS (should still work)")
    print("=" * 70)
    
    run_test_case(matcher, "before 4 pm", "Time constraint: 'before 4 pm'", should_extract=True)
    run_test_case(matcher, "by 6 pm", "Time constraint: 'by 6 pm'", should_extract=True)
    run_test_case(matcher, "after 10 am", "Time constraint: 'after 10 am'", should_extract=True)
    
    # Test cases: Contextual usage
    print("\n" + "=" * 70)
    print("CONTEXTUAL USAGE TESTS")
    print("=" * 70)
    
    run_test_case(matcher, "book me for facial at 14 : 00", "Time in booking context: 'book me for facial at 14 : 00'", should_extract=True)
    run_test_case(matcher, "make it 18 : 30", "Time in modification context: 'make it 18 : 30'", should_extract=True)
    
    print("\n" + "=" * 70)
    print("TEST SUITE COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    main()

