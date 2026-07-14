"""
Regression: temporal proposals must not suppress SEARCH_AVAILABILITY.

Scenario:
1. "book haircut tomorrow by 9am" → service disambiguation, proposals persisted
2. "flexi" → service resolved, planner must SEARCH (not silent success)
"""

import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock

import pytest

src_path = Path(__file__).parent.parent.parent.parent
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

from core.session.persist import build_session_state_from_outcome
from core.adapters.clients.organization_client import OrganizationClient
from core.execution.clients.availability_client import AvailabilityClient
from core.adapters.nlu import LumaClient
from core.api.compat import handle_message


class _StatefulSessionStore:
    def __init__(self, state=None):
        self._state = state or {}

    def get_session(self, _user_id):
        return self._state

    def save_session(self, _user_id, state):
        self._state = state


def _persist_session_from_result(result, previous_session, user_id, session_store):
    outcome = dict(result.get("outcome") or result.get("result") or {})
    plan = result.get("plan") or {}
    if not outcome.get("intent_name") and not outcome.get("intent"):
        plan_intent = plan.get("intent_name") or plan.get("intent")
        if plan_intent:
            outcome["intent_name"] = plan_intent
    outcome_status = outcome.get("status") or "success"
    merged = result.get("_merged_luma_response")
    new_session = build_session_state_from_outcome(
        outcome,
        outcome_status,
        merged,
        previous_session,
        user_id,
        session_store,
    )
    if new_session and session_store is not None:
        session_store.save_session(user_id, new_session)
    return new_session


def test_flexi_after_temporal_proposals_triggers_availability_search():
    frozen_time = datetime(2026, 7, 7, 10, 0, 0, tzinfo=timezone.utc)
    user_id = "test_proposal_flexi_search"
    session_store = _StatefulSessionStore()

    mock_org_client = Mock(spec=OrganizationClient)
    mock_org_client.get_details.return_value = {
        "organization": {"businessCategoryId": 1}
    }

    mock_availability_client = Mock(spec=AvailabilityClient)
    mock_availability_client.get_service_availability.return_value = {
        "slots": [
            {
                "start": "2026-07-08T09:00:00Z",
                "end": "2026-07-08T09:30:00Z",
                "staff_id": 1,
            },
            {
                "start": "2026-07-08T10:00:00Z",
                "end": "2026-07-08T10:30:00Z",
                "staff_id": 1,
            },
        ]
    }

    mock_luma_turn1 = Mock(spec=LumaClient)
    mock_luma_turn1.resolve.return_value = {
        "success": True,
        "intent": {"name": "CREATE_APPOINTMENT"},
        "facts": {"dates": ["2026-07-08"], "times": ["09:00"]},
        "slots": {},
        "service_candidates": [
            {"text": "premium haircut"},
            {"text": "flexi haircut + prunning"},
        ],
        "time_constraint": {"mode": "exact", "start": "09:00", "end": "09:00"},
        "missing_slots": ["service_id"],
        "needs_clarification": True,
    }

    result1 = handle_message(
        text="book haircut tomorrow by 9am",
        user_id=user_id,
        luma_client=mock_luma_turn1,
        organization_client=mock_org_client,
        session_store=session_store,
        frozen_time=frozen_time,
        organization_id=1,
    )

    assert result1.get("success") is True, result1.get("error")
    plan1 = result1.get("plan") or result1.get("result", {})
    assert plan1.get("status") == "NEEDS_CLARIFICATION"
    assert "service_id" in (plan1.get("missing_slots") or [])
    mock_availability_client.get_service_availability.assert_not_called()

    session_state = _persist_session_from_result(
        result1, None, user_id, session_store
    )
    assert session_state is not None
    assert session_state.get("slots") == {}
    assert session_state.get("date_proposal", {}).get("start") == "2026-07-08"
    assert session_state.get("time_proposal", {}).get("value") == "09:00"

    mock_luma_turn2 = Mock(spec=LumaClient)
    mock_luma_turn2.resolve.return_value = {
        "success": True,
        "intent": {"name": "CREATE_APPOINTMENT"},
        "facts": {"service_id": "flexi haircut + prunning"},
        "slots": {"service_id": "flexi haircut + prunning"},
        "missing_slots": [],
        "needs_clarification": False,
    }

    result2 = handle_message(
        text="flexi",
        user_id=user_id,
        luma_client=mock_luma_turn2,
        availability_client=mock_availability_client,
        organization_client=mock_org_client,
        session_store=session_store,
        frozen_time=frozen_time,
        organization_id=1,
    )

    assert result2.get("success") is True, result2.get("error")
    plan2 = result2.get("plan") or {}
    assert plan2.get("action") == "SEARCH_AVAILABILITY", (
        f"expected SEARCH_AVAILABILITY, got {plan2.get('action')!r} "
        f"status={plan2.get('status')!r}"
    )

    mock_availability_client.get_service_availability.assert_called_once()

    execution_result = result2.get("result") or {}
    assert execution_result.get("type") == "availability"
    assert execution_result.get("status") == "success"
    assert execution_result.get("slots"), "expected availability slots in response"
    assert result2.get("text"), "expected rendered availability text, not empty success"
