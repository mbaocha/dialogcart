"""CONFIRM_APPOINTMENT / CREATE_BOOKING_HOLD require resolved customer_id."""

from __future__ import annotations

from unittest.mock import MagicMock

from core.engine.execution_coordinator import ExecutionCoordinator
from core.execution.command_builder import build_execution_command
from core.session.confirmation_gate import get_confirmation_state


def _confirm_plan(**slot_overrides):
    slots = {
        "organization_id": 2,
        "service_id": 26,
        "date": "2026-08-04",
        "time": "09:00",
        "datetime_range": {
            "start": "2026-08-04T09:00:00+00:00",
            "end": "2026-08-04T10:00:00+00:00",
        },
    }
    slots.update(slot_overrides)
    return {
        "status": "READY",
        "intent_name": "CREATE_APPOINTMENT",
        "action": "CONFIRM_APPOINTMENT",
        "slots": slots,
        "missing_slots": [],
    }


def _resolve(coordinator, *, plan, session_state=None, kwargs=None):
    booking_client = MagicMock()
    command = build_execution_command(plan=plan, organization_id=2)
    return coordinator.resolve(
        plan=plan,
        session_state=session_state or {},
        session_store=None,
        user_id="test-user",
        availability_client=None,
        organization_client=None,
        organization_id=2,
        kwargs={"booking_client": booking_client, **(kwargs or {})},
        command=command,
    )


def test_confirm_blocked_without_customer_id_leaves_pending():
    """Identity block must not mutate confirmation — durable stays pending."""
    coordinator = ExecutionCoordinator()
    session = {
        "confirmation_state": "pending",
        "slots": {"service_id": "premium haircut"},
    }
    gate = _resolve(coordinator, plan=_confirm_plan(), session_state=session)
    assert gate.path == "blocked"
    assert gate.can_execute is False
    assert gate.response is not None
    assert "phone" in (gate.response.get("text") or "").lower()
    assert gate.plan.get("status") == "NEEDS_CLARIFICATION"
    assert gate.plan.get("action") is None
    assert (gate.response.get("outcome") or {}).get("status") == "NEEDS_CLARIFICATION"
    assert (gate.response.get("outcome") or {}).get("action") is None
    assert get_confirmation_state(session) == "pending"
    assert get_confirmation_state(gate.plan) == "pending"
    assert get_confirmation_state(gate.plan) != "confirmed"
    decision = gate.plan.get("_decision")
    assert isinstance(decision, dict)
    assert (decision.get("plan") or {}).get("status") == "NEEDS_CLARIFICATION"
    assert (decision.get("plan") or {}).get("action") is None


def test_confirm_ready_with_session_customer_id():
    coordinator = ExecutionCoordinator()
    session = {"customer_id": 55, "confirmation_state": "pending"}
    gate = _resolve(
        coordinator,
        plan=_confirm_plan(),
        session_state=session,
    )
    assert gate.path == "ready"
    assert gate.can_execute is True
    assert gate.slots.get("customer_id") == 55
    # Eligibility does not consume or rewrite confirmation_state.
    assert get_confirmation_state(session) == "pending"


def test_confirm_ready_with_kwargs_customer_id():
    coordinator = ExecutionCoordinator()
    gate = _resolve(
        coordinator,
        plan=_confirm_plan(),
        kwargs={"customer_id": 77},
    )
    assert gate.path == "ready"
    assert gate.slots.get("customer_id") == 77


def test_identity_block_preserves_pending_on_merged_and_session():
    """Coordinator must not repair confirmation; pending remains pending."""
    coordinator = ExecutionCoordinator()
    merged = {"confirmation_state": "pending", "slots": {"service_id": "x"}}
    plan = _confirm_plan()
    plan["_merged_luma_response"] = merged
    session = {"confirmation_state": "pending"}
    gate = _resolve(coordinator, plan=plan, session_state=session)
    assert gate.path == "blocked"
    assert get_confirmation_state(session) == "pending"
    assert get_confirmation_state(merged) == "pending"
    assert get_confirmation_state(gate.plan) != "confirmed"
    response_merged = gate.response.get("_merged_luma_response")
    assert response_merged == merged
    assert get_confirmation_state(response_merged) == "pending"


def test_identity_block_then_resolved_customer_allows_fresh_confirm():
    """After identity block leaves pending, a later turn with customer_id can execute."""
    coordinator = ExecutionCoordinator()
    session = {
        "confirmation_state": "pending",
        "slots": {
            "service_id": "premium haircut",
            "date": "2026-08-04",
            "time": "09:00",
        },
    }
    blocked = _resolve(coordinator, plan=_confirm_plan(), session_state=session)
    assert blocked.path == "blocked"
    assert get_confirmation_state(session) == "pending"

    session["customer_id"] = 91001
    ready = _resolve(
        coordinator,
        plan=_confirm_plan(),
        session_state=session,
    )
    assert ready.path == "ready"
    assert ready.slots.get("customer_id") == 91001
    # Gate does not consume pending — successful YES still required via Stage 06.
    assert get_confirmation_state(session) == "pending"


def test_coordinator_never_writes_confirmed():
    """ExecutionCoordinator must remain confirmation-lifecycle agnostic."""
    coordinator = ExecutionCoordinator()
    session = {"confirmation_state": "pending"}
    merged = {"confirmation_state": "pending"}
    plan = _confirm_plan()
    plan["_merged_luma_response"] = merged

    blocked = _resolve(coordinator, plan=plan, session_state=session)
    assert blocked.path == "blocked"
    assert get_confirmation_state(session) == "pending"
    assert get_confirmation_state(merged) == "pending"

    session["customer_id"] = 1
    ready = _resolve(coordinator, plan=_confirm_plan(), session_state=session)
    assert ready.path == "ready"
    assert get_confirmation_state(session) == "pending"
