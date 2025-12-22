#!/usr/bin/env python3
"""
Test cases for reservation intent resolver.

Tests rule-based intent resolution for all 10 production intents.
"""
import sys
import importlib.util
from pathlib import Path

# Load modules directly to avoid import issues
script_dir = Path(__file__).parent.resolve()

# Mock luma.config before importing
mock_config = type('MockConfig', (), {
    'DEBUG_ENABLED': False
})()
sys.modules['luma'] = type('MockLuma', (), {})()
sys.modules['luma.config'] = mock_config

# Load reservation_intent_resolver module
resolver_path = script_dir / "reservation_intent_resolver.py"
spec_resolver = importlib.util.spec_from_file_location(
    "luma.grouping.reservation_intent_resolver", resolver_path)
resolver_module = importlib.util.module_from_spec(spec_resolver)
resolver_module.__package__ = "luma.grouping"
resolver_module.__name__ = "luma.grouping.reservation_intent_resolver"
sys.modules["luma.grouping.reservation_intent_resolver"] = resolver_module
spec_resolver.loader.exec_module(resolver_module)

resolve_intent = resolver_module.resolve_intent
ReservationIntentResolver = resolver_module.ReservationIntentResolver

# Import all 10 intents
DISCOVERY = resolver_module.DISCOVERY
DETAILS = resolver_module.DETAILS
AVAILABILITY = resolver_module.AVAILABILITY
QUOTE = resolver_module.QUOTE
RECOMMENDATION = resolver_module.RECOMMENDATION
CREATE_BOOKING = resolver_module.CREATE_BOOKING
BOOKING_INQUIRY = resolver_module.BOOKING_INQUIRY
MODIFY_BOOKING = resolver_module.MODIFY_BOOKING
CANCEL_BOOKING = resolver_module.CANCEL_BOOKING
PAYMENT = resolver_module.PAYMENT
UNKNOWN = resolver_module.UNKNOWN


def test_payment_intent():
    """Test PAYMENT intent detection."""
    test_cases = [
        ("i want to pay", {}),
        ("how do i make a payment", {}),
        ("what's my balance", {}),
        ("i need to pay my deposit", {}),
        ("refund my booking", {}),
    ]

    for sentence, entities in test_cases:
        intent, confidence = resolve_intent(sentence, entities)
        assert intent == PAYMENT, f"Expected PAYMENT, got {intent} for '{sentence}'"
        assert confidence >= 0.9, f"Expected high confidence for payment"

    print("  [OK] PAYMENT intent: PASSED")


def test_cancel_booking_intent():
    """Test CANCEL_BOOKING intent detection."""
    test_cases = [
        ("cancel my appointment", {}),
        ("i need to cancel", {}),
        ("delete my booking", {}),
        ("can't make it tomorrow", {}),
        ("cannot make it", {}),
    ]

    for sentence, entities in test_cases:
        intent, confidence = resolve_intent(sentence, entities)
        assert intent == CANCEL_BOOKING, f"Expected CANCEL_BOOKING, got {intent} for '{sentence}'"
        assert confidence >= 0.9, f"Expected high confidence for cancel"

    print("  [OK] CANCEL_BOOKING intent: PASSED")


def test_modify_booking_intent():
    """Test MODIFY_BOOKING intent detection."""
    test_cases = [
        ("reschedule my appointment", {}),
        ("move my booking to tomorrow", {}),
        ("change time to 3pm", {}),
        ("change date to next week", {}),
        ("postpone my appointment", {}),
        ("update my booking", {}),
    ]

    for sentence, entities in test_cases:
        intent, confidence = resolve_intent(sentence, entities)
        assert intent == MODIFY_BOOKING, f"Expected MODIFY_BOOKING, got {intent} for '{sentence}'"
        assert confidence >= 0.9, f"Expected high confidence for modify"

    print("  [OK] MODIFY_BOOKING intent: PASSED")


def test_create_booking_intent():
    """Test CREATE_BOOKING intent detection (including fallback with missing service)."""
    test_cases = [
        (
            "book me a haircut tomorrow",
            {
                "business_categories": [{"text": "haircut"}],
                "dates": [{"text": "tomorrow"}],
                "times": [],
                "time_windows": [],
                "durations": []
            },
            0.9  # min confidence
        ),
        (
            "schedule beard trim at 9am",
            {
                "business_categories": [{"text": "beard trim"}],
                "dates": [],
                "times": [{"text": "9am"}],
                "time_windows": [],
                "durations": []
            },
            0.9
        ),
        (
            "reserve haircut for one hour",
            {
                "business_categories": [{"text": "haircut"}],
                "dates": [],
                "times": [],
                "time_windows": [],
                "durations": [{"text": "one hour"}]
            },
            0.9
        ),
        # Fallback scenario: verbs + date, but service extraction fails: should still trigger CREATE_BOOKING
        (
            "I want to book a full body massage this Friday at 4pm",
            {
                "business_categories": [],  # Service extraction failed
                "dates": [{"text": "this Friday"}],
                "times": [{"text": "4pm"}],
                "time_windows": [],
                "durations": []
            },
            0.85
        ),
        # Fallback: verbs + vague time
        (
            "Schedule appointment for about 6ish",
            {
                "business_categories": [],
                "dates": [],
                "times": [{"text": "6ish"}],  # Not exact, but still triggers fallback
                "time_windows": [],
                "durations": []
            },
            0.85
        )
    ]

    for sentence, entities, min_conf in test_cases:
        intent, confidence = resolve_intent(sentence, entities)
        assert intent == CREATE_BOOKING, f"Expected CREATE_BOOKING, got {intent} for '{sentence}'"
        assert confidence >= min_conf, f"Expected >= {min_conf} confidence for booking, got {confidence}"

    print("  [OK] CREATE_BOOKING intent (+fallback): PASSED  [Guardrail: Fallback supports booking verbs + date/time]")



def test_availability_intent():
    """Test AVAILABILITY intent detection."""
    test_cases = [
        (
            "are you available tomorrow?",
            {
                "dates": [{"text": "tomorrow"}],
                "times": [],
                "time_windows": [],
                "durations": []
            }
        ),
        (
            "do you have any slots?",
            {}
        ),
        (
            "what times are available?",
            {}
        ),
        (
            "is there availability next week?",
            {
                "dates": [{"text": "next week"}],
                "times": [],
                "time_windows": [],
                "durations": []
            }
        ),
    ]

    for sentence, entities in test_cases:
        intent, confidence = resolve_intent(sentence, entities)
        assert intent == AVAILABILITY, f"Expected AVAILABILITY, got {intent} for '{sentence}'"
        assert confidence >= 0.8, f"Expected medium+ confidence for availability"

    print("  [OK] AVAILABILITY intent: PASSED")


def test_booking_inquiry_intent():
    """Test BOOKING_INQUIRY intent detection."""
    test_cases = [
        ("my appointment", {}),
        ("my booking", {}),
        ("show my appointment", {}),
        ("when is my appointment", {}),
        ("what time is my booking", {}),
        ("status of my booking", {}),
    ]

    for sentence, entities in test_cases:
        intent, confidence = resolve_intent(sentence, entities)
        assert intent == BOOKING_INQUIRY, f"Expected BOOKING_INQUIRY, got {intent} for '{sentence}'"
        assert confidence >= 0.9, f"Expected high confidence for booking inquiry"

    print("  [OK] BOOKING_INQUIRY intent: PASSED")


def test_details_intent():
    """Test DETAILS intent detection."""
    test_cases = [
        (
            "does standard room include breakfast",
            {
                "business_categories": [{"text": "standard room"}]
            }
        ),
        (
            "how long does a haircut take",
            {
                "business_categories": [{"text": "haircut"}]
            }
        ),
        (
            "what's your cancellation policy",
            {}
        ),
        (
            "what's your address",
            {}
        ),
        (
            "what are your hours",
            {}
        ),
    ]

    for sentence, entities in test_cases:
        intent, confidence = resolve_intent(sentence, entities)
        assert intent == DETAILS, f"Expected DETAILS, got {intent} for '{sentence}'"
        assert confidence >= 0.8, f"Expected medium+ confidence for details"

    print("  [OK] DETAILS intent: PASSED")


def test_quote_intent():
    """Test QUOTE intent detection."""
    test_cases = [
        (
            "how much does a haircut cost",
            {
                "business_categories": [{"text": "haircut"}]
            }
        ),
        (
            "what's the price for standard room",
            {
                "business_categories": [{"text": "standard room"}]
            }
        ),
        (
            "how much",
            {}
        ),
        (
            "what's the cost",
            {}
        ),
    ]

    for sentence, entities in test_cases:
        intent, confidence = resolve_intent(sentence, entities)
        assert intent == QUOTE, f"Expected QUOTE, got {intent} for '{sentence}'"
        assert confidence >= 0.8, f"Expected medium+ confidence for quote"

    print("  [OK] QUOTE intent: PASSED")


def test_discovery_intent():
    """Test DISCOVERY intent detection."""
    test_cases = [
        (
            "what services do you offer",
            {
                "business_categories": [{"text": "haircut"}],
                "dates": [],
                "times": [],
                "time_windows": [],
                "durations": []
            }
        ),
        (
            "what rooms do you have",
            {
                "business_categories": [{"text": "standard room"}],
                "dates": [],
                "times": [],
                "time_windows": [],
                "durations": []
            }
        ),
        (
            "tell me about your services",
            {
                "business_categories": [{"text": "haircut"}],
                "dates": [],
                "times": [],
                "time_windows": [],
                "durations": []
            }
        ),
    ]

    for sentence, entities in test_cases:
        intent, confidence = resolve_intent(sentence, entities)
        assert intent == DISCOVERY, f"Expected DISCOVERY, got {intent} for '{sentence}'"
        assert confidence >= 0.8, f"Expected medium+ confidence for discovery"

    print("  [OK] DISCOVERY intent: PASSED")


def test_recommendation_intent():
    """Test RECOMMENDATION intent detection."""
    test_cases = [
        ("what do you recommend", {}),
        ("can you suggest something", {}),
        ("what's the best option", {}),
        ("help me choose", {}),
        ("which is better", {}),
    ]

    for sentence, entities in test_cases:
        intent, confidence = resolve_intent(sentence, entities)
        assert intent == RECOMMENDATION, f"Expected RECOMMENDATION, got {intent} for '{sentence}'"
        assert confidence >= 0.8, f"Expected medium+ confidence for recommendation"

    print("  [OK] RECOMMENDATION intent: PASSED")


def test_unknown_intent():
    """Test UNKNOWN intent for unmatched cases."""
    test_cases = [
        ("hello", {}),
        ("thanks", {}),
        ("okay", {}),
    ]

    for sentence, entities in test_cases:
        intent, confidence = resolve_intent(sentence, entities)
        assert intent == UNKNOWN, f"Expected UNKNOWN, got {intent} for '{sentence}'"

    print("  [OK] UNKNOWN intent: PASSED")


def test_rule_priority():
    """Test that rules are applied in correct priority order."""
    # Payment should win over everything
    sentence = "i want to pay for my booking"
    entities = {
        "business_categories": [{"text": "haircut"}],
        "dates": [{"text": "tomorrow"}],
    }
    intent, _ = resolve_intent(sentence, entities)
    assert intent == PAYMENT, "Payment should win over booking"

    # Cancel should win over booking
    sentence = "cancel my haircut appointment"
    entities = {
        "business_categories": [{"text": "haircut"}],
        "dates": [{"text": "tomorrow"}],
    }
    intent, _ = resolve_intent(sentence, entities)
    assert intent == CANCEL_BOOKING, "Cancel should win over booking"

    # Modify should win over booking
    sentence = "reschedule my haircut appointment"
    entities = {
        "business_categories": [{"text": "haircut"}],
        "dates": [{"text": "tomorrow"}],
    }
    intent, _ = resolve_intent(sentence, entities)
    assert intent == MODIFY_BOOKING, "Modify should win over booking"

    # Booking inquiry should win over availability
    sentence = "when is my appointment available"
    entities = {}
    intent, _ = resolve_intent(sentence, entities)
    assert intent == BOOKING_INQUIRY, "Booking inquiry should win over availability"

    print("  [OK] Rule priority: PASSED")


def main():
    """Run all test cases."""
    print("=" * 70)
    print("RESERVATION INTENT RESOLVER TEST SUITE")
    print("Testing all 10 production intents")
    print("=" * 70)
    print()

    test_payment_intent()
    test_cancel_booking_intent()
    test_modify_booking_intent()
    test_create_booking_intent()
    test_availability_intent()
    test_booking_inquiry_intent()
    test_details_intent()
    test_quote_intent()
    test_discovery_intent()
    test_recommendation_intent()
    test_unknown_intent()
    test_rule_priority()

    print()
    print("=" * 70)
    print("ALL TESTS PASSED")
    print("=" * 70)


if __name__ == "__main__":
    main()
