#!/usr/bin/env python3
"""
Tests for tenant alias resolution in service extraction.

Tests:
- Ambiguous alias input (multiple matches)
- No alias match input
"""

import sys
from pathlib import Path

# Add src directory to path for imports
script_dir = Path(__file__).parent.resolve()  # extraction/
luma_dir = script_dir.parent  # luma/
src_dir = luma_dir.parent  # src/

src_path = str(src_dir)
if src_path not in sys.path:
    sys.path.insert(0, src_path)

# Imports are done inside test functions after path is set up


def test_ambiguous_alias_resolution():
    """Test that ambiguous alias resolution (multiple matches) results in clarification."""
    from luma.extraction.matcher import _resolve_tenant_alias

    # Test ambiguous case: multiple aliases match the same phrase in input
    # This happens when multiple aliases appear as phrases in the input text
    tenant_aliases = {
        "premium": "beauty_and_wellness.haircut",
        "premium service": "beauty_and_wellness.haircut"
    }

    # Input text where multiple aliases could match as phrases
    # "premium" matches exactly, and "premium service" matches as a phrase
    service_text = "premium service"
    normalized_input = "book premium service tomorrow"

    canonical, status = _resolve_tenant_alias(
        service_text, normalized_input, tenant_aliases)

    # Should return None with "ambiguous" status because both "premium" and "premium service" match
    assert canonical is None, "Ambiguous alias should return None canonical"
    assert status == "ambiguous", f"Expected 'ambiguous' status, got '{status}'"

    print("Test 1 passed: Ambiguous alias resolution returns None with 'ambiguous' status")


def test_no_alias_match():
    """Test that no alias match results in clarification."""
    from luma.extraction.matcher import _resolve_tenant_alias

    tenant_aliases = {
        "premium": "beauty_and_wellness.haircut",
        "standard": "beauty_and_wellness.haircut"
    }

    # Input text that matches no aliases
    service_text = "unknown_service"
    normalized_input = "book unknown_service tomorrow"

    canonical, status = _resolve_tenant_alias(
        service_text, normalized_input, tenant_aliases)

    # Should return None with "no_match" status
    assert canonical is None, "No match should return None canonical"
    assert status == "no_match", f"Expected 'no_match' status, got '{status}'"

    print("Test 2 passed: No alias match returns None with 'no_match' status")


def test_single_alias_match():
    """Test that single alias match succeeds."""
    from luma.extraction.matcher import _resolve_tenant_alias

    tenant_aliases = {
        "premium": "beauty_and_wellness.haircut",
        "standard": "beauty_and_wellness.haircut"
    }

    # Input text that matches exactly one alias
    service_text = "premium"
    normalized_input = "book premium tomorrow"

    canonical, status = _resolve_tenant_alias(
        service_text, normalized_input, tenant_aliases)

    # Should return the canonical with None status
    assert canonical == "beauty_and_wellness.haircut", \
        f"Expected 'beauty_and_wellness.haircut', got '{canonical}'"
    assert status is None, f"Expected None status for success, got '{status}'"

    print("Test 3 passed: Single alias match succeeds")


def test_phrase_match():
    """Test that phrase matching works correctly."""
    from luma.extraction.matcher import _resolve_tenant_alias

    tenant_aliases = {
        "premium haircut": "beauty_and_wellness.haircut",
        "standard": "beauty_and_wellness.haircut"
    }

    # Input text with phrase that should match
    service_text = "premium haircut"
    normalized_input = "book premium haircut tomorrow"

    canonical, status = _resolve_tenant_alias(
        service_text, normalized_input, tenant_aliases)

    # Should return the canonical
    assert canonical == "beauty_and_wellness.haircut", \
        f"Expected 'beauty_and_wellness.haircut', got '{canonical}'"
    assert status is None, f"Expected None status for success, got '{status}'"

    print("Test 4 passed: Phrase matching works correctly")


if __name__ == "__main__":
    print("Running tenant alias resolution tests...\n")

    try:
        test_ambiguous_alias_resolution()
        test_no_alias_match()
        test_single_alias_match()
        test_phrase_match()

        print("\nAll tests passed!")
    except AssertionError as e:
        print(f"\nTest failed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\nUnexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
