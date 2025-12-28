#!/usr/bin/env python3
"""
Isolated test for fuzzy alias matching functionality.

Tests the _apply_fuzzy_matching_post_process function directly.
"""

from luma.extraction.matcher import _apply_fuzzy_matching_post_process
import sys
from pathlib import Path

# Add src directory to path for imports
script_dir = Path(__file__).parent.resolve()  # extraction/
luma_dir = script_dir.parent  # luma/
src_dir = luma_dir.parent  # src/

src_path = str(src_dir)
if src_path not in sys.path:
    sys.path.insert(0, src_path)


def test_premium_suite_fuzzy_match():
    """Test that 'premium suite' fuzzy matches to 'premum suite'."""
    print("=" * 60)
    print("Test 1: Premium Suite Fuzzy Match")
    print("=" * 60)

    normalized_text = "book me in for premium suite from october 5 th to 9 th"
    tenant_aliases = {
        "premum suite": "room",  # Typo in tenant alias
        "suite": "room",  # Shorter exact match
        "standard": "room",
        "delux": "room",
    }

    # Find actual position of "suite" in the text
    suite_pos = normalized_text.find("suite")
    print(f"Debug: 'suite' found at position {suite_pos}")
    print(
        f"Debug: Text around position: '{normalized_text[max(0, suite_pos-10):suite_pos+15]}'")

    # Simulate what compiled version would return (exact match for "suite")
    existing_spans = [
        {
            "start_char": suite_pos,  # Actual position of "suite" in normalized text
            "end_char": suite_pos + len("suite"),
            "text": "suite",
            "canonical": "room",
            "alias_key": "suite",
            "match_type": "exact",
        }
    ]

    print(f"Input text: '{normalized_text}'")
    print(f"Tenant aliases: {list(tenant_aliases.keys())}")
    print(f"Existing spans (from exact matching): {existing_spans}")
    print()

    result = _apply_fuzzy_matching_post_process(
        normalized_text, tenant_aliases, existing_spans
    )

    print(f"Result spans: {result}")
    print()

    # Check if fuzzy match was found
    fuzzy_matches = [s for s in result if s.get("match_type") == "fuzzy"]
    exact_matches = [s for s in result if s.get("match_type") == "exact"]

    print(f"Fuzzy matches: {len(fuzzy_matches)}")
    print(f"Exact matches: {len(exact_matches)}")
    print()

    if fuzzy_matches:
        for match in fuzzy_matches:
            print(f"  ✓ Fuzzy match found:")
            print(f"    Text: '{match['text']}'")
            print(f"    Alias key: '{match['alias_key']}'")
            print(f"    Score: {match.get('fuzzy_score', 'N/A')}")
            print(f"    Position: [{match['start_char']}:{match['end_char']}]")

    if exact_matches:
        for match in exact_matches:
            print(f"  Exact match remaining:")
            print(f"    Text: '{match['text']}'")
            print(f"    Alias key: '{match['alias_key']}'")
            print(f"    Position: [{match['start_char']}:{match['end_char']}]")

    # Assertions
    assert len(fuzzy_matches) > 0, "Expected at least one fuzzy match"
    assert any(m["alias_key"] == "premum suite" for m in fuzzy_matches), \
        "Expected fuzzy match to 'premum suite'"
    assert not any(m["alias_key"] == "suite" for m in result), \
        "Expected 'suite' exact match to be removed"

    print()
    print("✓ Test 1 PASSED")
    print()


def test_premum_suite_exact_match():
    """Test that 'premum suite' (typo) still matches exactly."""
    print("=" * 60)
    print("Test 2: Premum Suite Exact Match")
    print("=" * 60)

    normalized_text = "book me in for premum suite from october 5 th to 9 th"
    tenant_aliases = {
        "premum suite": "room",  # Typo in tenant alias
        "suite": "room",
    }

    # Simulate what compiled version would return (exact match for "premum suite")
    existing_spans = [
        {
            "start_char": 20,  # Position of "premum suite" in normalized text
            "end_char": 31,
            "text": "premum suite",
            "canonical": "room",
            "alias_key": "premum suite",
            "match_type": "exact",
        }
    ]

    print(f"Input text: '{normalized_text}'")
    print(f"Tenant aliases: {list(tenant_aliases.keys())}")
    print(f"Existing spans (from exact matching): {existing_spans}")
    print()

    result = _apply_fuzzy_matching_post_process(
        normalized_text, tenant_aliases, existing_spans
    )

    print(f"Result spans: {result}")
    print()

    # Should still have the exact match, no fuzzy match needed
    exact_matches = [s for s in result if s.get("match_type") == "exact"]
    fuzzy_matches = [s for s in result if s.get("match_type") == "fuzzy"]

    print(f"Exact matches: {len(exact_matches)}")
    print(f"Fuzzy matches: {len(fuzzy_matches)}")
    print()

    assert len(exact_matches) > 0, "Expected exact match to remain"
    assert any(m["alias_key"] == "premum suite" for m in exact_matches), \
        "Expected exact match to 'premum suite'"

    print("✓ Test 2 PASSED")
    print()


def test_no_match():
    """Test that unrelated text doesn't match."""
    print("=" * 60)
    print("Test 3: No Match")
    print("=" * 60)

    normalized_text = "book me a haircut tomorrow"
    tenant_aliases = {
        "premum suite": "room",
        "suite": "room",
    }

    existing_spans = []

    print(f"Input text: '{normalized_text}'")
    print(f"Tenant aliases: {list(tenant_aliases.keys())}")
    print()

    result = _apply_fuzzy_matching_post_process(
        normalized_text, tenant_aliases, existing_spans
    )

    print(f"Result spans: {result}")
    print()

    assert len(result) == 0, "Expected no matches for unrelated text"

    print("✓ Test 3 PASSED")
    print()


if __name__ == "__main__":
    try:
        test_premium_suite_fuzzy_match()
        test_premum_suite_exact_match()
        test_no_match()
        print("=" * 60)
        print("ALL TESTS PASSED!")
        print("=" * 60)
    except AssertionError as e:
        print(f"\nTEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    except Exception as e:
        print(f"\n✗ ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
