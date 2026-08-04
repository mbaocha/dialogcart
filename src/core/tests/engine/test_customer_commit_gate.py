"""CONFIRM_APPOINTMENT / CREATE_BOOKING_HOLD require resolved customer_id."""

from __future__ import annotations

from unittest.mock import MagicMock

from core.engine.execution_coordinator import ExecutionCoordinator


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
    return coordinator.resolve(
        plan=plan,
        session_state=session_state or {},
        session_store=None,
        user_id="test-user",
        availability_client=None,
        organization_client=None,
        organization_id=2,
        kwargs={"booking_client": booking_client, **(kwargs or {})},
    )


def test_confirm_blocked_without_customer_id():
    coordinator = ExecutionCoordinator()
    session = {"confirmation_state": "confirmed", "slots": {"service_id": "premium haircut"}}
    gate = _resolve(coordinator, plan=_confirm_plan(), session_state=session)
    assert gate.path == "skipped"
    assert gate.can_execute is False
    assert gate.response is not None
    assert "phone" in (gate.response.get("text") or "").lower()
    from core.session.confirmation_gate import get_confirmation_state

    assert get_confirmation_state(session) == "pending"
    assert get_confirmation_state(gate.plan) == "pending"


def test_confirm_ready_with_session_customer_id():
    coordinator = ExecutionCoordinator()
    gate = _resolve(
        coordinator,
        plan=_confirm_plan(),
        session_state={"customer_id": 55, "confirmation_state": "confirmed"},
    )
    assert gate.path == "ready"
    assert gate.can_execute is True
    assert gate.slots.get("customer_id") == 55
    from core.session.confirmation_gate import get_confirmation_state

    # Successful eligibility must not roll confirmed back.
    assert get_confirmation_state({"confirmation_state": "confirmed"}) == "confirmed"


def test_confirm_ready_with_kwargs_customer_id():
    coordinator = ExecutionCoordinator()
    gate = _resolve(
        coordinator,
        plan=_confirm_plan(),
        kwargs={"customer_id": 77},
    )
    assert gate.path == "ready"
    assert gate.slots.get("customer_id") == 77


def test_identity_block_rolls_merged_luma_confirmed_to_pending():
    coordinator = ExecutionCoordinator()
    merged = {"confirmation_state": "confirmed", "slots": {"service_id": "x"}}
    plan = _confirm_plan()
    plan["_merged_luma_response"] = merged
    session = {"confirmation_state": "confirmed"}
    gate = _resolve(coordinator, plan=plan, session_state=session)
    assert gate.path == "skipped"
    from core.session.confirmation_gate import get_confirmation_state

    assert get_confirmation_state(session) == "pending"
    assert get_confirmation_state(merged) == "pending"
    assert get_confirmation_state(gate.plan) == "pending"
    assert gate.response.get("_merged_luma_response") is merged
    assert get_confirmation_state(gate.response["_merged_luma_response"]) == "pending"


def test_identity_block_then_resolved_customer_allows_fresh_confirm():
    """After identity block leaves pending, a later turn with customer_id can execute."""
    coordinator = ExecutionCoordinator()
    session = {
        "confirmation_state": "confirmed",
        "slots": {"service_id": "premium haircut", "date": "2026-08-04", "time": "09:00"},
    }
    blocked = _resolve(coordinator, plan=_confirm_plan(), session_state=session)
    assert blocked.path == "skipped"
    from core.session.confirmation_gate import get_confirmation_state

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
