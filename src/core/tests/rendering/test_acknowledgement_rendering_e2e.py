"""
End-to-End Tests: Acknowledgement Rendering (Phase 2)

Tests that acknowledgement prefix ("Got it. ") appears when:
- A slot was just filled (last_filled_slot is set)
- It differs from current missing slot
- attempt_count < 1 (first attempt)

Tests that acknowledgement does NOT appear on retry attempts (attempt_count >= 1).
"""

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict
from unittest.mock import Mock

import pytest

from unittest.mock import patch

from core.orchestration.clients.organization_client import OrganizationClient
from core.orchestration.nlu import LumaClient
from core.orchestration.orchestrator import handle_message

# Add src to path BEFORE importing core modules
src_path = Path(__file__).parent.parent.parent.parent
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))


def _build_mock_luma_response(
    status: str,
    missing_slots: list,
    slots: Dict[str, Any],
    slot_attempts: Dict[str, int] = None,
) -> Dict[str, Any]:
    """Build a mock Luma response for acknowledgement rendering tests."""
    service_id = slots.get("service_id", "haircut")

    response = {
        "success": True,
        "intent": {"name": "CREATE_APPOINTMENT", "confidence": 0.95},
        "needs_clarification": status == "NEEDS_CLARIFICATION",
        "booking": {
            "booking_type": "service",
            "services": [
                {"text": service_id, "canonical": f"beauty_and_wellness.{service_id}"}
            ],
            "booking_state": (
                "RESOLVED"
                if status in ("READY", "AWAITING_CONFIRMATION")
                else "NEEDS_CLARIFICATION"
            ),
        },
        "facts": {"service_id": service_id},
        "slots": slots.copy(),
        "missing_slots": missing_slots,
        "context": {},
    }

    # Add slot_attempts to facts if provided
    if slot_attempts:
        response["facts"]["slot_attempts"] = slot_attempts

    # Add datetime_range if date is present
    if "date" in slots:
        date_str = slots["date"]
        response["booking"]["datetime_range"] = {
            "start": f"{date_str}T00:00:00Z",
            "end": f"{date_str}T23:59:59Z",
        }

    return response


def test_acknowledgement_appears_when_slot_just_filled():
    """
    Test that acknowledgement appears when a slot was just filled.

    Scenario:
    - User provides date
    - System still needs time
    - session_state["last_filled_slot"] == "date"
    - attempt_count == 0 (first attempt)

    Expected:
    - Response starts with "Got it."
    - Response still mentions "time"
    - Clarification template text is preserved after prefix
    """
    user_id = "test_ack_1"
    frozen_time = datetime(2026, 1, 15, 10, 0, 0, tzinfo=timezone.utc)

    # Session state with last_filled_slot set to "date"
    session_state = {
        "intent_name": "CREATE_APPOINTMENT",
        "slots": {"service_id": "haircut", "date": "2026-01-16"},
        "missing_slots": ["time"],
        "status": "NEEDS_CLARIFICATION",
        "facts": {},
        "slot_attempts": {},
        "last_filled_slot": "date",
    }

    # Mock clients
    mock_luma_client = Mock(spec=LumaClient)
    mock_org_client = Mock(spec=OrganizationClient)
    mock_org_client.get_details.return_value = {
        "organization": {"businessCategoryId": 1}
    }

    # Build mock Luma response: user provided date, still needs time
    mock_luma_response = _build_mock_luma_response(
        status="NEEDS_CLARIFICATION",
        missing_slots=["time"],
        slots={"service_id": "haircut", "date": "2026-01-16"},
        slot_attempts={},  # attempt_count == 0 for "time"
    )

    mock_luma_client.resolve.return_value = mock_luma_response

    _llm_response = "Got it. What time would you like for your appointment?"

    with patch(
        "core.orchestration.orchestrator.render_llm", return_value=_llm_response
    ):
        result = handle_message(
            text="tomorrow",
            user_id=user_id,
            luma_client=mock_luma_client,
            organization_client=mock_org_client,
            frozen_time=frozen_time,
            organization_id=1,
            session_state=session_state,
        )

    assert "text" in result, f"Expected text in result, got keys: {list(result.keys())}"
    assert result["text"], f"Expected non-empty text, got: {result.get('text')}"
    assert result["text"] == _llm_response, (
        f"Expected LLM-rendered text, got: {result['text']}"
    )


def test_acknowledgement_does_not_appear_on_retry():
    """
    Test that acknowledgement does NOT appear on retry attempt.

    Scenario:
    - session_state["last_filled_slot"] == "date"
    - missing slot == "time"
    - attempt_count == 1 (retry attempt)

    Expected:
    - Response does NOT start with "Got it."
    - Response contains "still" (adaptive retry template)
    - Response still mentions "time"
    """
    user_id = "test_ack_2"
    frozen_time = datetime(2026, 1, 15, 10, 0, 0, tzinfo=timezone.utc)

    # Session state with last_filled_slot set to "date" and slot_attempts showing retry
    session_state = {
        "intent_name": "CREATE_APPOINTMENT",
        "slots": {"service_id": "haircut", "date": "2026-01-16"},
        "missing_slots": ["time"],
        "status": "NEEDS_CLARIFICATION",
        "facts": {},
        "slot_attempts": {"time": 1},  # attempt_count == 1 (retry)
        "last_filled_slot": "date",
    }

    # Mock clients
    mock_luma_client = Mock(spec=LumaClient)
    mock_org_client = Mock(spec=OrganizationClient)
    mock_org_client.get_details.return_value = {
        "organization": {"businessCategoryId": 1}
    }

    # Build mock Luma response: still needs time, with retry attempt
    mock_luma_response = _build_mock_luma_response(
        status="NEEDS_CLARIFICATION",
        missing_slots=["time"],
        slots={"service_id": "haircut", "date": "2026-01-16"},
        slot_attempts={"time": 1},  # attempt_count == 1 for "time"
    )

    mock_luma_client.resolve.return_value = mock_luma_response

    _llm_response = "Could you still let me know what time works best for you?"

    with patch(
        "core.orchestration.orchestrator.render_llm", return_value=_llm_response
    ):
        result = handle_message(
            text="what time",
            user_id=user_id,
            luma_client=mock_luma_client,
            organization_client=mock_org_client,
            frozen_time=frozen_time,
            organization_id=1,
            session_state=session_state,
        )

    assert "text" in result, f"Expected text in result, got keys: {list(result.keys())}"
    assert result["text"], f"Expected non-empty text, got: {result.get('text')}"
    assert not result["text"].startswith("Got it."), (
        f"Expected text to NOT start with 'Got it.' on retry, got: {result['text']}"
    )
    assert result["text"] == _llm_response, (
        f"Expected LLM-rendered text, got: {result['text']}"
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
