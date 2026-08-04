"""Unit tests for ExecutionCommand construction and coordinator command path."""

from __future__ import annotations

from copy import deepcopy
from types import MappingProxyType
from unittest.mock import MagicMock, patch

import pytest

from core.execution.command import (
    ExecutionBlocked,
    ExecutionCommand,
    ExecutionCommandError,
)
from core.execution.command_builder import build_execution_command
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
        "stage": "CONFIRM",
        "slots": slots,
        "missing_slots": [],
    }


def _availability_plan(**slot_overrides):
    slots = {
        "organization_id": 2,
        "service_id": 26,
        "date": "2026-08-04",
    }
    slots.update(slot_overrides)
    return {
        "status": "READY",
        "intent_name": "CREATE_APPOINTMENT",
        "action": "SEARCH_AVAILABILITY",
        "stage": "AVAILABILITY",
        "slots": slots,
        "missing_slots": [],
        "execution_proposal_context": {"source": "test"},
    }


# ---------------------------------------------------------------------------
# Command construction
# ---------------------------------------------------------------------------


def test_authorized_action_produces_expected_command():
    plan = _confirm_plan()
    cmd = build_execution_command(plan=plan, organization_id=2)
    assert cmd is not None
    assert cmd.action == "CONFIRM_APPOINTMENT"
    assert cmd.client_name == "booking_client"
    assert cmd.intent_name == "CREATE_APPOINTMENT"
    assert cmd.mode == "committing"
    assert cmd.organization_id == 2
    assert cmd.slots["service_id"] == 26
    assert isinstance(cmd.slots, MappingProxyType)


def test_no_action_produces_no_command():
    plan = {"status": "NEEDS_CLARIFICATION", "intent_name": "CREATE_APPOINTMENT", "slots": {}}
    assert build_execution_command(plan=plan, organization_id=2) is None


def test_unknown_selected_action_fails_closed():
    plan = {
        "status": "READY",
        "intent_name": "CREATE_APPOINTMENT",
        "action": "NOT_A_REAL_ACTION",
        "slots": {},
    }
    with pytest.raises(ExecutionCommandError):
        build_execution_command(plan=plan, organization_id=2)


def test_command_uses_policy_client_override():
    plan = _availability_plan()
    cmd = build_execution_command(
        plan=plan, organization_id=2, policy_client="availability_client"
    )
    assert cmd is not None
    assert cmd.client_name == "availability_client"
    assert cmd.mode == "exploratory"


def test_command_contains_defensive_copies():
    plan = _availability_plan()
    original_slots = plan["slots"]
    cmd = build_execution_command(plan=plan, organization_id=2)
    assert cmd is not None
    assert cmd.slots is not original_slots
    assert dict(cmd.slots) == original_slots
    # Mutating source plan must not affect frozen command.
    plan["slots"]["service_id"] = 999
    assert cmd.slots["service_id"] == 26
    assert cmd.execution_proposal_context is not None
    assert dict(cmd.execution_proposal_context) == {"source": "test"}


# ---------------------------------------------------------------------------
# Coordinator command path
# ---------------------------------------------------------------------------


def _resolve_command(coordinator, *, plan, command, session_state=None, kwargs=None, **clients):
    return coordinator.resolve(
        plan=plan,
        session_state=session_state or {},
        session_store=None,
        user_id="test-user",
        availability_client=clients.get("availability_client"),
        organization_client=clients.get("organization_client"),
        organization_id=2,
        kwargs={"booking_client": MagicMock(), **(kwargs or {})},
        command=command,
    )


def test_command_path_does_not_call_get_execution_steps():
    plan = _confirm_plan()
    cmd = build_execution_command(plan=plan, organization_id=2)
    coordinator = ExecutionCoordinator()
    with patch(
        "core.policy.intent_policy.get_execution_steps"
    ) as mocked_steps:
        gate = _resolve_command(
            coordinator,
            plan=plan,
            command=cmd,
            session_state={"customer_id": 55},
        )
        mocked_steps.assert_not_called()
    assert gate.path == "ready"
    assert gate.action == "CONFIRM_APPOINTMENT"
    assert gate.client_name == "booking_client"


def test_command_path_does_not_recompute_slot_completeness():
    """Authorized command executes even if required slots would fail legacy gate."""
    plan = {
        "status": "READY",
        "intent_name": "CREATE_APPOINTMENT",
        "action": "SEARCH_AVAILABILITY",
        "slots": {},  # missing service_id — Decision already authorized
        "missing_slots": ["service_id"],
    }
    cmd = ExecutionCommand(
        action="SEARCH_AVAILABILITY",
        client_name="availability_client",
        intent_name="CREATE_APPOINTMENT",
        mode="exploratory",
        slots={},
        organization_id=2,
    )
    coordinator = ExecutionCoordinator()
    gate = _resolve_command(
        coordinator,
        plan=plan,
        command=cmd,
        availability_client=MagicMock(),
    )
    assert gate.path == "ready"
    assert gate.can_execute is True


def test_command_path_does_not_mutate_decision_plan():
    plan = _confirm_plan()
    snapshot = deepcopy(plan)
    cmd = build_execution_command(plan=plan, organization_id=2)
    coordinator = ExecutionCoordinator()
    gate = _resolve_command(
        coordinator,
        plan=plan,
        command=cmd,
        session_state={"customer_id": 55},
    )
    assert gate.path == "ready"
    assert plan == snapshot
    assert gate.plan is not plan
    assert gate.plan["slots"]["customer_id"] == 55
    assert "customer_id" not in plan["slots"]


def test_command_path_customer_block_typed_reason():
    plan = _confirm_plan()
    cmd = build_execution_command(plan=plan, organization_id=2)
    coordinator = ExecutionCoordinator()
    gate = _resolve_command(coordinator, plan=plan, command=cmd)
    assert gate.path == "blocked"
    assert isinstance(gate.blocked, ExecutionBlocked)
    assert gate.blocked.reason == "CUSTOMER_ID_REQUIRED"
    assert gate.blocked.required_input == "phone_or_email"
    assert "phone" in (gate.response.get("text") or "").lower()
    assert plan["status"] == "READY"  # Decision plan unchanged


def test_command_path_missing_client_typed_reason():
    plan = _confirm_plan()
    cmd = build_execution_command(plan=plan, organization_id=2)
    coordinator = ExecutionCoordinator()
    gate = coordinator.resolve(
        plan=plan,
        session_state={"customer_id": 1},
        session_store=None,
        user_id="u",
        availability_client=None,
        organization_client=None,
        organization_id=2,
        kwargs={},  # no booking_client
        command=cmd,
    )
    assert gate.path == "missing_client"
    assert gate.blocked is not None
    assert gate.blocked.reason == "MISSING_EXECUTION_CLIENT"


def test_command_path_no_action_skips_without_policy():
    plan = {"status": "NEEDS_CLARIFICATION", "intent_name": "CREATE_APPOINTMENT", "slots": {}}
    coordinator = ExecutionCoordinator()
    with patch(
        "core.policy.intent_policy.get_execution_steps"
    ) as mocked_steps:
        gate = coordinator.resolve(
            plan=plan,
            session_state={},
            session_store=None,
            user_id="u",
            availability_client=None,
            organization_client=None,
            organization_id=2,
            kwargs={},
            command=None,
        )
        mocked_steps.assert_not_called()
    assert gate.path == "skipped"
    assert gate.can_execute is False




def test_confirm_ready_injects_customer_and_org():
    plan = _confirm_plan()
    session = {"customer_id": 55}
    booking_client = MagicMock()
    cmd = build_execution_command(plan=plan, organization_id=2)
    gate = ExecutionCoordinator().resolve(
        plan=plan,
        session_state=session,
        session_store=None,
        user_id="u",
        availability_client=None,
        organization_client=None,
        organization_id=2,
        kwargs={"booking_client": booking_client},
        command=cmd,
    )
    assert gate.path == "ready"
    assert gate.action == "CONFIRM_APPOINTMENT"
    assert gate.client_name == "booking_client"
    assert gate.slots.get("customer_id") == 55
    assert gate.slots.get("organization_id") == 2
    assert gate.slots.get("datetime_range") == plan["slots"]["datetime_range"]


def test_availability_search_ready_with_catalog_mapping():
    plan = _availability_plan()
    availability_client = MagicMock()
    cmd = build_execution_command(plan=plan, organization_id=2)
    with patch(
        "core.execution.catalog_resolver.load_sku_to_catalog_id_for_org",
        return_value={"sku": 1},
    ):
        gate = ExecutionCoordinator().resolve(
            plan=plan,
            session_state={},
            session_store=None,
            user_id="u",
            availability_client=availability_client,
            organization_client=None,
            organization_id=2,
            kwargs={"booking_client": MagicMock()},
            command=cmd,
        )
    assert gate.path == "ready"
    assert gate.action == "SEARCH_AVAILABILITY"
    assert gate.client_name == "availability_client"
    assert gate.slots.get("organization_id") == 2
    assert gate.plan.get("sku_to_catalog_id") == {"sku": 1}


def test_conflicting_organization_id_enforced_on_command_path():
    plan = _confirm_plan(organization_id=999)
    cmd = build_execution_command(plan=plan, organization_id=2)
    coordinator = ExecutionCoordinator()
    gate = _resolve_command(
        coordinator,
        plan=plan,
        command=cmd,
        session_state={"customer_id": 1},
    )
    assert gate.path == "ready"
    assert gate.slots["organization_id"] == 2


def test_bound_datetime_from_session_v2_planning():
    plan = _confirm_plan()
    del plan["slots"]["datetime_range"]
    cmd = build_execution_command(plan=plan, organization_id=2)
    bound = {
        "start": "2026-08-04T11:00:00+00:00",
        "end": "2026-08-04T12:00:00+00:00",
    }
    coordinator = ExecutionCoordinator()
    gate = _resolve_command(
        coordinator,
        plan=plan,
        command=cmd,
        session_state={
            "customer_id": 1,
            "planning": {"bound_datetime": bound},
        },
    )
    assert gate.path == "ready"
    assert gate.slots["datetime_range"] == bound


@pytest.mark.parametrize(
    "intent,action,client,mode",
    [
        ("CREATE_APPOINTMENT", "SEARCH_AVAILABILITY", "availability_client", "exploratory"),
        ("CREATE_APPOINTMENT", "CONFIRM_APPOINTMENT", "booking_client", "committing"),
        ("VIEW_BOOKING", "FETCH_BOOKING", "booking_client", "exploratory"),
        ("MODIFY_BOOKING", "APPLY_MODIFICATION", "booking_client", "committing"),
        ("MODIFY_BOOKING", "SEARCH_AVAILABILITY", "availability_client", "exploratory"),
        ("CANCEL_BOOKING", "CONFIRM_CANCELLATION", "booking_client", "committing"),
        ("CANCEL_BOOKING", "FETCH_BOOKING", "booking_client", "exploratory"),
        ("CREATE_RESERVATION", "CREATE_BOOKING_HOLD", "booking_client", "committing"),
        ("CREATE_RESERVATION", "FINALIZE_RESERVATION", "booking_client", "exploratory"),
    ],
)
def test_command_construction_for_policy_actions(intent, action, client, mode):
    plan = {
        "status": "READY",
        "intent_name": intent,
        "action": action,
        "slots": {"organization_id": 2, "service_id": 1, "booking_id": "B1"},
    }
    cmd = build_execution_command(plan=plan, organization_id=2)
    assert cmd is not None
    assert cmd.action == action
    assert cmd.client_name == client
    assert cmd.mode == mode
    assert cmd.intent_name == intent


def test_identity_block_leaves_confirmation_pending():
    from core.session.confirmation_gate import get_confirmation_state

    plan = _confirm_plan()
    session = {"confirmation_state": "pending"}
    cmd = build_execution_command(plan=plan, organization_id=2)
    coordinator = ExecutionCoordinator()
    gate = _resolve_command(coordinator, plan=plan, command=cmd, session_state=session)
    assert gate.path == "blocked"
    assert get_confirmation_state(session) == "pending"
    assert get_confirmation_state(gate.plan) != "confirmed"
