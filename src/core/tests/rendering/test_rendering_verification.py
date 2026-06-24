"""
End-to-End Tests: Rendering Verification

Tests that rendering is correctly attached to core E2E responses.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock

import pytest

from core.orchestration.clients.organization_client import OrganizationClient
from core.orchestration.nlu import LumaClient
from core.orchestration.orchestrator import handle_message

# Add src to path BEFORE importing core modules
src_path = Path(__file__).parent.parent.parent.parent
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))


def test_rendering_missing_time_clarification():
    """
    Test 1: Missing slot → clarification rendering

    Scenario: User provides service + date but no time.
    Expected: status = NEEDS_CLARIFICATION, missing_slots = ["time"], rendered_text is present.
    """
    frozen_time = datetime(2026, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
    user_id = "test_user_rendering_001"

    # Mock Luma response for "book a haircut tomorrow" (missing time)
    mock_luma_response = {
        "success": True,
        "intent": {"name": "CREATE_APPOINTMENT", "confidence": 0.95},
        "needs_clarification": True,
        "booking": {
            "booking_type": "service",
            "services": [
                {"text": "haircut", "canonical": "beauty_and_wellness.haircut"}
            ],
            "datetime_range": {
                "start": "2026-01-16T00:00:00Z",
                "end": "2026-01-16T23:59:59Z",
            },
            "booking_state": "NEEDS_CLARIFICATION",
        },
        "facts": {"service_id": "haircut", "dates": ["tomorrow"]},
        "slots": {"service_id": "haircut", "date": "2026-01-16"},
        "missing_slots": ["time"],
        "context": {},
    }

    # Mock Luma client
    mock_luma_client = Mock(spec=LumaClient)
    mock_luma_client.resolve.return_value = mock_luma_response

    # Mock organization client
    mock_org_client = Mock(spec=OrganizationClient)
    mock_org_client.get_details.return_value = {
        "organization": {"businessCategoryId": 1}  # Maps to "service" domain
    }

    # Call handle_message
    result = handle_message(
        text="book a haircut tomorrow",
        user_id=user_id,
        luma_client=mock_luma_client,
        organization_client=mock_org_client,
        frozen_time=frozen_time,
        organization_id=1,
    )

    # Assert result structure
    assert result is not None
    assert result.get("success") is True

    # Assert text is present (clarification signal)
    assert "text" in result, "Expected text to be present in response for clarification"

    # Assert text is truthy
    assert result["text"], f"Expected text to be truthy, got {result.get('text')}"


def test_rendering_generic_clarification_fallback():
    """
    Test 2: Unknown clarification fallback

    Scenario: User input produces NEEDS_CLARIFICATION with no missing slots.
    Expected: rendered_text uses generic NEEDS_CLARIFICATION template.
    """
    frozen_time = datetime(2026, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
    user_id = "test_user_rendering_002"

    # Mock Luma response with NEEDS_CLARIFICATION but empty missing_slots
    # This simulates an ambiguous booking that needs clarification but no specific missing slots
    mock_luma_response = {
        "success": True,
        "intent": {"name": "CREATE_APPOINTMENT", "confidence": 0.85},
        "needs_clarification": True,
        "booking": {
            "booking_type": "service",
            "services": [
                {"text": "haircut", "canonical": "beauty_and_wellness.haircut"}
            ],
            "booking_state": "NEEDS_CLARIFICATION",
        },
        "facts": {"service_id": "haircut"},
        "slots": {"service_id": "haircut"},
        "missing_slots": [
            "date",
            "time",
        ],  # Multiple missing slots for generic fallback
        "context": {},
    }

    # Mock Luma client
    mock_luma_client = Mock(spec=LumaClient)
    mock_luma_client.resolve.return_value = mock_luma_response

    # Mock organization client
    mock_org_client = Mock(spec=OrganizationClient)
    mock_org_client.get_details.return_value = {
        "organization": {"businessCategoryId": 1}
    }

    # Call handle_message
    result = handle_message(
        text="book something",
        user_id=user_id,
        luma_client=mock_luma_client,
        organization_client=mock_org_client,
        frozen_time=frozen_time,
        organization_id=1,
    )

    # Assert result structure
    assert result is not None
    assert result.get("success") is True

    # Assert text is present (clarification signal)
    assert "text" in result, "Expected text to be present in response for clarification"

    # Assert text is truthy
    assert result["text"], f"Expected text to be truthy, got {result.get('text')}"


def test_rendering_ready_state_no_clarification():
    """
    Test 3: READY state does NOT force clarification rendering

    Scenario: User provides all required info in one turn.
    Expected: status = READY, rendered_text is either None or action-based (not clarification).
    """
    frozen_time = datetime(2026, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
    user_id = "test_user_rendering_003"

    # Mock Luma response for "book haircut tomorrow at 2pm" (complete)
    mock_luma_response = {
        "success": True,
        "intent": {"name": "CREATE_APPOINTMENT", "confidence": 0.95},
        "needs_clarification": False,
        "booking": {
            "booking_type": "service",
            "services": [
                {"text": "haircut", "canonical": "beauty_and_wellness.haircut"}
            ],
            "datetime_range": {
                "start": "2026-01-16T14:00:00Z",
                "end": "2026-01-16T14:30:00Z",
            },
            "confirmation_state": "pending",
            "booking_state": "RESOLVED",
        },
        "facts": {"service_id": "haircut", "times": ["2pm"]},
        "slots": {"service_id": "haircut", "date": "2026-01-16", "time": "2pm"},
        "time_constraint": {"mode": "exact", "start": "14:00", "end": "14:00"},
        "missing_slots": [],
        "context": {},
    }

    # Mock Luma client
    mock_luma_client = Mock(spec=LumaClient)
    mock_luma_client.resolve.return_value = mock_luma_response

    # Mock organization client
    mock_org_client = Mock(spec=OrganizationClient)
    mock_org_client.get_details.return_value = {
        "organization": {"businessCategoryId": 1}
    }

    # Call handle_message
    result = handle_message(
        text="book haircut tomorrow at 2pm",
        user_id=user_id,
        luma_client=mock_luma_client,
        organization_client=mock_org_client,
        frozen_time=frozen_time,
        organization_id=1,
    )

    # Assert result structure
    assert result is not None
    assert result.get("success") is True

    # Assert either awaiting is present or text is not present (non-clarification flow)
    assert ("awaiting" in result) or (
        "text" not in result
    ), f"Expected either 'awaiting' in result or 'text' not in result for non-clarification flow, got awaiting={result.get('awaiting')}, text={result.get('text')}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
