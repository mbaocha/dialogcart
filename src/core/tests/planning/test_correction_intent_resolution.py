"""
Tests for CORRECTION intent resolution and turn_planner reroute contract.

NLU classifies in-flow slot updates as CORRECTION. Core must not reclassify
MODIFY_* as corrections; slot corrections are adopted via:
  - resolve_effective_intent: non-core side-intent preservation (NEEDS_CLARIFICATION,
    READY+CREATE_APPOINTMENT)
  - turn_planner: CORRECTION reroute to session durable intent (all statuses)
"""

import sys
from pathlib import Path
from unittest.mock import patch

src_path = Path(__file__).parent.parent.parent.parent
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

from core.planning.planner.intent_resolution import resolve_effective_intent


def test_correction_preserves_create_appointment_session():
    """CORRECTION over READY CREATE_APPOINTMENT keeps session intent, no reset."""
    luma_response = {
        "intent": {"name": "CORRECTION"},
        "facts": {"service_id": "massage"},
        "slots": {"service_id": "massage"},
        "missing_slots": [],
    }
    session_state = {
        "intent_name": "CREATE_APPOINTMENT",
        "status": "READY",
        "slots": {
            "service_id": "haircut",
            "date": "2026-01-16",
            "time": "14:00",
        },
    }

    with patch("core.policy.intent_policy.get_intent_durable") as mock_durable:
        mock_durable.return_value = True
        effective_intent, session_reset = resolve_effective_intent(
            luma_response, session_state, "test_user"
        )

    assert effective_intent == "CREATE_APPOINTMENT"
    assert session_reset is False


def test_correction_preserves_modify_booking_session_needs_clarification():
    """CORRECTION during MODIFY slot-fill keeps MODIFY_BOOKING intent."""
    luma_response = {
        "intent": {"name": "CORRECTION"},
        "facts": {"booking_id": "ABC12345"},
        "slots": {"booking_id": "ABC12345"},
        "missing_slots": [],
    }
    session_state = {
        "intent_name": "MODIFY_BOOKING",
        "status": "NEEDS_CLARIFICATION",
        "slots": {},
        "missing_slots": ["booking_id", "date"],
    }

    with patch("core.policy.intent_policy.get_intent_durable") as mock_durable:
        mock_durable.return_value = True
        effective_intent, session_reset = resolve_effective_intent(
            luma_response, session_state, "test_user"
        )

    assert effective_intent == "MODIFY_BOOKING"
    assert session_reset is False


def test_correction_ready_modify_unresolved_by_intent_resolution():
    """READY MODIFY + CORRECTION stays CORRECTION until turn_planner reroute."""
    luma_response = {
        "intent": {"name": "CORRECTION"},
        "facts": {"time": "17:00"},
        "slots": {"time": "17:00"},
        "missing_slots": [],
    }
    session_state = {
        "intent_name": "MODIFY_BOOKING",
        "status": "READY",
        "slots": {
            "booking_id": "ABC12345",
            "date": "2026-01-16",
            "time": "15:00",
        },
    }

    with patch("core.policy.intent_policy.get_intent_durable") as mock_durable:
        mock_durable.return_value = True
        effective_intent, session_reset = resolve_effective_intent(
            luma_response, session_state, "test_user"
        )

    assert effective_intent == "CORRECTION"
    assert session_reset is False


def test_modify_without_booking_id_switches_from_create_session():
    """MODIFY without booking_id is a true intent switch; NLU should use CORRECTION."""
    luma_response = {
        "intent": {"name": "MODIFY_BOOKING"},
        "facts": {"service_id": "massage"},
        "slots": {"service_id": "massage"},
        "missing_slots": ["booking_id"],
    }
    session_state = {
        "intent_name": "CREATE_APPOINTMENT",
        "status": "READY",
        "slots": {
            "service_id": "haircut",
            "date": "2026-01-16",
            "time": "14:00",
        },
    }

    with patch("core.policy.intent_policy.get_intent_durable") as mock_durable:
        mock_durable.side_effect = lambda name: name in (
            "CREATE_APPOINTMENT",
            "MODIFY_BOOKING",
        )
        with patch(
            "core.session.session_manager.clear_session"
        ) as mock_clear:
            effective_intent, session_reset = resolve_effective_intent(
                luma_response, session_state, "test_user"
            )

    assert effective_intent == "MODIFY_BOOKING"
    assert session_reset is True
    mock_clear.assert_called_once_with("test_user")


def test_modify_with_booking_id_switches_from_create_session():
    """Real MODIFY with booking_id is still a true intent switch from CREATE."""
    luma_response = {
        "intent": {"name": "MODIFY_BOOKING"},
        "facts": {"booking_id": "ABC12345"},
        "slots": {"booking_id": "ABC12345"},
        "missing_slots": ["date"],
    }
    session_state = {
        "intent_name": "CREATE_APPOINTMENT",
        "status": "READY",
        "slots": {"service_id": "haircut", "date": "2026-01-16", "time": "14:00"},
    }

    with patch("core.policy.intent_policy.get_intent_durable") as mock_durable:
        mock_durable.side_effect = lambda name: name in (
            "CREATE_APPOINTMENT",
            "MODIFY_BOOKING",
        )
        with patch(
            "core.session.session_manager.clear_session"
        ) as mock_clear:
            effective_intent, session_reset = resolve_effective_intent(
                luma_response, session_state, "test_user"
            )

    assert effective_intent == "MODIFY_BOOKING"
    assert session_reset is True
    mock_clear.assert_called_once_with("test_user")


def test_correction_without_session_stays_correction():
    """Cold CORRECTION with no session is not rerouted by intent_resolution."""
    luma_response = {
        "intent": {"name": "CORRECTION"},
        "facts": {"service_id": "massage"},
        "slots": {},
        "missing_slots": [],
    }

    effective_intent, session_reset = resolve_effective_intent(
        luma_response, None, "test_user"
    )

    assert effective_intent == "CORRECTION"
    assert session_reset is False
