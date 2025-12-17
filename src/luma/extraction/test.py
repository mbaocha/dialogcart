#!/usr/bin/env python3
"""
Interactive test script for EntityMatcher.

Allows interactive testing of service and reservation entity extraction.
"""

import sys
import json
from pathlib import Path
from typing import Dict, Any

# Add src directory to path for imports FIRST, before any luma imports
# test.py is in: dialogcart/src/luma/extraction/test.py
# We need to add: dialogcart/src to sys.path
script_dir = Path(__file__).parent.resolve()  # extraction/
luma_dir = script_dir.parent  # luma/
src_dir = luma_dir.parent  # src/

# Add src to path if not already there
src_path = str(src_dir)
if src_path not in sys.path:
    sys.path.insert(0, src_path)

# Import EntityMatcher AFTER path is set up
from luma.extraction.matcher import EntityMatcher


def print_result(result: Dict[str, Any], domain: str):
    """Pretty print extraction result."""
    print("\n" + "=" * 60)
    print(f"Domain: {domain.upper()}")
    print("=" * 60)

    print(f"\n📝 Original Sentence:")
    print(f"   {result.get('osentence', 'N/A')}")

    print(f"\n🔤 Parameterized Sentence:")
    print(f"   {result.get('psentence', 'N/A')}")

    if domain == "service":
        services = result.get("services", [])
        print(f"\n✂️  Services ({len(services)}):")
        for i, svc in enumerate(services, 1):
            print(
                f"   {i}. {svc.get('text', 'N/A')} (pos: {svc.get('position', 'N/A')}, len: {svc.get('length', 'N/A')})")

    elif domain == "reservation":
        room_types = result.get("room_types", [])
        print(f"\n🏨 Room Types ({len(room_types)}):")
        for i, rt in enumerate(room_types, 1):
            print(
                f"   {i}. {rt.get('text', 'N/A')} (pos: {rt.get('position', 'N/A')}, len: {rt.get('length', 'N/A')})")

        amenities = result.get("amenities", [])
        if amenities:
            print(f"\n✨ Amenities ({len(amenities)}):")
            for i, am in enumerate(amenities, 1):
                print(
                    f"   {i}. {am.get('text', 'N/A')} (pos: {am.get('position', 'N/A')}, len: {am.get('length', 'N/A')})")

    dates = result.get("dates", [])
    if dates:
        print(f"\n📅 Dates ({len(dates)}):")
        for i, date in enumerate(dates, 1):
            print(
                f"   {i}. {date.get('text', 'N/A')} (pos: {date.get('position', 'N/A')}, len: {date.get('length', 'N/A')})")

    times = result.get("times", [])
    if times:
        print(f"\n⏰ Times ({len(times)}):")
        for i, time in enumerate(times, 1):
            print(
                f"   {i}. {time.get('text', 'N/A')} (pos: {time.get('position', 'N/A')}, len: {time.get('length', 'N/A')})")

    print("\n" + "=" * 60)
    print("\n📊 Full JSON Output:")
    print(json.dumps(result, indent=2))
    print()


def find_entity_file() -> Path:
    """Find the entity JSON file."""
    # Try to find normalization files relative to this script
    current_dir = Path(__file__).parent
    store_dir = current_dir.parent / "store" / "normalization"

    if not store_dir.exists():
        # Try alternative path structure
        store_dir = script_dir.parent.parent / "store" / "normalization"

    if store_dir.exists():
        # Look for tenant JSON files (e.g., 101.v1.json)
        json_files = list(store_dir.glob("*.v1.json"))
        tenant_files = [f for f in json_files if f.stem.split(".")[
            0].isdigit()]

        if tenant_files:
            return tenant_files[0]

    # Fallback: return None and let EntityMatcher handle it
    return None


def interactive_mode():
    """Run interactive testing mode."""
    print("=" * 60)
    print("EntityMatcher Interactive Test")
    print("=" * 60)
    print("\nSupported domains: 'service', 'reservation'")
    print("Type 'quit' or 'exit' to exit")
    print("Type 'help' for examples")
    print()

    # Find entity file
    entity_file = find_entity_file()
    if entity_file:
        print(f"✓ Found entity file: {entity_file}")
    else:
        print("⚠ Warning: No entity file found. Using default configuration.")
        print("  Place a tenant JSON file in: dialogcart/src/luma/store/normalization/")

    print()

    # Domain selection
    domain = None
    while domain not in ["service", "reservation"]:
        domain_input = input(
            "Select domain (service/reservation) [service]: ").strip().lower()
        if not domain_input:
            domain = "service"
        elif domain_input in ["service", "reservation"]:
            domain = domain_input
        else:
            print("Invalid domain. Please enter 'service' or 'reservation'")

    # Initialize matcher
    try:
        if entity_file:
            entity_matcher = EntityMatcher(
                domain=domain, entity_file=str(entity_file))
        else:
            print("⚠ Cannot initialize without entity file. Exiting.")
            return
    except Exception as e:
        print(f"❌ Error initializing EntityMatcher: {e}")
        return

    print(f"\n✓ EntityMatcher initialized for '{domain}' domain")
    print()

    # Test loop
    while True:
        try:
            user_input = input(
                "Enter text to extract (or 'quit'/'help'): ").strip()

            if not user_input:
                continue

            if user_input.lower() in ["quit", "exit", "q"]:
                print("\n👋 Goodbye!")
                break

            if user_input.lower() == "help":
                print("\n📚 Example inputs:")
                print("   Service domain:")
                print("   - 'book me in for haircut tomorrow by 6pm'")
                print("   - 'i need a trim next week'")
                print("   - 'shaving appointment today at 3pm'")
                print()
                print("   Reservation domain:")
                print("   - 'reserve standard room from 8th dec to 15th dec'")
                print("   - 'book double room tomorrow for 3 nights'")
                print("   - 'i want a suite with ocean view next week'")
                print()
                continue

            # Extract entities
            result = entity_matcher.extract_with_parameterization(
                user_input, debug_units=True)
            print_result(result, domain)

        except KeyboardInterrupt:
            print("\n\n👋 Goodbye!")
            break
        except Exception as e:
            print(f"\n❌ Error: {e}")
            import traceback
            traceback.print_exc()
            print()


def test_examples():
    """Run predefined test examples, including timetoken + am/pm guard cases."""
    print("=" * 60)
    print("EntityMatcher Test Examples")
    print("=" * 60)

    entity_file = find_entity_file()
    if not entity_file:
        print("❌ No entity file found. Cannot run examples.")
        return

    test_cases = [
        # Classic and AM/PM-guarded scenarios: must always produce single timetoken!
        {"domain": "service", "text": "hair cut booking for today 5.30pm", "description": "Service booking with exact 12h time (pm, dot format)"},
        {"domain": "service", "text": "hair cut booking for today 5.30 pm", "description": "Service booking with exact 12h time (pm, spaced)"},
        {"domain": "service", "text": "hair cut booking for today 5pm", "description": "Service booking with exact 12h time (no minutes)"},
        {"domain": "service", "text": "hair cut booking for today 5 pm", "description": "Service booking with exact 12h time (no minutes, spaced)"},
        {"domain": "service", "text": "hair cut booking for today 5:30pm", "description": "Service booking with exact 12h time (colon format)"},
        {"domain": "service", "text": "hair cut booking for today 5:30 pm", "description": "Service booking with exact 12h time (colon, spaced)"},
        # Control: No false "pm" left behind
        {"domain": "service", "text": "hair cut booking for today 10am", "description": "Service with AM"},
        {"domain": "service", "text": "hair cut booking for today 10 am", "description": "Service with AM (spaced)"},
    ]

    for i, test_case in enumerate(test_cases, 1):
        print(f"\n{'=' * 60}")
        print(f"Test {i}: {test_case['description']}")
        print(f"{'=' * 60}")
        print(f"Domain: {test_case['domain']}")
        print(f"Input: {test_case['text']}")

        try:
            entity_matcher = EntityMatcher(
                domain=test_case['domain'],
                entity_file=str(entity_file)
            )
            result = entity_matcher.extract_with_parameterization(
                test_case['text'])
            print_result(result, test_case['domain'])
            # Assert that "psentence" never contains a lone "am" or "pm" token
            ps = result.get("psentence", "")
            assert not any(tok == "pm" or tok == "am" for tok in ps.split()), f"FAIL: Standalone AM/PM found in psentence for: {test_case['text']} => {ps}"
            print("[OK]: No stray am/pm token detected (✓)")
        except AssertionError as aserr:
            print("❌ AssertionError:", aserr)
        except Exception as e:
            print(f"❌ Error: {e}")
            import traceback
            traceback.print_exc()


def main():
    """Main entry point."""
    if len(sys.argv) > 1 and sys.argv[1] == "--examples":
        test_examples()
    else:
        interactive_mode()


if __name__ == "__main__":
    main()
