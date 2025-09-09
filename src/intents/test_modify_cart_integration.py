# tests/test_modify_cart_integration.py
import os
import math
import requests
import pytest

BASE_URL = os.getenv("CLASSIFY_BASE_URL", "http://localhost:9000")
ENDPOINT = f"{BASE_URL}/classify"

def _post(text, sender_id="tester", route="llm"):
    payload = {
        "text": text,
        "sender_id": sender_id,
        "route": route,
        # "action": "predict"  # include if your service expects it
    }
    r = requests.post(ENDPOINT, json=payload, timeout=10)
    r.raise_for_status()
    return r.json()

def _lower_or_none(x):
    return x.lower() if isinstance(x, str) else x

def _eq_num(a, b, tol=1e-9):
    if a is None or b is None:
        return a is None and b is None
    return math.isclose(float(a), float(b), rel_tol=0, abs_tol=tol)

def _assert_actions(actual_actions, expected_actions, context_msg=""):
    assert isinstance(actual_actions, list), f"{context_msg} actions must be a list"
    assert len(actual_actions) == len(expected_actions), (
        f"{context_msg} action count mismatch: got {len(actual_actions)}, expected {len(expected_actions)}.\n"
        f"Actual: {actual_actions}"
    )
    for i, (act, exp) in enumerate(zip(actual_actions, expected_actions)):
        a_action = _lower_or_none(act.get("action"))
        a_product = _lower_or_none(act.get("product"))
        a_qty = act.get("quantity")

        e_action = _lower_or_none(exp["action"])
        e_product = _lower_or_none(exp["product"])
        e_qty = exp["quantity"]

        assert a_action == e_action, f"{context_msg} step {i}: action mismatch: got {a_action}, expected {e_action}"
        assert a_product == e_product, f"{context_msg} step {i}: product mismatch: got {a_product}, expected {e_product}"
        assert _eq_num(a_qty, e_qty), f"{context_msg} step {i}: quantity mismatch: got {a_qty}, expected {e_qty}"

@pytest.mark.parametrize("text,expected_actions", [
    # 1
    ("add 5kg rice and 3kg beans", [
        {"action":"add","product":"rice","quantity":5.0},
        {"action":"add","product":"beans","quantity":3.0},
    ]),
    # 2
    ("remove rice from cart and add 7kg beans", [
        {"action":"remove","product":"rice","quantity":None},
        {"action":"add","product":"beans","quantity":7.0},
    ]),
    # 3
    ("set fish to 2 crates", [
        {"action":"set","product":"fish","quantity":2.0},
    ]),
    # 4
    ("increase rice by 1 kg", [
        {"action":"increase","product":"rice","quantity":1.0},
    ]),
    # 5
    ("decrease garri 500 g", [
        {"action":"decrease","product":"garri","quantity":500.0},
    ]),
    # 6
    ("add 2 bags of milk", [
        {"action":"add","product":"milk","quantity":2.0},
    ]),
    # 7
    ("remove milk, remove bread", [
        {"action":"remove","product":"milk","quantity":None},
        {"action":"remove","product":"bread","quantity":None},
    ]),
    # 8
    ("add 7kg rice, 9kg beans and 3kg milk to cart", [
        {"action":"add","product":"rice","quantity":7.0},
        {"action":"add","product":"beans","quantity":9.0},
        {"action":"add","product":"milk","quantity":3.0},
    ]),
    # 9
    ("update sugar to 1 kg", [
        {"action":"set","product":"sugar","quantity":1.0},
    ]),
    # 10
    ("add three packs noodles", [
        {"action":"add","product":"noodles","quantity":3.0},
    ]),
    # 11
    ("decrease beans by 2 kg and add 1kg plantains", [
        {"action":"decrease","product":"beans","quantity":2.0},
        {"action":"add","product":"plantains","quantity":1.0},
    ]),
    # 12
    ("add 1 crate of fish and remove tomatoes", [
        {"action":"add","product":"fish","quantity":1.0},
        {"action":"remove","product":"tomatoes","quantity":None},
    ]),
    # 13
    ("set butter to 4 packets; decrease butter by 1 packet", [
        {"action":"set","product":"butter","quantity":4.0},
        {"action":"decrease","product":"butter","quantity":1.0},
    ]),
    # 14
    ("add 2 tins of oil and 1 bag of salt", [
        {"action":"add","product":"oil","quantity":2.0},
        {"action":"add","product":"salt","quantity":1.0},
    ]),
    # 15
    ("remove kwulikwili", [
        {"action":"remove","product":"kwulikwili","quantity":None},
    ]),
    # 16
    ("add 500 g garri, increase garri by 500 g", [
        {"action":"add","product":"garri","quantity":500.0},
        {"action":"increase","product":"garri","quantity":500.0},
    ]),
    # 17
    ("add 1 bottle of oil, add 2 bottles of oil", [
        {"action":"add","product":"oil","quantity":1.0},
        {"action":"add","product":"oil","quantity":2.0},
    ]),
    # 18
    ("set flour to 2 kg and set beans to 5 kg", [
        {"action":"set","product":"flour","quantity":2.0},
        {"action":"set","product":"beans","quantity":5.0},
    ]),
    # 19
    ("decrease milk 1 kg, remove milk", [
        {"action":"decrease","product":"milk","quantity":1.0},
        {"action":"remove","product":"milk","quantity":None},
    ]),
    # 20
    ("add a bunch of plantains & add 2 bunches of plantains", [
        {"action":"add","product":"plantains","quantity":1.0},
        {"action":"add","product":"plantains","quantity":2.0},
    ]),
])
def test_modify_cart_integration(text, expected_actions):
    resp = _post(text)
    assert "success" in resp and resp["success"] is True, f"Request failed: {resp}"
    assert "result" in resp, f"No result in response: {resp}"

    result = resp["result"]
    # Intent & sender checks (non-strict on case)
    assert result.get("intent", "").lower() == "modify_cart", f"Wrong intent: {result.get('intent')}"
    assert "actions" in result and isinstance(result["actions"], list), f"No actions list in result: {result}"

    _assert_actions(result["actions"], expected_actions, context_msg=f"text='{text}'")
