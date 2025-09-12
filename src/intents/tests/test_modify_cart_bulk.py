# test_modify_cart_bulk.py
import os, json, math, requests
import pytest

BASE_URL = os.getenv("CLASSIFY_BASE_URL", "http://localhost:9000")
ENDPOINT = f"{BASE_URL}/classify"
SCENARIOS_JSONL = os.getenv("SCENARIOS_JSONL", "modify_cart_100_scenarios.jsonl")
FAILURE_LOG_PATH = os.getenv("FAILURE_LOG_PATH", "modify_cart_failures.jsonl")

# Global mutable failure collector
FAILURES = []

def _post(text, sender_id="tester", validate=False):
    payload = {"text": text, "sender_id": sender_id, "validate": validate}
    r = requests.post(ENDPOINT, json=payload, timeout=10)
    r.raise_for_status()
    return r.json()

def _eq_num(a, b, tol=1e-9):
    if a is None or b is None:
        return a is None and b is None
    try:
        return math.isclose(float(a), float(b), rel_tol=0, abs_tol=tol)
    except Exception:
        return False

def _lower_or_none(x):
    return x.lower() if isinstance(x, str) else x

def _assert_actions(actual_actions, expected_actions):
    assert isinstance(actual_actions, list), "actions must be a list"
    assert len(actual_actions) == len(expected_actions), (
        f"action count mismatch: got {len(actual_actions)}, expected {len(expected_actions)}.\n"
        f"Actual: {actual_actions}"
    )
    for i, (act, exp) in enumerate(zip(actual_actions, expected_actions)):
        a_action = _lower_or_none(act.get("action"))
        a_product = _lower_or_none(act.get("product"))
        a_qty = act.get("quantity")

        e_action = _lower_or_none(exp["action"])
        e_product = _lower_or_none(exp["product"])
        e_qty = exp["quantity"]

        assert a_action == e_action, f"step {i}: action mismatch: got {a_action}, expected {e_action}"
        assert a_product == e_product, f"step {i}: product mismatch: got {a_product}, expected {e_product}"
        assert _eq_num(a_qty, e_qty), f"step {i}: quantity mismatch: got {a_qty}, expected {e_qty}"

def _load_scenarios(path):
    scenarios = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line: continue
            scenarios.append(json.loads(line))
    return scenarios

SCENARIOS = _load_scenarios(SCENARIOS_JSONL)


@pytest.mark.parametrize("idx,sc", list(enumerate(SCENARIOS, start=1)))
def test_bulk_modify_cart(idx, sc):
    text = sc["text"]
    expected_actions = sc["expected_actions"]
    try:
        resp = _post(text)
        assert resp.get("success") is True, f"[{idx}] Request failed: {resp}"
        result = resp.get("result", {})
        assert result.get("intent", "").lower() == "modify_cart", f"[{idx}] Wrong intent: {result}"
        
        # Verify confidence_score is present
        assert "confidence_score" in result, f"[{idx}] Missing confidence_score in response"
        assert isinstance(result.get("confidence_score"), (int, float)), f"[{idx}] confidence_score should be numeric"
        
        # Verify validation_mode is present and set to False (no validation)
        assert result.get("validation_mode") is False, f"[{idx}] validation_mode should be False for no validation"
        assert result.get("validation_performed") is False, f"[{idx}] validation_performed should be False for no validation"
        
        actions = result.get("actions", [])

            # Only print for failures (moved to exception handler)

        _assert_actions(actions, expected_actions)

    except AssertionError as e:
        actual_actions = resp.get("result", {}).get("actions", [])
        failure = {
            "idx": idx,
            "text": text,
            "error": str(e),
            "expected": expected_actions,
            "actual": actual_actions,
            "confidence_score": resp.get("result", {}).get("confidence_score"),
            "validation_mode": resp.get("result", {}).get("validation_mode"),
            "validation_performed": resp.get("result", {}).get("validation_performed"),
            "raw_response": resp,
        }
        FAILURES.append(failure)
        
        # Clean failure message
        print(f"\n[{idx}] TEXT: {text}")
        print(f"[{idx}] EXPECTED: {expected_actions}")
        print(f"[{idx}] ACTUAL:   {actual_actions}")
        print(f"[{idx}] ERROR:    {e}")
        print(f"[FAILED {idx}] {text}")
        
        # Re-raise but suppress the exception message
        raise AssertionError() from None

# Final session summary + file logging
@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session, exitstatus):
    if FAILURES:
        print(f"\n==> {len(FAILURES)} test(s) failed. Writing failure details to {FAILURE_LOG_PATH}")
        with open(FAILURE_LOG_PATH, "w", encoding="utf-8") as f:
            for fail in FAILURES:
                f.write(json.dumps(fail) + "\n")
        print(f"✅ Failure log written to: {FAILURE_LOG_PATH}")
    else:
        print("\n✅ All modify_cart tests passed.")
