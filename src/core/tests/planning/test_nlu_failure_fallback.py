import pytest

from core.engine.outcome_builder import build_planning_response_from_plan
from core.planning.nlu_failure_fallback import build_nlu_failure_fallback
from core.planning.planning_service import plan_message
from core.session.persist import assemble_session_projection_fields


def test_durable_session_fallback_marks_message_unapplied():
    result = build_nlu_failure_fallback(
        {
            "intent_name": "CREATE_APPOINTMENT",
            "status": "AWAITING_CONFIRMATION",
            "stage": "CONFIRM",
            "slots": {
                "service_id": "premium-haircut",
                "date": "2026-07-16",
                "time": "09:00",
            },
        },
        user_id="user-1",
        error_code="upstream_error",
        error_message="NLU unavailable",
    )

    assert result["success"] is True
    assert result["outcome"]["status"] == "AWAITING_CONFIRMATION"
    assert result["outcome"]["action"] is None
    assert result["outcome"]["plan"]["action"] is None
    assert result["outcome"]["plan"]["executable_actions"] == []
    assert result["outcome"]["recovered"] is True
    assert result["outcome"]["nlu_failure_recovery"] is True
    assert result["outcome"]["recovery_reason"] == "upstream_error"
    assert result["outcome"]["message_applied"] is False


def test_no_recoverable_session_preserves_error_response():
    result = build_nlu_failure_fallback(
        None,
        user_id="user-1",
        error_code="upstream_error",
        error_message="NLU unavailable",
    )

    assert result == {
        "success": False,
        "error": "upstream_error",
        "message": "NLU unavailable",
    }


def test_fallback_reason_can_be_more_specific_than_client_error():
    result = build_nlu_failure_fallback(
        {
            "intent_name": "CREATE_APPOINTMENT",
            "status": "NEEDS_CLARIFICATION",
            "slots": {},
        },
        user_id="user-1",
        error_code="upstream_error",
        error_message="Luma returned empty response",
        fallback_reason="empty_response",
    )

    assert result["outcome"]["recovery_reason"] == "empty_response"
    assert result["outcome"]["action"] is None


@pytest.mark.parametrize(
    ("error_code", "fallback_reason"),
    [
        ("upstream_error", None),
        ("upstream_error", "empty_response"),
        ("contract_violation", None),
    ],
)
@pytest.mark.parametrize(
    ("stage", "persisted_action"),
    [
        ("CONFIRM", "CONFIRM_APPOINTMENT"),
        ("AVAILABILITY", "SEARCH_AVAILABILITY"),
        ("COLLECT", None),
    ],
)
def test_all_nlu_failure_recoveries_are_non_executable(
    error_code, fallback_reason, stage, persisted_action
):
    session = {
        "intent_name": "CREATE_APPOINTMENT",
        "status": "AWAITING_CONFIRMATION" if stage == "CONFIRM" else "READY",
        "stage": stage,
        "action": persisted_action,
        "slots": {"service_id": 26},
        "missing_slots": [] if stage != "COLLECT" else ["date"],
    }

    result = build_nlu_failure_fallback(
        session,
        user_id="user-1",
        error_code=error_code,
        error_message="NLU unavailable",
        fallback_reason=fallback_reason,
    )

    outcome = result["outcome"]
    assert outcome["message_applied"] is False
    assert outcome["nlu_failure_recovery"] is True
    assert outcome["action"] is None
    assert outcome["plan"]["action"] is None
    assert outcome["plan"]["executable_actions"] == []
    assert session["action"] == persisted_action


def test_unapplied_recovery_preserves_durable_session_exactly():
    previous = {
        "intent_name": "CREATE_APPOINTMENT",
        "status": "AWAITING_CONFIRMATION",
        "stage": "CONFIRM",
        "action": "CONFIRM_APPOINTMENT",
        "slots": {"service_id": 26, "date": "2026-07-16", "time": "09:00"},
        "confirmation_state": {"status": "pending", "proposal_id": "p-1"},
    }
    recovery = build_nlu_failure_fallback(
        previous,
        user_id="user-1",
        error_code="upstream_error",
        error_message="NLU unavailable",
    )["outcome"]

    projected = assemble_session_projection_fields(
        recovery,
        recovery["status"],
        organization_id=1,
        previous_session_state=previous,
    )

    assert projected is previous
    assert projected["action"] == "CONFIRM_APPOINTMENT"
    assert projected["confirmation_state"] == {
        "status": "pending",
        "proposal_id": "p-1",
    }


def test_engine_response_shaping_preserves_fallback_metadata():
    response = build_planning_response_from_plan(
        {
            "intent_name": "CREATE_APPOINTMENT",
            "status": "AWAITING_CONFIRMATION",
            "stage": "CONFIRM",
            "action": "CONFIRM_APPOINTMENT",
            "slots": {},
            "missing_slots": [],
            "recovered": True,
            "nlu_failure_recovery": True,
            "recovery_reason": "contract_violation",
            "message_applied": False,
        }
    )

    assert response["outcome"]["recovered"] is True
    assert response["outcome"]["nlu_failure_recovery"] is True
    assert response["outcome"]["recovery_reason"] == "contract_violation"
    assert response["outcome"]["message_applied"] is False


def test_planning_service_preserves_fallback_metadata(monkeypatch):
    def fallback_plan_turn(**_kwargs):
        return {
            "success": True,
            "outcome": {
                "intent_name": "CREATE_APPOINTMENT",
                "status": "AWAITING_CONFIRMATION",
                "stage": "CONFIRM",
                "action": "CONFIRM_APPOINTMENT",
                "plan": {
                    "status": "AWAITING_CONFIRMATION",
                    "stage": "CONFIRM",
                    "action": "CONFIRM_APPOINTMENT",
                },
                "slots": {},
                "missing_slots": [],
                "recovered": True,
                "nlu_failure_recovery": True,
                "recovery_reason": "empty_response",
                "message_applied": False,
            },
        }

    monkeypatch.setattr(
        "core.planning.planner.turn_planner.plan_turn",
        fallback_plan_turn,
    )

    plan = plan_message(text="yes", user_id="user-1", organization_id=1)

    assert plan["recovered"] is True
    assert plan["nlu_failure_recovery"] is True
    assert plan["recovery_reason"] == "empty_response"
    assert plan["message_applied"] is False
    assert plan["action"] is None
    assert plan["plan"]["action"] is None
    assert plan["plan"]["executable_actions"] == []
