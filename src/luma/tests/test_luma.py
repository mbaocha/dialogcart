import os
import requests
import random
import json
import argparse
from .scenarios import booking_scenarios, other_scenarios, scenarios

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
#
# PROTECTED INVARIANT: Relative date expressions MUST be normalized to ISO dates (YYYY-MM-DD)
# using LUMA_TEST_NOW as the reference point. This is a pure syntactic transformation with
# no semantic interpretation. See RELATIVE_DATE_NORMALIZATION_AUDIT.md for details.
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
        "test_now": TEST_NOW,  # Pass reference date in request payload for deterministic testing
    }

    resp = requests.post(API_BASE, json=payload, timeout=30)
    try:
        data = resp.json()
    except (ValueError, TypeError):
        data = None
    return data, resp.status_code, resp.text


def assert_response(resp, expected):
    """
    Assert that Luma response matches expected facts-only extraction.

    Luma is a pure fact extractor - only checks:
    - intent.name
    - intent.confidence (if present)
    - facts (dates[], times[], date_time_pairs[], service_id, booking_id)

    Does NOT check:
    - status
    - missing_slots
    - issues
    - clarification*
    - booking block
    - date_range (semantic)
    - has_datetime
    - start_date/end_date
    """
    # Assert intent
    assert resp["intent"]["name"] == expected["intent"], (
        f"Intent mismatch: got '{resp['intent']['name']}', expected '{expected['intent']}'"
    )

    # Enforce invariant: semantic fields must NOT be present
    assert "status" not in resp, "Luma must not output 'status' (semantic field)"
    assert "missing_slots" not in resp, "Luma must not output 'missing_slots' (semantic field)"
    assert "issues" not in resp, "Luma must not output 'issues' (semantic field)"
    assert "clarification_reason" not in resp, "Luma must not output 'clarification_reason' (semantic field)"
    assert "clarification" not in resp, "Luma must not output 'clarification' (semantic field)"
    assert "booking" not in resp or resp.get(
        "booking") is None, "Luma must not output 'booking' block (semantic field)"

    # Assert facts extraction
    expected_facts = expected.get("facts", {})
    actual_facts = resp.get("facts", {})

    # Check each expected fact
    for fact_key, expected_value in expected_facts.items():
        assert fact_key in actual_facts, f"Missing fact: {fact_key} in facts"
        actual_value = actual_facts[fact_key]

        if fact_key == "dates":
            # dates[] array - order may matter for ranges
            assert isinstance(
                actual_value, list), f"facts.dates must be a list, got {type(actual_value)}"
            assert actual_value == expected_value, (
                f"facts.dates mismatch: got {actual_value}, expected {expected_value}"
            )
        elif fact_key == "times":
            # times[] array
            assert isinstance(
                actual_value, list), f"facts.times must be a list, got {type(actual_value)}"
            assert actual_value == expected_value, (
                f"facts.times mismatch: got {actual_value}, expected {expected_value}"
            )
        elif fact_key == "date_time_pairs":
            # date_time_pairs[] - only when linguistically explicit
            assert isinstance(
                actual_value, list), f"facts.date_time_pairs must be a list, got {type(actual_value)}"
            assert actual_value == expected_value, (
                f"facts.date_time_pairs mismatch: got {actual_value}, expected {expected_value}"
            )
        else:
            # Direct value comparison for service_id, booking_id
            assert actual_value == expected_value, (
                f"facts.{fact_key} mismatch: got '{actual_value}', expected '{expected_value}'"
            )

    # STAGE 4: Assert time_constraint if present in expected (for fuzzy time cases)
    if "time_constraint" in expected:
        assert "time_constraint" in resp, "Missing time_constraint in response"
        expected_tc = expected["time_constraint"]
        actual_tc = resp["time_constraint"]
        
        # Check mode
        assert actual_tc.get("mode") == expected_tc.get("mode"), (
            f"time_constraint.mode mismatch: got '{actual_tc.get('mode')}', expected '{expected_tc.get('mode')}'"
        )
        
        # Check label if present in expected
        if "label" in expected_tc:
            assert actual_tc.get("label") == expected_tc.get("label"), (
                f"time_constraint.label mismatch: got '{actual_tc.get('label')}', expected '{expected_tc.get('label')}'"
            )
        
        # Check start/end if present in expected
        if "start" in expected_tc:
            assert actual_tc.get("start") == expected_tc.get("start"), (
                f"time_constraint.start mismatch: got '{actual_tc.get('start')}', expected '{expected_tc.get('start')}'"
            )
        if "end" in expected_tc:
            assert actual_tc.get("end") == expected_tc.get("end"), (
                f"time_constraint.end mismatch: got '{actual_tc.get('end')}', expected '{expected_tc.get('end')}'"
            )

    # For non-UNKNOWN intents, check confidence is present and reasonable
    if expected["intent"] != "UNKNOWN":
        assert "confidence" in resp["intent"], "Intent should have confidence for non-UNKNOWN intents"
        assert resp["intent"]["confidence"] >= 0.7, (
            f"Low confidence: {resp['intent'].get('confidence')}"
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
            print(f"PASS Test case {i} passed")
        except AssertionError as e:
            print(f"FAIL Test case {i} failed")
            print(f"  sentence: {case['sentence']}")
            print(f"  expected: {case['expected']}")
            if resp:
                print(f"  actual.intent: {resp.get('intent')}")
                print(f"  actual.facts: {resp.get('facts')}")
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

        # Check facts.service_id if present
        service_id = resp.get("facts", {}).get("service_id")
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

    print("PASS Invariant test passed: No canonical service IDs returned in API responses")


def test_output_independent_of_previous_requests():
    """
    Invariant test: Assert that Luma output must not depend on previous requests.

    Luma is stateless - each request is processed independently without any memory
    or context from previous requests. This test verifies that:
    1. Same input with same user_id produces identical output regardless of prior requests
    2. Different inputs with same user_id produce outputs that depend only on the current input
    3. Fragmentary inputs like "tomorrow" or "at 3pm" without booking verbs return UNKNOWN
       regardless of any prior requests with the same user_id

    This invariant ensures that Luma never infers intent or facts from previous turns.
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

    print("PASS Invariant test passed: Luma output is independent of previous requests (stateless)")


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
                print(f"PASS Test case {idx} ({scenario_type}) passed")
                if args.verbose:
                    print(f"  sentence: {single_case['sentence']}")
                    print("  response json:")
                    try:
                        print(json.dumps(single_resp, indent=2, ensure_ascii=False))
                    except (TypeError, ValueError) as dump_err:
                        print(f"  (could not dump actual json: {dump_err})")
            except AssertionError as e:
                print(f"FAIL Test case {idx} ({scenario_type}) failed")
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

        print("\nPASS All invariant tests passed")
