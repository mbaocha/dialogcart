from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from core.engine.execution_coordinator import ExecutionCoordinator
from core.execution.command_builder import build_execution_command
from core.execution.dispatcher import execute


def _plan(service_id, sku_to_catalog_id=None):
    return {
        "status": "READY",
        "action": "CONFIRM_APPOINTMENT",
        "intent_name": "CREATE_APPOINTMENT",
        "stage": "CONFIRM",
        "slots": {
            "organization_id": 2,
            "customer_id": 55,
            "service_id": service_id,
            "datetime_range": {
                "start": "2026-08-04T09:00:00+00:00",
                "end": "2026-08-04T10:00:00+00:00",
            },
        },
        "sku_to_catalog_id": sku_to_catalog_id or {},
        "missing_slots": [],
    }


def _successful_client():
    client = MagicMock()
    client.create_booking.return_value = {
        "booking": {"id": 42, "booking_code": "BK-42"}
    }
    return client


@pytest.mark.parametrize(
    ("service_id", "mapping", "expected_item_id"),
    [
        ("premium haircut", {"premium haircut": 1001}, 1001),
        ("flexi haircut + prunning", {"flexi haircut + prunning": 1002}, 1002),
        (26, {}, 26),
        ("premium haircut", {"premium haircut": 1}, 1),
    ],
)
def test_confirm_appointment_uses_only_resolved_or_numeric_catalog_item(
    service_id, mapping, expected_item_id
):
    client = _successful_client()

    result = execute(_plan(service_id, mapping), booking_client=client)

    assert result["status"] == "succeeded"
    assert client.create_booking.call_args.kwargs["item_id"] == expected_item_id


@pytest.mark.parametrize("mapping", [{}, {"another service": 7}])
def test_unmapped_nonnumeric_service_never_calls_create_booking(mapping):
    client = _successful_client()

    with pytest.raises(ValueError, match="does not resolve"):
        execute(_plan("premium haircut", mapping), booking_client=client)

    client.create_booking.assert_not_called()


def test_failed_catalog_load_returns_existing_execution_failure_representation():
    plan = _plan("premium haircut")
    command = build_execution_command(plan=plan, organization_id=2)
    client = _successful_client()
    coordinator = ExecutionCoordinator()

    with patch(
        "core.execution.catalog_resolver.load_sku_to_catalog_id_for_org",
        side_effect=RuntimeError("catalog unavailable"),
    ):
        gate = coordinator.resolve(
            plan=plan,
            session_state={"customer_id": 55},
            session_store=None,
            user_id="test-user",
            availability_client=None,
            organization_client=MagicMock(),
            organization_id=2,
            kwargs={"booking_client": client},
            command=command,
        )

    assert gate.path == "ready"
    assert gate.plan["sku_to_catalog_id"] == {}

    router = MagicMock()
    router.get_route.return_value = "booking"
    result = coordinator.run(
        gate,
        session_store=None,
        user_id="test-user",
        organization_id=2,
        workflow_router=router,
        booking_workflow=MagicMock(),
        availability_workflow=MagicMock(),
        kwargs={},
    )

    assert result.path == "failed"
    assert result.response["success"] is False
    assert result.response["error"] == "execution_failed"
    assert "does not resolve" in result.response["message"]
    client.create_booking.assert_not_called()
