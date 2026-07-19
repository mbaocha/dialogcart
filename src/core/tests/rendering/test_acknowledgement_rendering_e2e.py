"""
End-to-End Tests: Acknowledgement Rendering (Phase 2)

Under current policy, service_id (+ optional date) with missing time yields READY
and exploratory SEARCH_AVAILABILITY. These tests omit availability_client and
assert the planning/missing-client response shape (no clarification text).
"""

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict
from unittest.mock import Mock

import pytest

from core.adapters.clients.organization_client import OrganizationClient
from core.adapters.nlu import LumaClient
from core.api.compat import handle_message
from core.tests.harness.clients import stub_catalog_client

# Add src to path BEFORE importing core modules
src_path = Path(__file__).parent.parent.parent.parent
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))


_TEMPORAL_SLOT_KEYS = frozenset(
    {"date", "time", "date_range", "datetime_range", "start_date", "end_date"}
)


def _build_mock_luma_response(
    status: str,
    missing_slots: list,
    slots: Dict[str, Any],
    slot_attempts: Dict[str, int] = None,
) -> Dict[str, Any]:
    """Build a mock Luma response for acknowledgement rendering tests.

    Unconfirmed date/time use facts + proposals (canonical). Durable
    slots.date / slots.time stay absent until bind/confirm.
    """
    raw = dict(slots or {})
    date_val = raw.pop("date", None)
    time_val = raw.pop("time", None)
    for key in _TEMPORAL_SLOT_KEYS:
        raw.pop(key, None)

    durable_slots = raw
    service_id = durable_slots.get("service_id", "haircut")
    facts: Dict[str, Any] = {"service_id": service_id}

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
        "facts": facts,
        "slots": durable_slots,
        "missing_slots": missing_slots,
        "context": {},
    }

    if slot_attempts:
        response["facts"]["slot_attempts"] = slot_attempts

    if date_val:
        date_str = date_val if isinstance(date_val, str) else date_val[0]
        facts["dates"] = [date_str]
        response["date_proposal"] = {"mode": "single_day", "start": date_str}

    if time_val:
        time_str = time_val if isinstance(time_val, str) else time_val[0]
        facts["times"] = [time_str]
        response["time_proposal"] = {"mode": "exact", "value": time_str}
        response["time_constraint"] = {
            "mode": "exact",
            "start": time_str,
            "end": time_str,
        }

    return response


def _assert_ready_missing_client(result: Dict[str, Any], expected_missing: list) -> None:
    assert result.get("success") is True
    outcome = result.get("outcome") or {}
    assert outcome.get("status") == "READY", (
        f"Expected READY for executable_with=[service_id], got {outcome.get('status')}"
    )
    missing = outcome.get("missing_slots")
    if missing is None:
        missing = (outcome.get("facts") or {}).get("missing_slots", [])
    assert set(missing) == set(expected_missing), (
        f"Expected missing_slots={expected_missing}, got {missing}"
    )
    allowed = outcome.get("allowed_actions") or []
    assert "SEARCH_AVAILABILITY" in allowed
    assert "text" not in result, (
        f"Expected no clarification text on missing-client READY response, "
        f"got keys={list(result.keys())}"
    )


def test_acknowledgement_appears_when_slot_just_filled():
    """
    Date filled, time still missing, no availability_client.

    Expect READY exploratory planning shape (not clarification acknowledgement text).
    """
    user_id = "test_ack_1"
    frozen_time = datetime(2026, 1, 15, 10, 0, 0, tzinfo=timezone.utc)

    session_state = {
        "intent_name": "CREATE_APPOINTMENT",
        "slots": {"service_id": "haircut"},
        "date_proposal": {"mode": "single_day", "start": "2026-01-16"},
        "missing_slots": ["time"],
        "status": "READY",
        "facts": {"service_id": "haircut", "dates": ["2026-01-16"]},
        "slot_attempts": {},
        "last_filled_slot": "date",
    }

    mock_luma_client = Mock(spec=LumaClient)
    mock_org_client = Mock(spec=OrganizationClient)
    mock_org_client.get_details.return_value = {
        "organization": {"businessCategoryId": 1}
    }

    mock_luma_response = _build_mock_luma_response(
        status="NEEDS_CLARIFICATION",
        missing_slots=["time"],
        slots={"service_id": "haircut", "date": "2026-01-16"},
        slot_attempts={},
    )
    mock_luma_client.resolve.return_value = mock_luma_response

    result = handle_message(
        text="tomorrow",
        user_id=user_id,
        luma_client=mock_luma_client,
        organization_client=mock_org_client,
        catalog_client=stub_catalog_client(),
        frozen_time=frozen_time,
        organization_id=1,
        session_state=session_state,
    )

    _assert_ready_missing_client(result, ["time"])


def test_acknowledgement_does_not_appear_on_retry():
    """
    Retry attempt with time still missing, no availability_client.

    Expect READY exploratory planning shape (not adaptive clarification text).
    """
    user_id = "test_ack_2"
    frozen_time = datetime(2026, 1, 15, 10, 0, 0, tzinfo=timezone.utc)

    session_state = {
        "intent_name": "CREATE_APPOINTMENT",
        "slots": {"service_id": "haircut"},
        "date_proposal": {"mode": "single_day", "start": "2026-01-16"},
        "missing_slots": ["time"],
        "status": "READY",
        "facts": {"service_id": "haircut", "dates": ["2026-01-16"]},
        "slot_attempts": {"time": 1},
        "last_filled_slot": "date",
    }

    mock_luma_client = Mock(spec=LumaClient)
    mock_org_client = Mock(spec=OrganizationClient)
    mock_org_client.get_details.return_value = {
        "organization": {"businessCategoryId": 1}
    }

    mock_luma_response = _build_mock_luma_response(
        status="NEEDS_CLARIFICATION",
        missing_slots=["time"],
        slots={"service_id": "haircut", "date": "2026-01-16"},
        slot_attempts={"time": 1},
    )
    mock_luma_client.resolve.return_value = mock_luma_response

    result = handle_message(
        text="what time",
        user_id=user_id,
        luma_client=mock_luma_client,
        organization_client=mock_org_client,
        catalog_client=stub_catalog_client(),
        frozen_time=frozen_time,
        organization_id=1,
        session_state=session_state,
    )

    _assert_ready_missing_client(result, ["time"])


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
