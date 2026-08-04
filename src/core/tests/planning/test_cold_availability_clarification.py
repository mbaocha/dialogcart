"""Cold-start AVAILABILITY must clarify missing service — not NON_DURABLE_INTENT.

Durability (session ownership) stays false; plannability lets Stage 01 continue.
"""

from datetime import datetime, timezone
from unittest.mock import Mock

from core.adapters.clients.organization_client import OrganizationClient
from core.adapters.nlu import LumaClient
from core.api.compat import handle_message
from core.planning.pipeline.stage01_intent import reconcile_intent
from core.policy.intent_policy import get_intent_durable, is_intent_plannable
from core.tests.harness.clients import stub_catalog_client


def test_availability_is_plannable_but_not_durable():
    assert is_intent_plannable("AVAILABILITY") is True
    assert get_intent_durable("AVAILABILITY") is False


def test_unknown_intent_is_neither_plannable_nor_durable():
    assert is_intent_plannable("BOOKING_INQUIRY") is False
    assert get_intent_durable("BOOKING_INQUIRY") is False


def test_stage01_cold_availability_does_not_short_circuit():
    decision, _ = reconcile_intent(
        luma_response={
            "intent": {"name": "AVAILABILITY"},
            "facts": {"dates": ["2026-07-24"]},
            "slots": {},
            "missing_slots": [],
        },
        session_state=None,
        user_id="cold_avail_stage01",
        organization_id=1,
        source_text="show slots for july 24",
    )

    assert decision.planning_intent == "AVAILABILITY"
    assert decision.non_durable_status is None
    assert decision.handler_delegated is False


def test_stage01_genuine_non_durable_still_short_circuits():
    decision, _ = reconcile_intent(
        luma_response={
            "intent": {"name": "BOOKING_INQUIRY"},
            "facts": {},
            "slots": {},
        },
        session_state=None,
        user_id="non_durable_passthrough",
        organization_id=1,
        source_text="what is my booking status?",
    )

    assert decision.planning_intent == "BOOKING_INQUIRY"
    assert decision.non_durable_status == "NON_DURABLE_INTENT"


def test_cold_availability_needs_service_clarification():
    """show slots for july 24 → NEEDS_CLARIFICATION + missing service_id."""
    frozen_time = datetime(2026, 7, 1, 10, 0, 0, tzinfo=timezone.utc)
    user_id = "test_cold_availability_clarification"

    mock_org = Mock(spec=OrganizationClient)
    mock_org.get_details.return_value = {
        "organization": {"businessCategoryId": 1}
    }

    mock_luma = Mock(spec=LumaClient)
    mock_luma.resolve.return_value = {
        "success": True,
        "intent": {"name": "AVAILABILITY"},
        "facts": {"dates": ["2026-07-24"]},
        "slots": {},
        "missing_slots": [],
        "needs_clarification": False,
    }

    result = handle_message(
        text="show slots for july 24",
        user_id=user_id,
        luma_client=mock_luma,
        organization_client=mock_org,
        catalog_client=stub_catalog_client(),
        frozen_time=frozen_time,
        organization_id=1,
    )

    assert result.get("success") is True, result.get("error")
    plan = result.get("plan") or result.get("result") or {}
    assert plan.get("intent_name") == "AVAILABILITY"
    assert plan.get("status") == "NEEDS_CLARIFICATION"
    assert plan.get("status") != "NON_DURABLE_INTENT"
    missing = plan.get("missing_slots") or []
    assert "service_id" in list(missing)
