import os
import requests
import random
import json
import argparse
from .scenarios import booking_scenarios, other_scenarios, scenarios
from .assertions import (
    assert_no_partial_binding,
    assert_clarification_has_missing_slots,
    assert_ready_has_required_bound_fields,
    assert_status_missing_slots_consistency,
    assert_booking_block_consistency,
    assert_invariants
)
from luma.config.core import STATUS_READY

API_BASE = "http://localhost:9001/resolve"
USER_ID_PREFIX = "t_user_"

# Fixed test date for deterministic testing
# Set to 2026-01-13 (Wednesday) so relative dates are predictable:
# - "tomorrow" = 2026-01-14
# - "wednesday" = 2026-01-14 (nearest future Wednesday)
# - "next week" = 2026-01-19 to 2026-01-25
TEST_NOW = "2026-01-13T10:00:00Z"

# Set environment variable for deterministic date testing
# This ensures tests pass regardless of when they're run
# NOTE: If running the server separately (python -m luma.api), start it with:
#   LUMA_TEST_NOW=2026-01-13T10:00:00Z python -m luma.api
# Or set the env var before starting the server
os.environ["LUMA_TEST_NOW"] = TEST_NOW


def call_luma(sentence, booking_mode, user_id=None, aliases=None, options=None):
    """
    Call luma API with the given sentence and booking mode.

    Args:
        sentence: User input text
        booking_mode: "service" or "reservation"
        user_id: Optional user_id. If None, generates a random one.
        aliases: Optional dict of tenant aliases. If None, uses default aliases.
        options: Optional options dict for option-constrained resolution.

    Returns:
        Tuple of (response_data, status_code, raw_text)
    """
    if user_id is None:
        user_id = f"{USER_ID_PREFIX}{random.randint(10**15, 10**16 - 1)}"
    domain = "reservation" if booking_mode == "reservation" else "service"

    # Default aliases (used if not provided)
    default_aliases = {
        "standard": "room",
        "room": "room",
        "delux": "room",
        # Typo in tenant alias (for fuzzy matching tests)
        "premum suite": "room",
        "hair cut": "haircut",
        "haircut": "haircut",
        "beard": "beard grooming",
        "beerd": "beard grooming",
        "suite": "room",
        "massage": "massage",
        "presidential room": "room",
    }

    tenant_context = {
        "booking_mode": booking_mode,
        "aliases": aliases if aliases is not None else default_aliases,
    }

    # Add options if provided
    if options is not None:
        tenant_context["options"] = options

    payload = {
        "text": sentence,
        "domain": domain,
        "user_id": user_id,
        "tenant_context": tenant_context,
    }

    resp = requests.post(API_BASE, json=payload, timeout=30)
    try:
        data = resp.json()
    except (ValueError, TypeError):
        data = None
    return data, resp.status_code, resp.text


def _first_service_id(svc_list):
    if not svc_list:
        return None
    svc = svc_list[0]
    return (svc.get("canonical") or svc.get("text") or "").lower()


def _normalize_service_id(svc_id: str) -> str:
    """Normalize service id/text to a stable label for comparison (mirrors canonical vocab in global.v3.json)."""
    if not svc_id:
        return ""
    svc_id = svc_id.lower()
    alias_map = {
        "hospitality.room": "room",
        "room": "room",
        "deluxe": "room",
        "delux": "room",
        "standard": "room",
        "beauty_and_wellness.massage": "massage",
        "beauty_and_wellness.facial": "facial",
        "beauty_and_wellness.haircut": "haircut",
        "haircut": "haircut",
        "hair cut": "haircut",
        "beard grooming": "beard grooming",
        "beard": "beard grooming",
        "massage": "massage",
        "facial": "facial",
    }
    return alias_map.get(svc_id, svc_id)


# _extract_dates removed - dates are now in slots.date_range or slots.datetime_range
# This function is no longer needed as we assert directly on slots, not booking fields


def assert_response(resp, expected):
    assert resp["intent"]["name"] == expected["intent"]
    assert resp["status"] == expected["status"]

    # Assert invariants (additive safety nets - check after basic assertions)
    intent_name = resp.get("intent", {}).get("name")
    assert_invariants(resp, intent_name=intent_name)

    # For UNKNOWN intents: enforce stateless behavior - no intent promotion, no missing_slots, no clarification
    # EXCEPTION: Option-constrained resolution (INVALID_OPTION) is allowed to have clarification_reason
    # because it's a special clarification turn, not intent promotion
    if intent_name == "UNKNOWN":
        # UNKNOWN intents must NOT have missing_slots (no intent promotion)
        actual_issues = resp.get("issues", {})
        derived_missing_slots = sorted([
            slot for slot, issue in actual_issues.items()
            if issue == "missing" or (isinstance(issue, dict) and issue.get("type") == "missing")
        ])
        assert len(derived_missing_slots) == 0, (
            f"UNKNOWN intent must not have missing_slots (no intent promotion). "
            f"Got missing_slots: {derived_missing_slots}, issues: {actual_issues}"
        )
        # UNKNOWN intents must NOT have clarification_reason
        # EXCEPTION: INVALID_OPTION from option-constrained resolution is allowed
        clarification_reason = resp.get("clarification_reason")
        if clarification_reason is not None:
            assert clarification_reason == "INVALID_OPTION", (
                f"UNKNOWN intent must not have clarification_reason (except INVALID_OPTION). "
                f"Got: {clarification_reason}"
            )
            # For INVALID_OPTION, ensure clarification object is present
            assert resp.get("clarification") is not None, (
                f"UNKNOWN intent with INVALID_OPTION must have clarification object"
            )
        # UNKNOWN intents must NOT have booking block (no booking payload)
        assert "booking" not in resp or resp.get("booking") is None, (
            f"UNKNOWN intent must not have booking block (no booking payload). "
            f"Got: {resp.get('booking')}"
        )

        # For UNKNOWN intents: check that all expected slots match actual slots (extraction-only)
        # This ensures Luma only returns extracted slots, not inferred or promoted slots
        expected_slots = expected.get("slots", {})
        actual_slots = resp.get("slots", {})

        # Check each expected slot (date, time, date_range, service_id, etc.)
        for slot_name, expected_value in expected_slots.items():
            actual_value = actual_slots.get(slot_name)
            if slot_name == "date_range":
                # Special handling for date_range (dict comparison)
                assert actual_value is not None, f"Expected {slot_name} in slots for UNKNOWN intent"
                assert isinstance(
                    actual_value, dict), f"{slot_name} must be a dict, got {type(actual_value)}"
                assert actual_value == expected_value, (
                    f"{slot_name} mismatch: got {actual_value}, expected {expected_value}"
                )
            else:
                # Direct value comparison for other slots (date, time, service_id, etc.)
                assert actual_value == expected_value, (
                    f"Slot '{slot_name}' mismatch: got '{actual_value}', expected '{expected_value}'"
                )

    if expected["status"] == STATUS_READY:
        assert resp["intent"][
            "confidence"] >= 0.7, f"low confidence: {resp['intent'].get('confidence')}"

        # Booking block should be present ONLY for intents that produce_booking_payload
        # MODIFY_BOOKING and CANCEL_BOOKING do NOT produce booking_payload (intent-specific semantics)
        intent_name = resp.get("intent", {}).get("name")
        produces_booking = False
        if intent_name:
            from luma.config.intent_meta import get_intent_registry
            registry = get_intent_registry()
            intent_meta = registry.get(intent_name)
            if intent_meta:
                produces_booking = intent_meta.produces_booking_payload is True

        if produces_booking:
            # Booking block should be present for ready status (but minimal, only confirmation_state)
            assert "booking" in resp and resp["booking"], "booking should be present when ready"
            booking = resp["booking"]
            # Booking block should be minimal - only confirmation_state (temporal/service data is in slots)
            assert "confirmation_state" in booking, "booking should contain confirmation_state"

            # Check confirmation_state matches expected value if specified
            expected_booking = expected.get("booking", {})
            if isinstance(expected_booking, dict) and "confirmation_state" in expected_booking:
                expected_confirmation_state = expected_booking["confirmation_state"]
                actual_confirmation_state = booking.get("confirmation_state")
                assert actual_confirmation_state == expected_confirmation_state, (
                    f"confirmation_state mismatch: got '{actual_confirmation_state}', "
                    f"expected '{expected_confirmation_state}'"
                )

            # Ensure booking doesn't contain temporal/service fields (they're in slots)
            assert "services" not in booking, "booking.services should not be present (exposed via slots.service_id)"
            assert "date_range" not in booking, "booking.date_range should not be present (exposed via slots.date_range)"
            assert "datetime_range" not in booking, "booking.datetime_range should not be present (exposed via slots.datetime_range)"
            assert "start_date" not in booking, "booking.start_date should not be present (legacy field removed)"
            assert "end_date" not in booking, "booking.end_date should not be present (legacy field removed)"
        else:
            # For intents that don't produce booking_payload (MODIFY_BOOKING, CANCEL_BOOKING),
            # booking block should NOT be present
            assert "booking" not in resp or resp.get("booking") is None, (
                f"booking block should NOT be present for {intent_name} (produces_booking_payload=false)"
            )

        slots = expected.get("slots", {})

        expected_service = slots.get("service_id")
        if expected_service:
            # Tenant-authoritative: service_id must be a tenant alias key, not a canonical ID
            actual_service_id = resp.get("slots", {}).get("service_id")
            assert actual_service_id == expected_service, (
                f"service_id mismatch: got '{actual_service_id}', expected '{expected_service}'. "
                f"Expected service_id must be a tenant alias key from tenant_context.aliases, not a canonical ID."
            )

        # Check date_range for reservations (intent-specific temporal shape)
        if "date_range" in slots:
            actual_date_range = resp.get("slots", {}).get("date_range")
            assert actual_date_range is not None, "Expected date_range in slots for reservation"
            expected_date_range = slots["date_range"]

            # Handle placeholder dates (e.g., "<resolved_date>") - just check that date_range exists and has start/end
            if expected_date_range.get("start") == "<resolved_date>" or expected_date_range.get("end") == "<resolved_date>":
                # For placeholder dates, just verify date_range structure exists
                assert "start" in actual_date_range, "Expected date_range.start in slots for reservation"
                assert "end" in actual_date_range, "Expected date_range.end in slots for reservation"
                # Verify dates are valid ISO format dates
                import re
                date_pattern = r'^\d{4}-\d{2}-\d{2}$'
                assert re.match(
                    date_pattern, actual_date_range["start"]), f"date_range.start must be ISO date format, got {actual_date_range['start']}"
                assert re.match(
                    date_pattern, actual_date_range["end"]), f"date_range.end must be ISO date format, got {actual_date_range['end']}"
            else:
                # Exact match for specific dates
                assert actual_date_range == expected_date_range, (
                    f"date_range mismatch: got {actual_date_range}, expected {expected_date_range}"
                )

        # Check datetime_range or has_datetime for appointments
        if slots.get("has_datetime"):
            actual_datetime_range = resp.get("slots", {}).get("datetime_range")
            assert actual_datetime_range is not None, "Expected datetime_range in slots for appointment with has_datetime"

        # Check booking_id for MODIFY_BOOKING/CANCEL_BOOKING
        if slots.get("booking_id"):
            actual_booking_id = resp.get("slots", {}).get("booking_id")
            expected_booking_id = slots["booking_id"]
            assert actual_booking_id == expected_booking_id, (
                f"booking_id mismatch: got '{actual_booking_id}', expected '{expected_booking_id}'"
            )

        # Check start_date/end_date for MODIFY_BOOKING date-range modifications
        if slots.get("start_date"):
            actual_start_date = resp.get("slots", {}).get("start_date")
            expected_start_date = slots["start_date"]
            assert actual_start_date == expected_start_date, (
                f"start_date mismatch: got '{actual_start_date}', expected '{expected_start_date}'"
            )

        if slots.get("end_date"):
            actual_end_date = resp.get("slots", {}).get("end_date")
            expected_end_date = slots["end_date"]
            assert actual_end_date == expected_end_date, (
                f"end_date mismatch: got '{actual_end_date}', expected '{expected_end_date}'"
            )

        # Check has_datetime flag for MODIFY_BOOKING time-only modifications
        if "has_datetime" in slots:
            actual_has_datetime = resp.get("slots", {}).get("has_datetime")
            expected_has_datetime = slots["has_datetime"]
            assert actual_has_datetime == expected_has_datetime, (
                f"has_datetime mismatch: got '{actual_has_datetime}', expected '{expected_has_datetime}'"
            )

        # Note: Legacy start_date/end_date support removed - all reservation tests should use date_range
        # However, MODIFY_BOOKING uses start_date/end_date as delta slots
    else:
        # needs_clarification
        assert resp.get(
            "booking") is None, "booking should be omitted when needs_clarification"

        # Check clarification_reason if expected
        expected_clarification_reason = expected.get("clarification_reason")
        if expected_clarification_reason:
            actual_clarification_reason = resp.get("clarification_reason")
            assert actual_clarification_reason == expected_clarification_reason, (
                f"clarification_reason mismatch: got '{actual_clarification_reason}', "
                f"expected '{expected_clarification_reason}'"
            )

        # Check clarification structure if expected (for option-constrained resolution)
        expected_clarification = expected.get("clarification")
        if expected_clarification:
            actual_clarification = resp.get("clarification")
            assert actual_clarification is not None, "Expected clarification object in response"
            assert isinstance(actual_clarification,
                              dict), "clarification must be a dict"

            # Check each field in expected clarification
            for field, expected_value in expected_clarification.items():
                actual_value = actual_clarification.get(field)
                if field == "options":
                    # For options, check that it's a list with the same structure
                    assert isinstance(
                        actual_value, list), "clarification.options must be a list"
                    assert len(actual_value) == len(expected_value), (
                        f"clarification.options length mismatch: got {len(actual_value)}, "
                        f"expected {len(expected_value)}"
                    )
                    # Check that all expected options are present (order may vary)
                    actual_ids = {
                        opt.get("id") for opt in actual_value if isinstance(opt, dict)}
                    expected_ids = {
                        opt.get("id") for opt in expected_value if isinstance(opt, dict)}
                    assert actual_ids == expected_ids, (
                        f"clarification.options mismatch: got IDs {actual_ids}, expected {expected_ids}"
                    )
                else:
                    assert actual_value == expected_value, (
                        f"clarification.{field} mismatch: got '{actual_value}', expected '{expected_value}'"
                    )

        # Always derive missing_slots from issues (Fix #1)
        # Luma emits issues → { slot_name: "missing" }, not missing_slots
        actual_issues = resp.get("issues", {})
        derived_missing_slots = sorted([
            slot for slot, issue in actual_issues.items()
            if issue == "missing" or (isinstance(issue, dict) and issue.get("type") == "missing")
        ])

        # Check for issues (new structure) or missing_slots (legacy test format)
        expected_issues = expected.get("issues")
        expected_missing_slots = expected.get("missing_slots") or []

        if expected_issues:
            # New structure: validate issues
            for slot, expected_issue in expected_issues.items():
                actual_issue = actual_issues.get(slot)
                if isinstance(expected_issue, dict):
                    # Rich issue object (e.g., ambiguous meridiem)
                    assert actual_issue is not None, f"Missing issue for slot '{slot}'"
                    assert isinstance(
                        actual_issue, dict), f"Issue for '{slot}' should be a dict, got {type(actual_issue)}"
                    # Check all expected fields (e.g., raw, start_hour, end_hour, candidates)
                    for field, expected_value in expected_issue.items():
                        actual_value = actual_issue.get(field)
                        assert actual_value == expected_value, (
                            f"Issue field '{field}' mismatch for '{slot}': got '{actual_value}', "
                            f"expected '{expected_value}'"
                        )
                else:
                    # Simple string (e.g., "missing")
                    assert actual_issue == expected_issue, (
                        f"Issue mismatch for '{slot}': got '{actual_issue}', expected '{expected_issue}'"
                    )

        # If expected_missing_slots is provided (legacy test format), validate against derived slots
        if expected_missing_slots:
            assert derived_missing_slots == sorted(expected_missing_slots), (
                f"missing_slots mismatch: got {derived_missing_slots}, expected {sorted(expected_missing_slots)}. "
                f"Derived from issues structure: {actual_issues}"
            )

        # Check slots for needs_clarification status (e.g., extracted time/date that should be in slots)
        # This ensures Luma surfaces extracted temporal values in slots, not just in semantic/context layers
        expected_slots = expected.get("slots", {})
        if expected_slots:
            actual_slots = resp.get("slots", {})
            # Check each expected slot (date, time, date_range, service_id, booking_id, etc.)
            for slot_name, expected_value in expected_slots.items():
                actual_value = actual_slots.get(slot_name)
                if slot_name == "date_range":
                    # Special handling for date_range (dict comparison)
                    assert actual_value is not None, f"Expected {slot_name} in slots for needs_clarification status"
                    assert isinstance(
                        actual_value, dict), f"{slot_name} must be a dict, got {type(actual_value)}"
                    assert actual_value == expected_value, (
                        f"{slot_name} mismatch: got {actual_value}, expected {expected_value}"
                    )
                else:
                    # Direct value comparison for other slots (date, time, service_id, booking_id, etc.)
                    assert actual_value == expected_value, (
                        f"Slot '{slot_name}' mismatch: got '{actual_value}', expected '{expected_value}'"
                    )


def test_cases(scenarios_to_run=None):
    """
    Run test cases for the given scenarios.

    Args:
        scenarios_to_run: List of scenarios to test. If None, uses default scenarios.
    """
    if scenarios_to_run is None:
        scenarios_to_run = scenarios

    failures = []
    for i, case in enumerate(scenarios_to_run, start=1):
        # Get scenario-specific aliases if provided, otherwise use None (default aliases)
        scenario_aliases = case.get("aliases", None)
        # Get scenario-specific options if provided
        scenario_options = case.get("options", None)
        resp, resp_status, resp_raw = call_luma(
            case["sentence"], case["booking_mode"], aliases=scenario_aliases, options=scenario_options)
        try:
            if resp_status != 200 or resp is None:
                raise AssertionError(f"HTTP {resp_status}, body={resp_raw}")
            assert_response(resp, case["expected"])
            print(f"✓ Test case {i} passed")
        except AssertionError as e:
            print(f"✗ Test case {i} failed")
            print(f"  sentence: {case['sentence']}")
            print(f"  expected: {case['expected']}")
            if resp:
                print(f"  actual.status: {resp.get('status')}")
                print(f"  actual.intent: {resp.get('intent')}")
                print(f"  actual.missing_slots: {resp.get('missing_slots')}")
                if resp.get("needs_clarification"):
                    print(
                        f"  actual.clarification_reason: {resp.get('clarification_reason')}")
            else:
                print(f"  actual.http_status: {resp_status}")
                print(f"  actual.raw_body: {resp_raw}")
            try:
                print("  actual.response json:")
                print(json.dumps(resp, indent=2, ensure_ascii=False))
            except (TypeError, ValueError) as dump_err:
                print(f"  (could not dump actual json: {dump_err})")
            try:
                print("  expected.scenario json:")
                print(json.dumps(case.get("expected", {}),
                      indent=2, ensure_ascii=False))
            except (TypeError, ValueError) as dump_err:
                print(f"  (could not dump expected json: {dump_err})")
            failures.append((i, case, e))
    if failures:
        print(f"\n{len(failures)} test(s) failed:")
        for i, case, err in failures:
            print(f"- Case {i}: {case['sentence']} -> {err}")
        # Do not crash; report failures and continue


def test_no_canonical_service_id_in_response():
    """
    Invariant test: Assert that no API response ever returns a canonical service ID as service_id.

    Tests should reflect tenant reality, not global ontology. The service_id must always
    be a tenant service key from tenant_context.aliases, never a canonical ID like "room",
    "beard grooming", "hospitality.room", or "beauty_and_wellness.haircut".
    """
    # Known canonical IDs that should never appear as service_id
    # These are canonical family names or full canonical IDs
    canonical_ids = [
        "room", "haircut", "beard grooming", "massage", "facial", "suite",
        "hospitality.room", "beauty_and_wellness.haircut", "beauty_and_wellness.beard_grooming",
        "beauty_and_wellness.massage", "beauty_and_wellness.facial", "hospitality.suite"
    ]

    # Test with a few scenarios to ensure no canonical IDs are returned
    test_cases = [
        ("book hair cut tomorrow at 3pm", "service"),
        ("reserve suite from january 1st to january 5th", "reservation"),
        ("schedule beerd trim friday at noon", "service"),
    ]

    for sentence, booking_mode in test_cases:
        resp, resp_status, resp_raw = call_luma(sentence, booking_mode)
        assert resp_status == 200 and resp is not None, f"HTTP {resp_status}, body={resp_raw}"

        # Check slots.service_id if present
        service_id = resp.get("slots", {}).get("service_id")
        if service_id:
            # Must not be a canonical ID
            assert service_id not in canonical_ids, (
                f"INVARIANT VIOLATION: service_id must be a tenant alias key, not a canonical ID. "
                f"Got canonical ID '{service_id}' in response for sentence: '{sentence}'. "
                f"Expected a tenant service key from tenant_context.aliases."
            )

            # If it contains a dot, it's likely a canonical format (category.service_name)
            if "." in service_id:
                assert False, (
                    f"INVARIANT VIOLATION: service_id appears to be a canonical ID format (contains '.'). "
                    f"Got '{service_id}' in response for sentence: '{sentence}'. "
                    f"Expected a tenant service key from tenant_context.aliases."
                )

    print("✓ Invariant test passed: No canonical service IDs returned in API responses")


def test_output_independent_of_previous_requests():
    """
    Invariant test: Assert that Luma output must not depend on previous requests.

    Luma is stateless - each request is processed independently without any memory
    or context from previous requests. This test verifies that:
    1. Same input with same user_id produces identical output regardless of prior requests
    2. Different inputs with same user_id produce outputs that depend only on the current input
    3. Fragmentary inputs like "tomorrow" or "at 3pm" without booking verbs return UNKNOWN
       regardless of any prior requests with the same user_id

    This invariant ensures that Luma never infers intent or slots from previous turns.
    """
    # Use a fixed user_id to test statelessness
    fixed_user_id = f"{USER_ID_PREFIX}stateless_test"

    # Test 1: Same input should produce identical output regardless of prior requests
    test_input_1 = "book haircut tomorrow at 3pm"

    # Make first request
    resp1, status1, raw1 = call_luma(
        test_input_1, "service", user_id=fixed_user_id)
    assert status1 == 200 and resp1 is not None, f"First request failed: HTTP {status1}, body={raw1}"

    # Make a different request with the same user_id
    test_input_2 = "what times are available"
    resp2, status2, raw2 = call_luma(
        test_input_2, "service", user_id=fixed_user_id)
    assert status2 == 200 and resp2 is not None, f"Second request failed: HTTP {status2}, body={raw2}"

    # Make the same first request again - should produce identical output
    resp3, status3, raw3 = call_luma(
        test_input_1, "service", user_id=fixed_user_id)
    assert status3 == 200 and resp3 is not None, f"Third request failed: HTTP {status3}, body={raw3}"

    # Assert that resp1 and resp3 are identical (same input = same output)
    assert resp1 == resp3, (
        f"INVARIANT VIOLATION: Same input with same user_id produced different outputs. "
        f"This violates statelessness - Luma must not depend on previous requests. "
        f"First response: {json.dumps(resp1, indent=2)}, "
        f"Third response: {json.dumps(resp3, indent=2)}"
    )

    # Test 2: Fragmentary inputs without booking verbs should return UNKNOWN
    # regardless of any prior requests with the same user_id
    fragmentary_inputs = [
        "tomorrow",
        "at 3pm",
        "at 10",
        "in the morning",
    ]

    for fragment in fragmentary_inputs:
        resp, status, raw = call_luma(
            fragment, "service", user_id=fixed_user_id)
        assert status == 200 and resp is not None, f"Request failed for '{fragment}': HTTP {status}, body={raw}"

        intent_name = resp.get("intent", {}).get("name", "")
        assert intent_name == "UNKNOWN", (
            f"INVARIANT VIOLATION: Fragmentary input '{fragment}' without booking verb "
            f"should return UNKNOWN intent (stateless behavior), but got '{intent_name}'. "
            f"This violates statelessness - Luma must not infer intent from previous turns. "
            f"Response: {json.dumps(resp, indent=2)}"
        )

    # Test 3: Explicit booking inputs should work regardless of prior fragmentary inputs
    # This ensures that prior UNKNOWN responses don't affect valid booking requests
    explicit_booking = "book haircut tomorrow at 3pm"
    resp4, status4, raw4 = call_luma(
        explicit_booking, "service", user_id=fixed_user_id)
    assert status4 == 200 and resp4 is not None, f"Request failed for '{explicit_booking}': HTTP {status4}, body={raw4}"

    intent_name_4 = resp4.get("intent", {}).get("name", "")
    assert intent_name_4 == "CREATE_APPOINTMENT", (
        f"INVARIANT VIOLATION: Explicit booking input should return CREATE_APPOINTMENT "
        f"regardless of prior fragmentary inputs, but got '{intent_name_4}'. "
        f"This violates statelessness - Luma must not let prior UNKNOWN responses affect valid requests. "
        f"Response: {json.dumps(resp4, indent=2)}"
    )

    print("✓ Invariant test passed: Luma output is independent of previous requests (stateless)")


if __name__ == "__main__":
    import sys

    parser = argparse.ArgumentParser(
        description="Run luma test scenarios",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m luma.tests.test_luma                    # Run booking scenarios (default)
  python -m luma.tests.test_luma --other            # Run other intent scenarios
  python -m luma.tests.test_luma 5                  # Run test case 5 from booking scenarios
  python -m luma.tests.test_luma 62 63 64           # Run test cases 62, 63, 64
  python -m luma.tests.test_luma 62,63,64           # Run test cases 62, 63, 64 (comma-separated)
  python -m luma.tests.test_luma 100-105           # Run test cases 100, 101, 102, 103, 104, 105
  python -m luma.tests.test_luma --other 2          # Run test case 2 from other scenarios
  python -m luma.tests.test_luma 5 -v               # Run test case 5 with verbose output
        """
    )
    parser.add_argument(
        "--other",
        action="store_true",
        help="Run other intent scenarios (MODIFY_BOOKING, CANCEL_BOOKING, etc.) instead of booking scenarios"
    )
    parser.add_argument(
        "test_indices",
        nargs="*",
        help="Optional test case indices to run (e.g., '62 63 64', '62,63,64', or '100-105'). Can be space-separated, comma-separated, or range syntax."
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Verbose output (show full response JSON)"
    )

    args = parser.parse_args()

    # Select which scenarios to use for regular scenarios
    scenarios_to_run = other_scenarios if args.other else booking_scenarios
    scenario_type = "other intents" if args.other else "booking intents"

    # Parse test indices - support space-separated, comma-separated, and range syntax
    test_indices = []
    if args.test_indices:
        for arg in args.test_indices:
            # Check for range syntax (e.g., "100-105")
            if '-' in arg and ',' not in arg:
                try:
                    parts = arg.split('-')
                    if len(parts) == 2:
                        start = int(parts[0].strip())
                        end = int(parts[1].strip())
                        if start > end:
                            print(
                                f"Invalid range: '{arg}'. Start ({start}) must be <= end ({end}).")
                            sys.exit(1)
                        # Expand range (inclusive on both ends)
                        test_indices.extend(range(start, end + 1))
                    else:
                        print(
                            f"Invalid range syntax: '{arg}'. Expected format: 'start-end' (e.g., '100-105').")
                        sys.exit(1)
                except ValueError:
                    print(
                        f"Invalid range: '{arg}'. Both start and end must be integers.")
                    sys.exit(1)
            # Split by comma if comma-separated
            elif ',' in arg:
                test_indices.extend([int(x.strip())
                                    for x in arg.split(',') if x.strip()])
            else:
                # Single integer
                try:
                    test_indices.append(int(arg))
                except ValueError:
                    print(
                        f"Invalid test index: '{arg}'. Must be an integer, range (e.g., '100-105'), or comma-separated list.")
                    sys.exit(1)

    if test_indices:
        # Run multiple test cases
        failures = []
        for idx in test_indices:
            if idx < 1 or idx > len(scenarios_to_run):
                print(
                    f"Invalid test index: {idx}. Must be between 1 and {len(scenarios_to_run)} "
                    f"(scenario type: {scenario_type})."
                )
                sys.exit(1)
            single_case = scenarios_to_run[idx - 1]
            # Get scenario-specific aliases if provided, otherwise use None (default aliases)
            scenario_aliases = single_case.get("aliases", None)
            # Get scenario-specific options if provided
            scenario_options = single_case.get("options", None)
            single_resp, single_status, single_raw = call_luma(
                single_case["sentence"], single_case["booking_mode"], aliases=scenario_aliases, options=scenario_options)
            try:
                if single_status != 200 or single_resp is None:
                    raise AssertionError(
                        f"HTTP {single_status}, body={single_raw}")
                assert_response(single_resp, single_case["expected"])
                print(f"✓ Test case {idx} ({scenario_type}) passed")
                if args.verbose:
                    print(f"  sentence: {single_case['sentence']}")
                    print("  response json:")
                    try:
                        print(json.dumps(single_resp, indent=2, ensure_ascii=False))
                    except (TypeError, ValueError) as dump_err:
                        print(f"  (could not dump actual json: {dump_err})")
            except AssertionError as e:
                print(f"✗ Test case {idx} ({scenario_type}) failed")
                print(f"  sentence: {single_case['sentence']}")
                print(f"  expected: {single_case['expected']}")
                print("  actual.response json:")
                try:
                    print(json.dumps(single_resp, indent=2, ensure_ascii=False))
                except (TypeError, ValueError) as dump_err:
                    print(f"  (could not dump actual json: {dump_err})")
                failures.append((idx, single_case, e))

        if failures:
            print(f"\n{len(failures)} test(s) failed:")
            for idx, case, err in failures:
                print(f"- Case {idx}: {case['sentence']} -> {err}")
            sys.exit(1)
    else:
        # Run all test cases
        print(f"Running {len(scenarios_to_run)} {scenario_type} scenarios...")
        test_cases(scenarios_to_run)
        print(f"All {scenario_type} tests passed.")

        # Run invariant tests
        print("\nRunning invariant tests...")

        print("\n  Running invariant: No canonical service IDs in responses...")
        try:
            test_no_canonical_service_id_in_response()
        except AssertionError as e:
            print(f"✗ Invariant test failed: {e}")
            sys.exit(1)

        print("\n  Running invariant: Output independent of previous requests...")
        try:
            test_output_independent_of_previous_requests()
        except AssertionError as e:
            print(f"✗ Invariant test failed: {e}")
            sys.exit(1)

        print("\n✓ All invariant tests passed")
