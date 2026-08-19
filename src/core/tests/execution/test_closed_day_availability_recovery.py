from unittest.mock import Mock

from core.adapters.errors import AvailabilityRejectedError
from core.engine.execution_coordinator import ExecutionCoordinator, ExecutionGateResult
from core.rendering.response_renderer import ResponseRenderer
from core.session.turn_persistence import project_and_persist_turn_result
from core.workflows.availability.workflow import AvailabilityWorkflow
from core.workflows.booking.workflow import BookingWorkflow
from core.workflows.router import WorkflowRouter


def test_closed_day_is_recoverable_and_preserves_current_turn_engine_type(monkeypatch):
    plan = {
        "status": "READY",
        "intent_name": "CREATE_APPOINTMENT",
        "action": "SEARCH_AVAILABILITY",
        "slots": {
            "organization_id": 1,
            "service_id": "executive-oil-change",
            "date": "2026-08-08",
            "engine_type": "petrol",
        },
        "missing_slots": ["time", "registration_number"],
        "facts": {"slots": {"engine_type": "petrol"}},
    }
    closed = AvailabilityRejectedError(reason="business_closed")
    availability_client = Mock()
    availability_client.get_service_availability.side_effect = closed
    gate = ExecutionGateResult(
        path="ready",
        plan=plan,
        plan_status="READY",
        plan_action="SEARCH_AVAILABILITY",
        can_execute=True,
        action="SEARCH_AVAILABILITY",
        client_name="availability_client",
        execution_client=availability_client,
        intent_name="CREATE_APPOINTMENT",
        slots=dict(plan["slots"]),
        session_state={},
    )

    run = ExecutionCoordinator().run(
        gate,
        session_store=None,
        user_id="closed-day-user",
        organization_id=1,
        workflow_router=WorkflowRouter(),
        booking_workflow=BookingWorkflow(),
        availability_workflow=AvailabilityWorkflow(),
        kwargs={},
    )

    assert run.path == "executed"
    assert run.execution_result["status"] == "succeeded"
    assert run.execution_result["availability"]["slots"] == []
    assert (
        run.execution_result["availability"]["unavailable_reason"]
        == "business_closed"
    )

    monkeypatch.setattr(
        "core.rendering.response_renderer.render_llm",
        lambda request: "We're closed that day. Please choose another date.",
    )
    result = run.response
    ResponseRenderer().render_execution(
        result,
        run.plan,
        run.execution_result,
        session_state={},
    )
    projected = project_and_persist_turn_result(
        result=result,
        organization_id=1,
        user_id="closed-day-user",
        previous_session_state=None,
        working_session_state=run.session_state,
        save=False,
    )

    assert result["success"] is True
    assert "another date" in result["text"].lower()
    assert projected["planning"]["slots"]["engine_type"] == "petrol"
    assert "unavailable_reason" not in projected["planning"]["slots"]
