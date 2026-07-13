"""
End-to-End Tests: Rendering Verification

Tests that rendering is correctly attached to core E2E responses.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import Mock

import pytest

from core.orchestration.clients.organization_client import OrganizationClient
from core.orchestration.nlu import LumaClient
from core.orchestration.orchestrator import handle_message

# Add src to path BEFORE importing core modules
src_path = Path(__file__).parent.parent.parent.parent
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))


def _outcome(result: Dict[str, Any]) -> Dict[str, Any]:
    outcome = result.get("outcome")
    return outcome if isinstance(outcome, dict) else {}


def _missing_slots(result: Dict[str, Any]) -> List[str]:
    outcome = _outcome(result)
    missing = outcome.get("missing_slots")
    if missing is None:
        facts = outcome.get("facts") or {}
        missing = facts.get("missing_slots", []) if isinstance(facts, dict) else []
    return list(missing) if isinstance(missing, list) else []


def _assert_ready_missing_client_planning(
    result: Dict[str, Any],
    *,
    expected_missing: Optional[List[str]] = None,
) -> None:
    """Assert exploratory READY planning shape when availability_client is omitted."""
    assert result is not None
    assert result.get("success") is True
    outcome = _outcome(result)
    assert outcome.get("status") == "READY", (
        f"Expected READY when executable_with=[service_id] is satisfied, "
        f"got {outcome.get('status')}"
    )
    missing = _missing_slots(result)
    if expected_missing is not None:
        assert set(missing) == set(expected_missing), (
            f"Expected missing_slots={expected_missing}, got {missing}"
        )
    allowed = outcome.get("allowed_actions") or []
    assert "SEARCH_AVAILABILITY" in allowed, (
        f"Expected SEARCH_AVAILABILITY in allowed_actions, got {allowed}"
    )
    assert "text" not in result, (
        f"Expected no clarification text on missing-client READY planning response, "
        f"got text={result.get('text')!r}"
    )


def test_rendering_missing_time_clarification():
    """
    service_id + date (time missing) with no availability_client.

    Product policy: READY + exploratory SEARCH_AVAILABILITY allowed; no
    clarification rendering when the availability client is omitted.
    """
    frozen_time = datetime(2026, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
    user_id = "test_user_rendering_001"

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

    mock_luma_client = Mock(spec=LumaClient)
    mock_luma_client.resolve.return_value = mock_luma_response

    mock_org_client = Mock(spec=OrganizationClient)
    mock_org_client.get_details.return_value = {
        "organization": {"businessCategoryId": 1}
    }

    result = handle_message(
        text="book a haircut tomorrow",
        user_id=user_id,
        luma_client=mock_luma_client,
        organization_client=mock_org_client,
        frozen_time=frozen_time,
        organization_id=1,
    )

    _assert_ready_missing_client_planning(result, expected_missing=["time"])


def test_rendering_generic_clarification_fallback():
    """
    service_id only (date/time missing) with no availability_client.

    Product policy: READY + exploratory SEARCH_AVAILABILITY; planning/missing-client
    response shape (no clarification text).
    """
    frozen_time = datetime(2026, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
    user_id = "test_user_rendering_002"

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
        "missing_slots": ["date", "time"],
        "context": {},
    }

    mock_luma_client = Mock(spec=LumaClient)
    mock_luma_client.resolve.return_value = mock_luma_response

    mock_org_client = Mock(spec=OrganizationClient)
    mock_org_client.get_details.return_value = {
        "organization": {"businessCategoryId": 1}
    }

    result = handle_message(
        text="book something",
        user_id=user_id,
        luma_client=mock_luma_client,
        organization_client=mock_org_client,
        frozen_time=frozen_time,
        organization_id=1,
    )

    _assert_ready_missing_client_planning(
        result, expected_missing=["date", "time"]
    )


def test_rendering_ready_state_no_clarification():
    """
    Complete booking info in one turn → confirmation flow.

    Assert canonical nested outcome.awaiting == USER_CONFIRMATION rather than
    a root-level awaiting field.
    """
    frozen_time = datetime(2026, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
    user_id = "test_user_rendering_003"

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

    mock_luma_client = Mock(spec=LumaClient)
    mock_luma_client.resolve.return_value = mock_luma_response

    mock_org_client = Mock(spec=OrganizationClient)
    mock_org_client.get_details.return_value = {
        "organization": {"businessCategoryId": 1}
    }

    result = handle_message(
        text="book haircut tomorrow at 2pm",
        user_id=user_id,
        luma_client=mock_luma_client,
        organization_client=mock_org_client,
        frozen_time=frozen_time,
        organization_id=1,
    )

    assert result is not None
    assert result.get("success") is True
    outcome = _outcome(result)
    assert outcome.get("awaiting") == "USER_CONFIRMATION", (
        f"Expected outcome.awaiting=USER_CONFIRMATION, got {outcome.get('awaiting')!r}"
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
