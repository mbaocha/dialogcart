"""Unit tests for execution adapters and registry."""

from __future__ import annotations

from types import MappingProxyType
from unittest.mock import MagicMock, patch

import pytest

from core.execution.adapters import get_execution_adapter
from core.execution.adapters.availability_adapter import AvailabilityAdapter
from core.execution.adapters.base import PreparedExecution
from core.execution.adapters.booking_adapter import BookingAdapter
from core.execution.command import ExecutionCommand
from core.execution.command_builder import build_execution_command
from core.engine.execution_coordinator import ExecutionCoordinator


def _confirm_command(**slot_overrides):
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
    return ExecutionCommand(
        action="CONFIRM_APPOINTMENT",
        client_name="booking_client",
        intent_name="CREATE_APPOINTMENT",
        mode="committing",
        slots=slots,
        organization_id=2,
        stage="CONFIRM",
    )


def _search_command(**slot_overrides):
    slots = {
        "organization_id": 2,
        "service_id": 26,
        "date": "2026-08-04",
    }
    slots.update(slot_overrides)
    return ExecutionCommand(
        action="SEARCH_AVAILABILITY",
        client_name="availability_client",
        intent_name="CREATE_APPOINTMENT",
        mode="exploratory",
        slots=slots,
        organization_id=2,
        execution_proposal_context={"source": "test"},
    )


def test_registry_maps_search_to_availability():
    assert isinstance(get_execution_adapter("SEARCH_AVAILABILITY"), AvailabilityAdapter)


@pytest.mark.parametrize(
    "action",
    [
        "CONFIRM_APPOINTMENT",
        "CREATE_BOOKING_HOLD",
        "FETCH_BOOKING",
        "APPLY_MODIFICATION",
        "CONFIRM_CANCELLATION",
        "FINALIZE_RESERVATION",
    ],
)
def test_registry_maps_booking_family(action):
    assert isinstance(get_execution_adapter(action), BookingAdapter)


def test_registry_unknown_action_returns_none():
    assert get_execution_adapter("NOT_AN_ACTION") is None


def test_booking_adapter_injects_customer_and_org():
    cmd = _confirm_command(organization_id=999)
    prepared = BookingAdapter().prepare(
        cmd,
        {"customer_id": 55},
        2,
        organization_client=None,
        kwargs={},
    )
    assert prepared.blocked is None
    assert prepared.slots["organization_id"] == 2
    assert prepared.slots["customer_id"] == 55
    assert prepared.stage == "CONFIRM"
    assert isinstance(prepared.slots, MappingProxyType)


def test_booking_adapter_customer_block():
    prepared = BookingAdapter().prepare(
        _confirm_command(),
        {},
        2,
        kwargs={},
    )
    assert prepared.blocked is not None
    assert prepared.blocked.reason == "CUSTOMER_ID_REQUIRED"
    assert prepared.blocked.required_input == "phone_or_email"


def test_booking_adapter_bound_datetime_from_planning():
    cmd = _confirm_command()
    slots = dict(cmd.slots)
    del slots["datetime_range"]
    cmd = ExecutionCommand(
        action=cmd.action,
        client_name=cmd.client_name,
        intent_name=cmd.intent_name,
        mode=cmd.mode,
        slots=slots,
        organization_id=2,
    )
    bound = {
        "start": "2026-08-04T11:00:00+00:00",
        "end": "2026-08-04T12:00:00+00:00",
    }
    prepared = BookingAdapter().prepare(
        cmd,
        {"customer_id": 1, "planning": {"bound_datetime": bound}},
        2,
        kwargs={},
    )
    assert prepared.blocked is None
    assert prepared.slots["datetime_range"] == bound


def test_booking_adapter_catalog_mapping():
    with patch(
        "core.execution.catalog_resolver.load_sku_to_catalog_id_for_org",
        return_value={"sku-a": 10},
    ):
        prepared = BookingAdapter().prepare(
            _confirm_command(),
            {"customer_id": 1},
            2,
            organization_client=MagicMock(),
            kwargs={},
        )
    assert dict(prepared.sku_to_catalog_id or {}) == {"sku-a": 10}


def test_booking_adapter_finalize_facts():
    org_client = MagicMock()
    org_client.get_details.return_value = {"organization": {"name": "Salon"}}
    cmd = ExecutionCommand(
        action="FINALIZE_RESERVATION",
        client_name="booking_client",
        intent_name="CREATE_RESERVATION",
        mode="exploratory",
        slots={"organization_id": 2, "booking_id": "B1", "booking_code": "C1"},
        organization_id=2,
    )
    prepared = BookingAdapter().prepare(
        cmd,
        {"facts": {"currency": "USD"}},
        2,
        organization_client=org_client,
        kwargs={},
    )
    assert prepared.facts is not None
    assert prepared.facts["currency"] == "USD"
    assert prepared.facts["org"]["name"] == "Salon"


def test_availability_adapter_prepares_search_slots():
    with patch(
        "core.planning.temporal_proposal.resolve_execution_proposals",
        return_value={
            "date_proposal": {"date": "2026-08-05"},
            "time_proposal": None,
        },
    ), patch(
        "core.planning.temporal_proposal.slots_for_availability_search",
        side_effect=lambda slots, d, t: {**slots, "date": "2026-08-05"},
    ), patch(
        "core.execution.catalog_resolver.load_sku_to_catalog_id_for_org",
        return_value={},
    ):
        prepared = AvailabilityAdapter().prepare(
            _search_command(),
            {},
            2,
            kwargs={},
        )
    assert prepared.blocked is None
    assert prepared.stage == "AVAILABILITY"
    assert prepared.slots["date"] == "2026-08-05"
    assert prepared.slots["organization_id"] == 2
    assert isinstance(prepared, PreparedExecution)


def test_coordinator_command_path_invokes_registered_adapter():
    plan = {
        "status": "READY",
        "intent_name": "CREATE_APPOINTMENT",
        "action": "CONFIRM_APPOINTMENT",
        "slots": {
            "organization_id": 2,
            "service_id": 26,
            "datetime_range": {
                "start": "2026-08-04T09:00:00+00:00",
                "end": "2026-08-04T10:00:00+00:00",
            },
        },
        "missing_slots": [],
    }
    cmd = build_execution_command(plan=plan, organization_id=2)
    adapter = get_execution_adapter("CONFIRM_APPOINTMENT")
    assert adapter is not None
    coordinator = ExecutionCoordinator()
    with patch.object(adapter, "prepare", wraps=adapter.prepare) as prep:
        gate = coordinator.resolve(
            plan=plan,
            session_state={"customer_id": 9},
            session_store=None,
            user_id="u",
            availability_client=None,
            organization_client=None,
            organization_id=2,
            kwargs={"booking_client": MagicMock()},
            command=cmd,
            use_execution_command=True,
        )
        prep.assert_called_once()
    assert gate.path == "ready"
    assert gate.slots.get("customer_id") == 9


def test_coordinator_does_not_mutate_decision_plan_via_adapter():
    from copy import deepcopy

    plan = {
        "status": "READY",
        "intent_name": "CREATE_APPOINTMENT",
        "action": "CONFIRM_APPOINTMENT",
        "slots": {
            "organization_id": 2,
            "service_id": 26,
            "datetime_range": {
                "start": "2026-08-04T09:00:00+00:00",
                "end": "2026-08-04T10:00:00+00:00",
            },
        },
        "missing_slots": [],
    }
    snapshot = deepcopy(plan)
    cmd = build_execution_command(plan=plan, organization_id=2)
    gate = ExecutionCoordinator().resolve(
        plan=plan,
        session_state={"customer_id": 1},
        session_store=None,
        user_id="u",
        availability_client=None,
        organization_client=None,
        organization_id=2,
        kwargs={"booking_client": MagicMock()},
        command=cmd,
        use_execution_command=True,
    )
    assert gate.path == "ready"
    assert plan == snapshot
