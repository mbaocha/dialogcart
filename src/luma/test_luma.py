import requests
import random
import json
from .test_luma_list import scenarios

API_BASE = "http://localhost:9001/resolve"
USER_ID_PREFIX = "t_user_"


def call_luma(sentence, booking_mode):
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


def test_cases():
    failures = []
    for i, case in enumerate(scenarios, start=1):
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


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        # Parse arguments: support both "29" and "29 -v" formats
        verbose = False
        test_idx_arg = None

        for arg in sys.argv[1:]:
            if arg == "-v" or arg == "--verbose":
                verbose = True
            else:
                # Try to parse as test case index
                try:
                    test_idx_arg = int(arg)
                except ValueError:
                    pass

        if test_idx_arg is None:
            print("Invalid argument. Provide a numeric test case index.")
            sys.exit(1)

        try:
            idx = test_idx_arg
            if idx < 1 or idx > len(scenarios):
                print(
                    f"Invalid test index: {idx}. Must be between 1 and {len(scenarios)}.")
                sys.exit(1)
            single_case = scenarios[idx - 1]
            single_resp, single_status, single_raw = call_luma(
                single_case["sentence"], single_case["booking_mode"])
            try:
                if single_status != 200 or single_resp is None:
                    raise AssertionError(
                        f"HTTP {single_status}, body={single_raw}")
                assert_response(single_resp, single_case["expected"])
                print(f"✓ Test case {idx} passed")
                if verbose:
                    print(f"  sentence: {single_case['sentence']}")
                    print("  response json:")
                    try:
                        print(json.dumps(single_resp, indent=2, ensure_ascii=False))
                    except (TypeError, ValueError) as dump_err:
                        print(f"  (could not dump actual json: {dump_err})")
            except AssertionError as e:
                print(f"✗ Test case {idx} failed")
                print(f"  sentence: {single_case['sentence']}")
                print(f"  expected: {single_case['expected']}")
                print("  actual.response json:")
                try:
                    print(json.dumps(single_resp, indent=2, ensure_ascii=False))
                except (TypeError, ValueError) as dump_err:
                    print(f"  (could not dump actual json: {dump_err})")
                raise e
        except ValueError:
            print("Invalid argument. Provide a numeric test case index.")
            sys.exit(1)
    else:
        test_cases()
        print("All tests passed.")
