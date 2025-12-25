import requests
import random
import json
import argparse
from .scenarios import booking_scenarios, other_scenarios, followup_scenarios, scenarios

API_BASE = "http://localhost:9001/resolve"
USER_ID_PREFIX = "t_user_"


def call_luma(sentence, booking_mode, user_id=None):
    """
    Call luma API with the given sentence and booking mode.
    
    Args:
        sentence: User input text
        booking_mode: "service" or "reservation"
        user_id: Optional user_id. If None, generates a random one.
    
    Returns:
        Tuple of (response_data, status_code, raw_text)
    """
    if user_id is None:
        user_id = f"{USER_ID_PREFIX}{random.randint(10**15, 10**16 - 1)}"
    domain = "reservation" if booking_mode == "reservation" else "service"
    payload = {
        "text": sentence,
        "domain": domain,
        "user_id": user_id,
        "tenant_context": {
            "booking_mode": booking_mode,
            "aliases": {
                "standrd": "standard",
                "rom": "room",
                "delux": "deluxe",
                "hair cut": "haircut",
                "beerd": "beard",
            },
        },
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


def _extract_dates(booking):
    start = None
    end = None
    if not booking:
        return start, end
    # Use datetime_range only; start/end are ISO strings
    dtr = booking.get("datetime_range") or {}
    if dtr:
        if not start and dtr.get("start"):
            start = dtr["start"][:10]
        if not end and dtr.get("end"):
            end = dtr["end"][:10]
    return start, end


def assert_response(resp, expected):
    assert resp["intent"]["name"] == expected["intent"]
    assert resp["status"] == expected["status"]

    if expected["status"] == "ready":
        assert resp["intent"][
            "confidence"] >= 0.7, f"low confidence: {resp['intent'].get('confidence')}"
        assert "booking" in resp and resp["booking"], "booking should be present when ready"
        booking = resp["booking"]
        slots = expected.get("slots", {})

        expected_service = slots.get("service_id")
        if expected_service:
            svc_id = _first_service_id(booking.get("services"))
            if svc_id:
                svc_id_norm = _normalize_service_id(svc_id)
                exp_norm = _normalize_service_id(expected_service)
                assert svc_id_norm == exp_norm, f"service_id mismatch: got '{svc_id_norm}', expected '{exp_norm}'"

        start, end = _extract_dates(booking)
        if "start_date" in slots:
            assert start == slots[
                "start_date"], f"start_date mismatch: got '{start}', expected '{slots['start_date']}'"
        if "end_date" in slots:
            assert end == slots[
                "end_date"], f"end_date mismatch: got '{end}', expected '{slots['end_date']}'"
        if slots.get("has_datetime"):
            assert booking.get(
                "datetime_range"), "Expected datetime_range for ready booking"
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

        # Check for issues (new structure) or missing_slots (legacy)
        expected_issues = expected.get("issues")
        expected_missing_slots = expected.get("missing_slots") or []

        if expected_issues:
            # New structure: validate issues
            actual_issues = resp.get("issues", {})
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
        elif expected_missing_slots:
            # Legacy structure: check missing_slots (for backward compatibility)
            # Convert issues to missing_slots format for comparison
            actual_issues = resp.get("issues", {})
            # Extract slots with "missing" issues
            actual_missing = [
                slot for slot, issue in actual_issues.items()
                if issue == "missing" or (isinstance(issue, dict) and issue.get("type") == "missing")
            ]
            assert sorted(actual_missing) == sorted(expected_missing_slots), (
                f"missing_slots mismatch: got {actual_missing}, expected {expected_missing_slots}. "
                f"Note: Response uses 'issues' structure, converted for comparison."
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
        resp, resp_status, resp_raw = call_luma(
            case["sentence"], case["booking_mode"])
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


def test_followup_scenarios(followup_scenarios_to_run=None, verbose=False, start_index=1):
    """
    Run followup scenarios where each scenario is a batch of related turns.
    All turns in a batch share the same user_id to test multi-turn conversations.
    
    Args:
        followup_scenarios_to_run: List of followup scenario batches. If None, uses all followup_scenarios.
        verbose: If True, show full response JSON for each turn.
        start_index: Starting index for scenario numbering (default 1).
    """
    if followup_scenarios_to_run is None:
        followup_scenarios_to_run = followup_scenarios
    
    failures = []
    for relative_idx, scenario_batch in enumerate(followup_scenarios_to_run, start=0):
        scenario_idx = start_index + relative_idx
        scenario_name = scenario_batch.get("name", f"scenario_{scenario_idx}")
        booking_mode = scenario_batch["booking_mode"]
        turns = scenario_batch["turns"]
        
        # Generate a single user_id for this batch
        user_id = f"{USER_ID_PREFIX}{random.randint(10**15, 10**16 - 1)}"
        
        print(f"\n{'='*70}")
        print(f"Followup Scenario {scenario_idx}: {scenario_name}")
        print(f"User ID: {user_id}")
        print(f"{'='*70}")
        
        scenario_passed = True
        for turn_idx, turn in enumerate(turns, start=1):
            sentence = turn["sentence"]
            expected = turn["expected"]
            
            print(f"  Turn {turn_idx}/{len(turns)}: \"{sentence}\"")
            
            resp, resp_status, resp_raw = call_luma(
                sentence, booking_mode, user_id=user_id)
            
            try:
                if resp_status != 200 or resp is None:
                    raise AssertionError(f"HTTP {resp_status}, body={resp_raw}")
                assert_response(resp, expected)
                print(f"    ✓ Turn {turn_idx} passed")
                if verbose:
                    print("    response json:")
                    try:
                        print(json.dumps(resp, indent=2, ensure_ascii=False))
                    except (TypeError, ValueError) as dump_err:
                        print(f"      (could not dump actual json: {dump_err})")
            except AssertionError as e:
                print(f"    ✗ Turn {turn_idx} failed")
                print(f"      sentence: {sentence}")
                print(f"      expected: {expected}")
                if resp:
                    print(f"      actual.status: {resp.get('status')}")
                    print(f"      actual.intent: {resp.get('intent')}")
                    print(f"      actual.missing_slots: {resp.get('missing_slots')}")
                else:
                    print(f"      actual.http_status: {resp_status}")
                    print(f"      actual.raw_body: {resp_raw}")
                try:
                    print("      actual.response json:")
                    print(json.dumps(resp, indent=2, ensure_ascii=False))
                except (TypeError, ValueError) as dump_err:
                    print(f"      (could not dump actual json: {dump_err})")
                failures.append((scenario_idx, scenario_name, turn_idx, sentence, expected, e))
                scenario_passed = False
                # Continue to next turn even if one fails
        
        if scenario_passed:
            print(f"✓ Followup scenario {scenario_idx} ({scenario_name}) passed all {len(turns)} turns")
        else:
            print(f"✗ Followup scenario {scenario_idx} ({scenario_name}) had failures")
    
    if failures:
        print(f"\n{len(failures)} followup turn(s) failed:")
        for scenario_idx, scenario_name, turn_idx, sentence, expected, err in failures:
            print(f"- Scenario {scenario_idx} ({scenario_name}), Turn {turn_idx}: \"{sentence}\" -> {err}")
        # Do not crash; report failures and continue


if __name__ == "__main__":
    import sys

    parser = argparse.ArgumentParser(
        description="Run luma test scenarios",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m luma.tests.test_luma                    # Run booking scenarios (default)
  python -m luma.tests.test_luma --other            # Run other intent scenarios
  python -m luma.tests.test_luma --followup         # Run followup scenarios (multi-turn)
  python -m luma.tests.test_luma --f                # Run followup scenarios (short form)
  python -m luma.tests.test_luma --f 1               # Run followup scenario 1
  python -m luma.tests.test_luma --f 2 -v           # Run followup scenario 2 with verbose output
  python -m luma.tests.test_luma 5                  # Run test case 5 from booking scenarios
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
        "--followup",
        "--f",
        action="store_true",
        dest="followup",
        help="Run followup scenarios (multi-turn conversations with shared user_id)"
    )
    parser.add_argument(
        "test_index",
        nargs="?",
        type=int,
        help="Optional test case index to run a single test (works with --followup/--f to run a specific followup scenario)"
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Verbose output (show full response JSON)"
    )

    args = parser.parse_args()

    # Handle followup scenarios separately (they have a different structure)
    if args.followup:
        if args.test_index is not None:
            # Run a single followup scenario by index
            idx = args.test_index
            if idx < 1 or idx > len(followup_scenarios):
                print(
                    f"Invalid followup scenario index: {idx}. Must be between 1 and {len(followup_scenarios)}."
                )
                sys.exit(1)
            single_scenario = [followup_scenarios[idx - 1]]
            scenario_name = single_scenario[0].get("name", f"scenario_{idx}")
            print(f"Running followup scenario {idx}: {scenario_name}")
            test_followup_scenarios(single_scenario, verbose=args.verbose, start_index=idx)
            print(f"Followup scenario {idx} completed.")
        else:
            # Run all followup scenarios
            print(f"Running {len(followup_scenarios)} followup scenario batch(es)...")
            test_followup_scenarios(followup_scenarios, verbose=args.verbose)
            print(f"All followup scenarios completed.")
        sys.exit(0)

    # Select which scenarios to use for regular scenarios
    scenarios_to_run = other_scenarios if args.other else booking_scenarios
    scenario_type = "other intents" if args.other else "booking intents"

    if args.test_index is not None:
        # Run a single test case
        idx = args.test_index
        if idx < 1 or idx > len(scenarios_to_run):
            print(
                f"Invalid test index: {idx}. Must be between 1 and {len(scenarios_to_run)} "
                f"(scenario type: {scenario_type})."
            )
            sys.exit(1)
        single_case = scenarios_to_run[idx - 1]
        single_resp, single_status, single_raw = call_luma(
            single_case["sentence"], single_case["booking_mode"])
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
            raise e
    else:
        # Run all test cases
        print(f"Running {len(scenarios_to_run)} {scenario_type} scenarios...")
        test_cases(scenarios_to_run)
        print(f"All {scenario_type} tests passed.")

