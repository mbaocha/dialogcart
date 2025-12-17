#!/usr/bin/env python3
"""
Test script for date extraction (relative and absolute dates).

Tests various date formats and edge cases independently.
"""

import sys
import json
from pathlib import Path
from typing import Dict, Any, List

# Add src directory to path for imports FIRST, before any luma imports
# test_date_extraction.py is in: dialogcart/src/luma/extraction/test_date_extraction.py
# We need to add: dialogcart/src to sys.path
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


def print_date_result(result: Dict[str, Any], test_input: str):
    """Pretty print date extraction result."""
    print("\n" + "=" * 70)
    print(f"Input: {test_input}")
    print("=" * 70)

    print(f"\n📝 Original Sentence:")
    print(f"   {result.get('osentence', 'N/A')}")

    print(f"\n🔤 Parameterized Sentence:")
    print(f"   {result.get('psentence', 'N/A')}")

    # Relative dates
    dates = result.get("dates", [])
    if dates:
        print(f"\n📅 Relative Dates ({len(dates)}):")
        for i, date in enumerate(dates, 1):
            text = date.get("text", "N/A")
            start = date.get("start", "N/A")
            end = date.get("end", "N/A")
            print(f"   {i}. '{text}' [span: {start}-{end}]")
    else:
        print(f"\n📅 Relative Dates: (none)")

    # Absolute dates
    dates_absolute = result.get("dates_absolute", [])
    if dates_absolute:
        print(f"\n📆 Absolute Dates ({len(dates_absolute)}):")
        for i, date in enumerate(dates_absolute, 1):
            text = date.get("text", "N/A")
            start = date.get("start", "N/A")
            end = date.get("end", "N/A")
            print(f"   {i}. '{text}' [span: {start}-{end}]")
    else:
        print(f"\n📆 Absolute Dates: (none)")

    print()


def run_test_case(matcher: EntityMatcher, test_input: str, description: str = ""):
    """Run a single test case and print results."""
    if description:
        print(f"\n{'='*70}")
        print(f"TEST: {description}")
        print('='*70)

    try:
        result = matcher.extract_with_parameterization(test_input)
        print_date_result(result, test_input)
        return result
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return None


def main():
    """Run date extraction tests."""
    print("=" * 70)
    print("DATE EXTRACTION TEST SUITE")
    print("=" * 70)

    # Initialize matcher
    print("\n🔧 Initializing EntityMatcher...")
    try:
        # Find normalization directory and provide path to any file in it
        # EntityMatcher will look for global.v1.json in the same directory
        normalization_dir = src_dir / "luma" / "store" / "normalization"
        # Any file in normalization dir works
        entity_file = normalization_dir / "101.v1.json"

        if not entity_file.exists():
            # Fallback: try to find any .json file in normalization directory
            json_files = list(normalization_dir.glob("*.json"))
            if json_files:
                entity_file = json_files[0]
            else:
                raise FileNotFoundError(
                    f"Could not find any JSON file in {normalization_dir}")

        matcher = EntityMatcher(domain="service", entity_file=str(entity_file))
        print("✅ EntityMatcher initialized successfully\n")
    except Exception as e:
        print(f"❌ Failed to initialize EntityMatcher: {e}")
        import traceback
        traceback.print_exc()
        return

    # Test cases
    test_cases = [
        # Relative dates (should still work)
        ("book me for haircut today", "Relative date: today"),
        ("schedule appointment tomorrow", "Relative date: tomorrow"),
        ("book next week", "Relative date: next week"),
        ("tonight please", "Relative date: tonight"),

        # Absolute dates - Day + Month format
        ("book me for haircut on 15th dec", "Absolute date: day+month (15th dec)"),
        ("schedule on 15 dec", "Absolute date: day+month (15 dec)"),
        ("appointment on 15 december", "Absolute date: day+month (15 december)"),
        ("book 15th december 2025", "Absolute date: day+month+year (15th december 2025)"),
        ("on 5 jan", "Absolute date: day+month (5 jan)"),
        ("on 25th feb", "Absolute date: day+month (25th feb)"),

        # Absolute dates - Month + Day format
        ("book dec 15", "Absolute date: month+day (dec 15)"),
        ("schedule dec 15th", "Absolute date: month+day (dec 15th)"),
        ("appointment december 15", "Absolute date: month+day (december 15)"),
        ("book december 15th 2025", "Absolute date: month+day+year (december 15th 2025)"),
        ("on jan 5", "Absolute date: month+day (jan 5)"),
        ("on feb 25th", "Absolute date: month+day (feb 25th)"),

        # Absolute dates - Numeric format
        ("book on 15/12", "Absolute date: numeric (15/12)"),
        ("schedule 15/12/2025", "Absolute date: numeric (15/12/2025)"),
        ("appointment 15-12-2025", "Absolute date: numeric (15-12-2025)"),
        ("book 5/1", "Absolute date: numeric (5/1)"),
        ("on 25-2-2025", "Absolute date: numeric (25-2-2025)"),

        # Mixed scenarios
        ("book haircut tomorrow morning at 9am",
         "Mixed: relative date + time window + time"),
        ("schedule on 15th dec at 2pm", "Mixed: absolute date + time"),
        ("book today or 15th dec", "Mixed: relative OR absolute date"),
        ("appointment tomorrow or dec 15", "Mixed: relative OR absolute date"),

        # Edge cases
        ("book on 15th dec 2025 at 9am for one hour",
         "Edge: absolute date + time + duration"),
        ("schedule haircut and beard trim on 15th dec",
         "Edge: multiple services + absolute date"),
        ("book me in for haircut tommorow mornign at 9am",
         "Edge: typos in relative date + time"),
        ("on 15/12/2025 please", "Edge: absolute date with noise"),

        # Should NOT match (malformed)
        ("book on 32nd dec", "Malformed: invalid day (should not match)"),
        ("schedule on 15/13/2025", "Malformed: invalid month (should not match)"),
        ("appointment on dec 32", "Malformed: invalid day (should not match)"),
    ]

    print("\n" + "=" * 70)
    print("RUNNING TEST CASES")
    print("=" * 70)

    passed = 0
    failed = 0

    for test_input, description in test_cases:
        result = run_test_case(matcher, test_input, description)
        if result is not None:
            passed += 1
        else:
            failed += 1

    # Summary
    print("\n" + "=" * 70)
    print("TEST SUMMARY")
    print("=" * 70)
    print(f"✅ Passed: {passed}")
    print(f"❌ Failed: {failed}")
    print(f"📊 Total:  {passed + failed}")
    print("=" * 70)

    # Interactive mode
    print("\n" + "=" * 70)
    print("INTERACTIVE MODE")
    print("=" * 70)
    print("Enter your own test cases (type 'quit' to exit):\n")

    while True:
        try:
            user_input = input("> ").strip()
            if user_input.lower() in ['quit', 'exit', 'q']:
                break
            if not user_input:
                continue

            result = run_test_case(matcher, user_input, "User input")
        except KeyboardInterrupt:
            print("\n\nExiting...")
            break
        except EOFError:
            break


if __name__ == "__main__":
    main()
