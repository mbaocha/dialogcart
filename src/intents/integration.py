import pytest
import requests

BASE_URL = "http://localhost:9000/classify"


def post_classify(text: str, sender_id: str = "test-user") -> dict:
    response = requests.post(
        BASE_URL,
        json={"text": text, "sender_id": sender_id},
        timeout=10,
    )
    assert response.status_code == 200, f"Unexpected status: {response.status_code}"
    return response.json()


def loosely_equal(expected: dict, actual: dict) -> bool:
    for k, v in expected.items():
        if k not in actual:
            return False
        actual_val = actual[k]
        # Compare numerically if both are numbers
        if isinstance(v, (int, float)) and isinstance(actual_val, (int, float)):
            if abs(actual_val - v) > 1e-3:
                return False
        else:
            if str(actual_val).lower() != str(v).lower():
                return False
    return True


test_cases = [
    {
        "text": "add 5kg yam to cart",
        "sender_id": "followup-1",
        "expected_intents": ["ADD_TO_CART"],
        "expected_entities": [
            {"product": "yam", "quantity": 5, "unit": "kg"}
        ]
    },
    {
        "text": "remove rice from my cart",
        "expected_intents": ["REMOVE_FROM_CART"],
        "expected_entities": [
            {"product": "rice"}
        ]
    },
    {
        "text": "update the beans quantity to 4kg",
        "expected_intents": ["UPDATE_CART_QUANTITY"],
        "expected_entities": [
            {"product": "beans", "quantity": 4, "unit": "kg"}
        ]
    },
    {
        "text": "do you have yam in stock?",
        "expected_intents": ["CHECK_PRODUCT_EXISTENCE"],
        "expected_entities": [
            {"product": "yam"}
        ]
    },
    {
        "text": "remove yam from cart and add 7kg beans",
        "expected_intents": ["REMOVE_FROM_CART", "ADD_TO_CART"],
        "expected_entities": [
            {"product": "yam"},
            {"product": "beans", "quantity": 7, "unit": "kg"}
        ]
    },
    {
        # Follow-up using memory from prior turn (yam should be remembered)
        "text": "make it 10kg",
        "sender_id": "followup-1",
        "expected_intents": ["UPDATE_CART_QUANTITY"],
        "expected_entities": [
            {"product": "yam", "quantity": 10, "unit": "kg"}
        ]
    }
]


@pytest.mark.parametrize("case", test_cases)
def test_classify_intents_and_entities(case):
    sender_id = case.get("sender_id", "test-user")
    result = post_classify(case["text"], sender_id=sender_id)

    assert result["success"] is True
    assert "result" in result
    actual_intents = [i["intent"] for i in result["result"].get("intents", [])]
    all_entities = []
    for intent in result["result"].get("intents", []):
        all_entities.extend(intent.get("entities", []))

    # Intent validation
    for expected_intent in case["expected_intents"]:
        assert expected_intent in actual_intents, f"Missing intent: {expected_intent}"

    # Relaxed entity validation using loosely_equal
    for expected_entity in case["expected_entities"]:
        matched = any(loosely_equal(expected_entity, actual) for actual in all_entities)
        assert matched, f"Missing entity match: {expected_entity} in {all_entities}"

    # Debug output
    print(f"\nInput: {case['text']}\nSender ID: {sender_id}\nDetected Intents: {actual_intents}\nDetected Entities: {all_entities}\nFull Response: {result}")
