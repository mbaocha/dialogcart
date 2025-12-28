#!/usr/bin/env python3
"""
Isolated test for fuzzy matching in tenant alias detection.

Tests the _apply_fuzzy_matching_post_process function directly.
"""

from luma.extraction.matcher import _apply_fuzzy_matching_post_process
import sys
from pathlib import Path

# Add src directory to path for imports
script_dir = Path(__file__).parent.resolve()  # extraction/
src_dir = script_dir.parent.parent  # src/
src_path = str(src_dir)
if src_path not in sys.path:
    sys.path.insert(0, src_path)


def test_premium_suite_fuzzy_match():
    """Test that 'premium suite' fuzzy matches to 'premum suite'."""
    print("=" * 60)
    print("Test: 'premium suite' should fuzzy match 'premum suite'")
    print("=" * 60)

    normalized_text = "book me in for premium suite from october 5 th to 9 th"
    tenant_aliases = {
        "premum suite": "room",  # Typo in tenant alias
        "suite": "room",  # Shorter exact match
        "standard": "room",
        "room": "room",
    }

    # Find actual position of "suite" in the text
    import re
    suite_match = re.search(r"\bsuite\b", normalized_text.lower())
    suite_start = suite_match.start() if suite_match else 20
    suite_end = suite_match.end() if suite_match else 25

    # Simulate what compiled version would return (exact match for "suite")
    existing_spans = [
        {
            "start_char": suite_start,
            "end_char": suite_end,
            "text": "suite",
            "canonical": "room",
            "alias_key": "suite",
            "match_type": "exact",
        }
    ]

    print(f"\nInput text: '{normalized_text}'")
    print(f"Tenant aliases: {list(tenant_aliases.keys())}")
    print(f"\nExisting spans (from exact matching):")
    for span in existing_spans:
        print(
            f"  - {span['text']} at [{span['start_char']}:{span['end_char']}] (match_type: {span['match_type']})")

    # Apply fuzzy matching
    result = _apply_fuzzy_matching_post_process(
        normalized_text, tenant_aliases, existing_spans)

    print(f"\nResult spans (after fuzzy matching):")
    for span in result:
        match_type = span.get("match_type", "unknown")
        fuzzy_score = span.get("fuzzy_score", "")
        score_str = f", fuzzy_score: {fuzzy_score}" if fuzzy_score else ""
        print(f"  - '{span['text']}' at [{span['start_char']}:{span['end_char']}] "
              f"(match_type: {match_type}{score_str}, alias_key: {span.get('alias_key', 'N/A')})")

    # Check if "premum suite" was matched
    premum_suite_found = any(
        span.get("alias_key") == "premum suite"
        for span in result
    )

    suite_only_found = any(
        span.get("alias_key") == "suite" and span.get("match_type") == "exact"
        for span in result
    )

    print(f"\n[PASS] 'premum suite' matched: {premum_suite_found}")
    print(f"[FAIL] 'suite' (exact) still present: {suite_only_found}")

    if premum_suite_found and not suite_only_found:
        print(
            "\n[PASS] TEST PASSED: Fuzzy matching correctly preferred 'premum suite' over 'suite'")
        return True
    else:
        print(
            "\n[FAIL] TEST FAILED: Expected 'premum suite' to be matched, but got different result")
        return False


def test_exact_match_still_works():
    """Test that exact matches still work when no fuzzy match is needed."""
    print("\n" + "=" * 60)
    print("Test: Exact match 'premum suite' should still work")
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
            "end_char": 32,
            "text": "premum suite",
            "canonical": "room",
            "alias_key": "premum suite",
            "match_type": "exact",
        }
    ]

    print(f"\nInput text: '{normalized_text}'")
    print(f"\nExisting spans (from exact matching):")
    for span in existing_spans:
        print(
            f"  - {span['text']} at [{span['start_char']}:{span['end_char']}] (match_type: {span['match_type']})")

    # Apply fuzzy matching
    result = _apply_fuzzy_matching_post_process(
        normalized_text, tenant_aliases, existing_spans)

    print(f"\nResult spans (after fuzzy matching):")
    for span in result:
        match_type = span.get("match_type", "unknown")
        print(f"  - '{span['text']}' at [{span['start_char']}:{span['end_char']}] "
              f"(match_type: {match_type}, alias_key: {span.get('alias_key', 'N/A')})")

    # Check if "premum suite" is still there
    premum_suite_found = any(
        span.get("alias_key") == "premum suite"
        for span in result
    )

    if premum_suite_found:
        print("\n[PASS] TEST PASSED: Exact match 'premum suite' still works")
        return True
    else:
        print(
            "\n[FAIL] TEST FAILED: Expected 'premum suite' to remain, but it was removed")
        return False


def test_character_positions():
    """Test that character positions are calculated correctly."""
    print("\n" + "=" * 60)
    print("Test: Character position calculation")
    print("=" * 60)

    normalized_text = "book me in for premium suite from october 5 th to 9 th"
    print(f"\nText: '{normalized_text}'")
    print(f"Length: {len(normalized_text)}")

    # Find "premium suite" position
    import re
    match = re.search(r"premium suite", normalized_text.lower())
    if match:
        start, end = match.span()
        print(f"\n'premium suite' found at [{start}:{end}]")
        print(f"  Text at that position: '{normalized_text[start:end]}'")

    # Find "suite" position
    match = re.search(r"\bsuite\b", normalized_text.lower())
    if match:
        start, end = match.span()
        print(f"\n'suite' found at [{start}:{end}]")
        print(f"  Text at that position: '{normalized_text[start:end]}'")

    # Check if "suite" is contained within "premium suite"
    premium_start = normalized_text.lower().find("premium suite")
    premium_end = premium_start + len("premium suite")
    suite_start = normalized_text.lower().find(" suite")
    suite_end = suite_start + len(" suite")

    print(f"\nOverlap check:")
    print(f"  'premium suite': [{premium_start}:{premium_end}]")
    print(f"  'suite': [{suite_start}:{suite_end}]")
    print(
        f"  'suite' contained in 'premium suite': {suite_start >= premium_start and suite_end <= premium_end}")


def test_single_word_fuzzy_match():
    """Test single-word fuzzy matching (e.g., 'massge' -> 'massage')."""
    print("\n" + "=" * 60)
    print("Test: Single-word fuzzy match 'massge' -> 'massage'")
    print("=" * 60)

    normalized_text = "can u book me a massge for next friday morning"
    tenant_aliases = {
        "massage": "massage",
        "haircut": "haircut",
        "room": "room",
    }

    # No existing spans (no exact match)
    existing_spans = []

    print(f"\nInput text: '{normalized_text}'")
    print(f"Tenant aliases: {list(tenant_aliases.keys())}")

    # Apply fuzzy matching
    result = _apply_fuzzy_matching_post_process(
        normalized_text, tenant_aliases, existing_spans)

    print(f"\nResult spans (after fuzzy matching):")
    for span in result:
        match_type = span.get("match_type", "unknown")
        fuzzy_score = span.get("fuzzy_score", "")
        score_str = f", fuzzy_score: {fuzzy_score:.1f}%" if fuzzy_score else ""
        print(f"  - '{span['text']}' at [{span['start_char']}:{span['end_char']}] "
              f"(match_type: {match_type}{score_str}, alias_key: {span.get('alias_key', 'N/A')})")

    # Check if "massage" was matched
    massage_found = any(
        span.get("alias_key") == "massage" and span.get(
            "match_type") == "fuzzy"
        for span in result
    )

    if massage_found:
        print("\n[PASS] TEST PASSED: Single-word fuzzy match 'massge' -> 'massage'")
        return True
    else:
        print("\n[FAIL] TEST FAILED: Expected 'massage' to be matched")
        return False


def test_single_word_typo_standard():
    """Test single-word typo 'standrd' -> 'standard'."""
    print("\n" + "=" * 60)
    print("Test: Single-word fuzzy match 'standrd' -> 'standard'")
    print("=" * 60)

    normalized_text = "book me a standrd room for december 15th"
    tenant_aliases = {
        "standard": "room",
        "delux": "room",
        "suite": "room",
    }

    existing_spans = []

    print(f"\nInput text: '{normalized_text}'")
    print(f"Tenant aliases: {list(tenant_aliases.keys())}")

    result = _apply_fuzzy_matching_post_process(
        normalized_text, tenant_aliases, existing_spans)

    print(f"\nResult spans (after fuzzy matching):")
    for span in result:
        match_type = span.get("match_type", "unknown")
        fuzzy_score = span.get("fuzzy_score", "")
        score_str = f", fuzzy_score: {fuzzy_score:.1f}%" if fuzzy_score else ""
        print(f"  - '{span['text']}' at [{span['start_char']}:{span['end_char']}] "
              f"(match_type: {match_type}{score_str}, alias_key: {span.get('alias_key', 'N/A')})")

    standard_found = any(
        span.get("alias_key") == "standard" and span.get(
            "match_type") == "fuzzy"
        for span in result
    )

    if standard_found:
        print("\n[PASS] TEST PASSED: Single-word fuzzy match 'standrd' -> 'standard'")
        return True
    else:
        print("\n[FAIL] TEST FAILED: Expected 'standard' to be matched")
        return False


def test_single_word_typo_deluxe():
    """Test single-word typo 'deluxe' -> 'delux'."""
    print("\n" + "=" * 60)
    print("Test: Single-word fuzzy match 'deluxe' -> 'delux'")
    print("=" * 60)

    normalized_text = "i want a deluxe room for november 1st"
    tenant_aliases = {
        "delux": "room",  # Tenant has typo "delux"
        "standard": "room",
        "suite": "room",
    }

    existing_spans = []

    print(f"\nInput text: '{normalized_text}'")
    print(f"Tenant aliases: {list(tenant_aliases.keys())}")

    result = _apply_fuzzy_matching_post_process(
        normalized_text, tenant_aliases, existing_spans)

    print(f"\nResult spans (after fuzzy matching):")
    for span in result:
        match_type = span.get("match_type", "unknown")
        fuzzy_score = span.get("fuzzy_score", "")
        score_str = f", fuzzy_score: {fuzzy_score:.1f}%" if fuzzy_score else ""
        print(f"  - '{span['text']}' at [{span['start_char']}:{span['end_char']}] "
              f"(match_type: {match_type}{score_str}, alias_key: {span.get('alias_key', 'N/A')})")

    delux_found = any(
        span.get("alias_key") == "delux" and span.get("match_type") == "fuzzy"
        for span in result
    )

    if delux_found:
        print("\n[PASS] TEST PASSED: Single-word fuzzy match 'deluxe' -> 'delux'")
        return True
    else:
        print("\n[FAIL] TEST FAILED: Expected 'delux' to be matched")
        return False


def test_two_word_phrase_fuzzy():
    """Test two-word phrase fuzzy matching."""
    print("\n" + "=" * 60)
    print("Test: Two-word fuzzy match 'hair cut' -> 'haircut'")
    print("=" * 60)

    normalized_text = "i need a hair cut tomorrow at 3pm"
    tenant_aliases = {
        "haircut": "haircut",  # Single-word alias
        "hair cut": "haircut",  # Two-word alias
        "massage": "massage",
    }

    existing_spans = []

    print(f"\nInput text: '{normalized_text}'")
    print(f"Tenant aliases: {list(tenant_aliases.keys())}")

    result = _apply_fuzzy_matching_post_process(
        normalized_text, tenant_aliases, existing_spans)

    print(f"\nResult spans (after fuzzy matching):")
    for span in result:
        match_type = span.get("match_type", "unknown")
        fuzzy_score = span.get("fuzzy_score", "")
        score_str = f", fuzzy_score: {fuzzy_score:.1f}%" if fuzzy_score else ""
        print(f"  - '{span['text']}' at [{span['start_char']}:{span['end_char']}] "
              f"(match_type: {match_type}{score_str}, alias_key: {span.get('alias_key', 'N/A')})")

    # Should match "hair cut" (exact or fuzzy)
    hair_cut_found = any(
        span.get("alias_key") in ["hair cut", "haircut"]
        for span in result
    )

    if hair_cut_found:
        print("\n[PASS] TEST PASSED: Two-word phrase 'hair cut' matched")
        return True
    else:
        print("\n[FAIL] TEST FAILED: Expected 'hair cut' or 'haircut' to be matched")
        return False


def test_three_word_phrase_fuzzy():
    """Test three-word phrase fuzzy matching."""
    print("\n" + "=" * 60)
    print("Test: Three-word fuzzy match")
    print("=" * 60)

    normalized_text = "book me a deluxe king suite for next week"
    tenant_aliases = {
        "delux king suite": "room",  # Typo: "delux" instead of "deluxe"
        "king suite": "room",
        "suite": "room",
    }

    existing_spans = []

    print(f"\nInput text: '{normalized_text}'")
    print(f"Tenant aliases: {list(tenant_aliases.keys())}")

    result = _apply_fuzzy_matching_post_process(
        normalized_text, tenant_aliases, existing_spans)

    print(f"\nResult spans (after fuzzy matching):")
    for span in result:
        match_type = span.get("match_type", "unknown")
        fuzzy_score = span.get("fuzzy_score", "")
        score_str = f", fuzzy_score: {fuzzy_score:.1f}%" if fuzzy_score else ""
        print(f"  - '{span['text']}' at [{span['start_char']}:{span['end_char']}] "
              f"(match_type: {match_type}{score_str}, alias_key: {span.get('alias_key', 'N/A')})")

    # Should prefer longer match "delux king suite" over shorter "king suite" or "suite"
    deluxe_king_suite_found = any(
        span.get("alias_key") == "delux king suite"
        for span in result
    )

    if deluxe_king_suite_found:
        print("\n[PASS] TEST PASSED: Three-word phrase 'deluxe king suite' matched")
        return True
    else:
        print("\n[FAIL] TEST FAILED: Expected 'delux king suite' to be matched")
        return False


def test_word_count_priority():
    """Test that longer phrases are matched before shorter ones."""
    print("\n" + "=" * 60)
    print("Test: Word count priority (4-word -> 3-word -> 2-word -> 1-word)")
    print("=" * 60)

    normalized_text = "book me a premium deluxe king suite for next week"
    tenant_aliases = {
        "premium delux king suite": "room",  # 4-word (typo: "delux")
        "delux king suite": "room",  # 3-word
        "king suite": "room",  # 2-word
        "suite": "room",  # 1-word
    }

    existing_spans = []

    print(f"\nInput text: '{normalized_text}'")
    print(f"Tenant aliases: {list(tenant_aliases.keys())}")

    result = _apply_fuzzy_matching_post_process(
        normalized_text, tenant_aliases, existing_spans)

    print(f"\nResult spans (after fuzzy matching):")
    for span in result:
        match_type = span.get("match_type", "unknown")
        fuzzy_score = span.get("fuzzy_score", "")
        score_str = f", fuzzy_score: {fuzzy_score:.1f}%" if fuzzy_score else ""
        print(f"  - '{span['text']}' at [{span['start_char']}:{span['end_char']}] "
              f"(match_type: {match_type}{score_str}, alias_key: {span.get('alias_key', 'N/A')})")

    # Should match the longest phrase first
    longest_match = max(
        result, key=lambda s: s["end_char"] - s["start_char"]) if result else None
    longest_alias = longest_match.get("alias_key") if longest_match else None

    if longest_alias == "premium delux king suite":
        print("\n[PASS] TEST PASSED: Longest phrase matched first")
        return True
    else:
        print(
            f"\n[FAIL] TEST FAILED: Expected 'premium delux king suite', got '{longest_alias}'")
        return False


def test_single_word_only_matches_single_word_aliases():
    """Test that single words only match single-word aliases, not multi-word."""
    print("\n" + "=" * 60)
    print("Test: Single words only match single-word aliases")
    print("=" * 60)

    normalized_text = "book me a massage for tomorrow"
    tenant_aliases = {
        "massage": "massage",  # Single-word alias
        "hair cut": "haircut",  # Multi-word alias (should not match "massage")
        # Multi-word alias (should not match "massage")
        "premium suite": "room",
    }

    existing_spans = []

    print(f"\nInput text: '{normalized_text}'")
    print(f"Tenant aliases: {list(tenant_aliases.keys())}")

    result = _apply_fuzzy_matching_post_process(
        normalized_text, tenant_aliases, existing_spans)

    print(f"\nResult spans (after fuzzy matching):")
    for span in result:
        match_type = span.get("match_type", "unknown")
        fuzzy_score = span.get("fuzzy_score", "")
        score_str = f", fuzzy_score: {fuzzy_score:.1f}%" if fuzzy_score else ""
        print(f"  - '{span['text']}' at [{span['start_char']}:{span['end_char']}] "
              f"(match_type: {match_type}{score_str}, alias_key: {span.get('alias_key', 'N/A')})")

    # Should only match "massage" (single-word), not multi-word aliases
    massage_found = any(
        span.get("alias_key") == "massage"
        for span in result
    )
    multi_word_matched = any(
        " " in span.get("alias_key", "")
        for span in result
    )

    if massage_found and not multi_word_matched:
        print("\n[PASS] TEST PASSED: Single word only matched single-word alias")
        return True
    else:
        print(
            f"\n[FAIL] TEST FAILED: Single word matched multi-word alias: {multi_word_matched}")
        return False


def test_no_false_positives_short_words():
    """Test that very short words (< 4 chars) are not matched."""
    print("\n" + "=" * 60)
    print("Test: Short words (< 4 chars) are not matched")
    print("=" * 60)

    normalized_text = "book me a cat for tomorrow"
    tenant_aliases = {
        "massage": "massage",
        "haircut": "haircut",
        "room": "room",
    }

    existing_spans = []

    print(f"\nInput text: '{normalized_text}'")
    print(f"Tenant aliases: {list(tenant_aliases.keys())}")

    result = _apply_fuzzy_matching_post_process(
        normalized_text, tenant_aliases, existing_spans)

    print(f"\nResult spans (after fuzzy matching):")
    for span in result:
        match_type = span.get("match_type", "unknown")
        print(f"  - '{span['text']}' at [{span['start_char']}:{span['end_char']}] "
              f"(match_type: {match_type}, alias_key: {span.get('alias_key', 'N/A')})")

    # "cat" (3 chars) should not be matched
    cat_matched = any(
        span.get("text") == "cat"
        for span in result
    )

    if not cat_matched:
        print("\n[PASS] TEST PASSED: Short word 'cat' was not matched")
        return True
    else:
        print("\n[FAIL] TEST FAILED: Short word 'cat' was incorrectly matched")
        return False


def test_single_word_typo_haircut():
    """Test single-word typo 'haircut' variations."""
    print("\n" + "=" * 60)
    print("Test: Single-word fuzzy match 'haircut' typos")
    print("=" * 60)

    test_cases = [
        ("haircut", "haircut"),  # Exact match
        ("haircutt", "haircut"),  # Extra letter
        ("haircu", "haircut"),  # Missing letter
        ("hairct", "haircut"),  # Missing 'u'
    ]

    all_passed = True
    for typo, expected in test_cases:
        normalized_text = f"i need a {typo} tomorrow"
        tenant_aliases = {
            "haircut": "haircut",
            "massage": "massage",
        }

        existing_spans = []
        result = _apply_fuzzy_matching_post_process(
            normalized_text, tenant_aliases, existing_spans)

        found = any(
            span.get("alias_key") == expected
            for span in result
        )

        if found:
            print(f"  [PASS] '{typo}' -> '{expected}'")
        else:
            print(f"  [FAIL] '{typo}' did not match '{expected}'")
            all_passed = False

    return all_passed


def test_single_word_typo_massage():
    """Test single-word typo 'massage' variations."""
    print("\n" + "=" * 60)
    print("Test: Single-word fuzzy match 'massage' typos")
    print("=" * 60)

    test_cases = [
        ("massge", "massage"),  # Missing 'a'
        ("masage", "massage"),  # Missing 's'
        ("massag", "massage"),  # Missing 'e'
        ("massage", "massage"),  # Exact match
    ]

    all_passed = True
    for typo, expected in test_cases:
        normalized_text = f"book me a {typo} for friday"
        tenant_aliases = {
            "massage": "massage",
            "haircut": "haircut",
        }

        existing_spans = []
        result = _apply_fuzzy_matching_post_process(
            normalized_text, tenant_aliases, existing_spans)

        found = any(
            span.get("alias_key") == expected
            for span in result
        )

        if found:
            print(f"  [PASS] '{typo}' -> '{expected}'")
        else:
            print(f"  [FAIL] '{typo}' did not match '{expected}'")
            all_passed = False

    return all_passed


def test_two_word_typo_variations():
    """Test two-word phrase typos."""
    print("\n" + "=" * 60)
    print("Test: Two-word phrase fuzzy matching variations")
    print("=" * 60)

    test_cases = [
        ("hair cut", "hair cut", True, {"hair cut": "haircut"}),  # Exact match
        # Typo too different (score ~50%, below 90% threshold)
        ("hair kut", "hair cut", False, {"hair cut": "haircut"}),
        # Extra letter (user typo, should match)
        ("hair cutt", "hair cut", True, {"hair cut": "haircut"}),
        # Missing letter (user typo, should match)
        ("premium suit", "premium suite", True, {"premium suite": "room"}),
        # User says correct, tenant has typo (should fuzzy match)
        ("premium suite", "premum suite", True, {"premum suite": "room"}),
    ]

    all_passed = True
    for user_input, alias_key, should_match, tenant_aliases in test_cases:
        normalized_text = f"book me a {user_input} for tomorrow"

        existing_spans = []
        result = _apply_fuzzy_matching_post_process(
            normalized_text, tenant_aliases, existing_spans)

        found = any(
            span.get("alias_key") == alias_key
            for span in result
        )

        if found == should_match:
            if should_match:
                status = "PASS"
            else:
                status = "PASS (correctly not matched)"
            print(
                f"  [{status}] '{user_input}' -> '{alias_key}' (expected match={should_match})")
        else:
            print(
                f"  [FAIL] '{user_input}' expected match={should_match}, got found={found}")
            # Print actual matches for debugging
            actual_matches = [span.get("alias_key") for span in result]
            print(f"         Actual matches: {actual_matches}")
            all_passed = False

    return all_passed


def test_three_word_typo_variations():
    """Test three-word phrase typos."""
    print("\n" + "=" * 60)
    print("Test: Three-word phrase fuzzy matching variations")
    print("=" * 60)

    normalized_text = "book me a deluxe king suite for next week"
    tenant_aliases = {
        "delux king suite": "room",  # Typo: "delux" instead of "deluxe"
        "king suite": "room",
        "suite": "room",
    }

    existing_spans = []
    result = _apply_fuzzy_matching_post_process(
        normalized_text, tenant_aliases, existing_spans)

    print(f"\nInput text: '{normalized_text}'")
    print(f"Tenant aliases: {list(tenant_aliases.keys())}")

    print(f"\nResult spans (after fuzzy matching):")
    for span in result:
        match_type = span.get("match_type", "unknown")
        fuzzy_score = span.get("fuzzy_score", "")
        score_str = f", fuzzy_score: {fuzzy_score:.1f}%" if fuzzy_score else ""
        print(f"  - '{span['text']}' at [{span['start_char']}:{span['end_char']}] "
              f"(match_type: {match_type}{score_str}, alias_key: {span.get('alias_key', 'N/A')})")

    # Should match "delux king suite" (longest match)
    deluxe_king_suite_found = any(
        span.get("alias_key") == "delux king suite"
        for span in result
    )

    if deluxe_king_suite_found:
        print(
            "\n[PASS] TEST PASSED: Three-word phrase 'deluxe king suite' matched to 'delux king suite'")
        return True
    else:
        print("\n[FAIL] TEST FAILED: Expected 'delux king suite' to be matched")
        return False


def test_four_word_phrase_fuzzy():
    """Test four-word phrase fuzzy matching."""
    print("\n" + "=" * 60)
    print("Test: Four-word phrase fuzzy matching")
    print("=" * 60)

    normalized_text = "book me a premium deluxe king suite for next week"
    tenant_aliases = {
        "premium delux king suite": "room",  # 4-word (typo: "delux")
        "delux king suite": "room",  # 3-word
        "king suite": "room",  # 2-word
        "suite": "room",  # 1-word
    }

    existing_spans = []

    print(f"\nInput text: '{normalized_text}'")
    print(f"Tenant aliases: {list(tenant_aliases.keys())}")

    result = _apply_fuzzy_matching_post_process(
        normalized_text, tenant_aliases, existing_spans)

    print(f"\nResult spans (after fuzzy matching):")
    for span in result:
        match_type = span.get("match_type", "unknown")
        fuzzy_score = span.get("fuzzy_score", "")
        score_str = f", fuzzy_score: {fuzzy_score:.1f}%" if fuzzy_score else ""
        print(f"  - '{span['text']}' at [{span['start_char']}:{span['end_char']}] "
              f"(match_type: {match_type}{score_str}, alias_key: {span.get('alias_key', 'N/A')})")

    # Should match the longest phrase (4-word)
    longest_match = max(
        result, key=lambda s: s["end_char"] - s["start_char"]) if result else None
    longest_alias = longest_match.get("alias_key") if longest_match else None

    if longest_alias == "premium delux king suite":
        print("\n[PASS] TEST PASSED: Four-word phrase matched first")
        return True
    else:
        print(
            f"\n[FAIL] TEST FAILED: Expected 'premium delux king suite', got '{longest_alias}'")
        return False


def test_word_boundary_matching():
    """Test that word boundaries are respected for single words."""
    print("\n" + "=" * 60)
    print("Test: Word boundary matching for single words")
    print("=" * 60)

    normalized_text = "i need a massage appointment"
    tenant_aliases = {
        "massage": "massage",
        "age": "age",  # Should not match "age" from "massage"
    }

    existing_spans = []

    print(f"\nInput text: '{normalized_text}'")
    print(f"Tenant aliases: {list(tenant_aliases.keys())}")

    result = _apply_fuzzy_matching_post_process(
        normalized_text, tenant_aliases, existing_spans)

    print(f"\nResult spans (after fuzzy matching):")
    for span in result:
        match_type = span.get("match_type", "unknown")
        print(f"  - '{span['text']}' at [{span['start_char']}:{span['end_char']}] "
              f"(match_type: {match_type}, alias_key: {span.get('alias_key', 'N/A')})")

    # Should match "massage" but not "age" (substring)
    massage_found = any(
        span.get("alias_key") == "massage"
        for span in result
    )
    age_found = any(
        span.get("alias_key") == "age"
        for span in result
    )

    if massage_found and not age_found:
        print("\n[PASS] TEST PASSED: Word boundaries respected")
        return True
    else:
        print(
            f"\n[FAIL] TEST FAILED: massage={massage_found}, age={age_found} (age should not match)")
        return False


def test_overlapping_matches_priority():
    """Test that longer fuzzy matches override shorter exact matches."""
    print("\n" + "=" * 60)
    print("Test: Overlapping matches - longer fuzzy over shorter exact")
    print("=" * 60)

    normalized_text = "book me in for premium suite from october 5 th to 9 th"
    tenant_aliases = {
        "premum suite": "room",  # Typo in tenant alias
        "suite": "room",  # Shorter exact match
        "premium": "room",  # Single word
    }

    # Simulate exact match for "suite"
    import re
    suite_match = re.search(r"\bsuite\b", normalized_text.lower())
    suite_start = suite_match.start() if suite_match else 20
    suite_end = suite_match.end() if suite_match else 25

    existing_spans = [
        {
            "start_char": suite_start,
            "end_char": suite_end,
            "text": "suite",
            "canonical": "room",
            "alias_key": "suite",
            "match_type": "exact",
        }
    ]

    print(f"\nInput text: '{normalized_text}'")
    print(f"Tenant aliases: {list(tenant_aliases.keys())}")
    print(f"\nExisting spans (exact match for 'suite'):")
    for span in existing_spans:
        print(
            f"  - {span['text']} at [{span['start_char']}:{span['end_char']}]")

    result = _apply_fuzzy_matching_post_process(
        normalized_text, tenant_aliases, existing_spans)

    print(f"\nResult spans (after fuzzy matching):")
    for span in result:
        match_type = span.get("match_type", "unknown")
        fuzzy_score = span.get("fuzzy_score", "")
        score_str = f", fuzzy_score: {fuzzy_score:.1f}%" if fuzzy_score else ""
        print(f"  - '{span['text']}' at [{span['start_char']}:{span['end_char']}] "
              f"(match_type: {match_type}{score_str}, alias_key: {span.get('alias_key', 'N/A')})")

    # Should have "premum suite" (fuzzy) and NOT "suite" (exact, removed)
    premum_suite_found = any(
        span.get("alias_key") == "premum suite"
        for span in result
    )
    suite_still_present = any(
        span.get("alias_key") == "suite" and span.get("match_type") == "exact"
        for span in result
    )

    if premum_suite_found and not suite_still_present:
        print("\n[PASS] TEST PASSED: Longer fuzzy match overrode shorter exact match")
        return True
    else:
        print(
            f"\n[FAIL] TEST FAILED: premum_suite={premum_suite_found}, suite_still_present={suite_still_present}")
        return False


def test_threshold_boundaries():
    """Test that scores near threshold boundaries work correctly."""
    print("\n" + "=" * 60)
    print("Test: Threshold boundary testing")
    print("=" * 60)

    # Test single-word: "massge" should score ~92% (above 85% threshold)
    normalized_text = "book me a massge for tomorrow"
    tenant_aliases = {
        "massage": "massage",
    }

    existing_spans = []
    result = _apply_fuzzy_matching_post_process(
        normalized_text, tenant_aliases, existing_spans)

    print(f"\nInput text: '{normalized_text}'")
    print(f"Tenant aliases: {list(tenant_aliases.keys())}")

    print(f"\nResult spans (after fuzzy matching):")
    for span in result:
        match_type = span.get("match_type", "unknown")
        fuzzy_score = span.get("fuzzy_score", "")
        score_str = f", fuzzy_score: {fuzzy_score:.1f}%" if fuzzy_score else ""
        print(f"  - '{span['text']}' at [{span['start_char']}:{span['end_char']}] "
              f"(match_type: {match_type}{score_str}, alias_key: {span.get('alias_key', 'N/A')})")

    massage_found = any(
        span.get("alias_key") == "massage" and span.get("fuzzy_score", 0) >= 85
        for span in result
    )

    if massage_found:
        print("\n[PASS] TEST PASSED: Score above threshold (85%) matched correctly")
        return True
    else:
        print("\n[FAIL] TEST FAILED: Expected match with score >= 85%")
        return False


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("Fuzzy Matcher Isolation Tests")
    print("=" * 60)

    # Test character positions first
    test_character_positions()

    # Run all tests
    tests = [
        # Basic functionality
        ("Premium suite fuzzy match", test_premium_suite_fuzzy_match),
        ("Exact match still works", test_exact_match_still_works),

        # Single-word tests
        ("Single-word: massge -> massage", test_single_word_fuzzy_match),
        ("Single-word: standrd -> standard", test_single_word_typo_standard),
        ("Single-word: deluxe -> delux", test_single_word_typo_deluxe),
        ("Single-word: haircut variations", test_single_word_typo_haircut),
        ("Single-word: massage variations", test_single_word_typo_massage),

        # Multi-word tests
        ("Two-word phrase: hair cut", test_two_word_phrase_fuzzy),
        ("Two-word phrase: typo variations", test_two_word_typo_variations),
        ("Three-word phrase: deluxe king suite", test_three_word_phrase_fuzzy),
        ("Three-word phrase: typo variations", test_three_word_typo_variations),
        ("Four-word phrase: premium deluxe king suite", test_four_word_phrase_fuzzy),

        # Priority and ordering
        ("Word count priority (4->3->2->1)", test_word_count_priority),
        ("Overlapping matches priority", test_overlapping_matches_priority),

        # Edge cases
        ("Single words only match single-word aliases",
         test_single_word_only_matches_single_word_aliases),
        ("No false positives for short words",
         test_no_false_positives_short_words),
        ("Word boundary matching", test_word_boundary_matching),
        ("Threshold boundaries", test_threshold_boundaries),
    ]

    results = []
    for test_name, test_func in tests:
        try:
            passed = test_func()
            results.append((test_name, passed))
        except Exception as e:
            print(f"\n[ERROR] Test '{test_name}' raised exception: {e}")
            import traceback
            traceback.print_exc()
            results.append((test_name, False))

    print("\n" + "=" * 60)
    print("Summary")
    print("=" * 60)
    for test_name, passed in results:
        status = "PASSED" if passed else "FAILED"
        print(f"{test_name}: {status}")

    all_passed = all(passed for _, passed in results)
    if all_passed:
        print("\n[PASS] All tests passed!")
        sys.exit(0)
    else:
        print("\n[FAIL] Some tests failed!")
        sys.exit(1)
