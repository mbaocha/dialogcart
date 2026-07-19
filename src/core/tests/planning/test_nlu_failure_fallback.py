from core.engine.outcome_builder import build_planning_response_from_plan
from core.planning.nlu_failure_fallback import build_nlu_failure_fallback
from core.planning.planning_service import plan_message


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
    assert result["outcome"]["recovered"] is True
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
            "recovery_reason": "contract_violation",
            "message_applied": False,
        }
    )

    assert response["outcome"]["recovered"] is True
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
                "recovery_reason": "empty_response",
                "message_applied": False,
            },
        }

    monkeypatch.setattr(
        "core.planning.planner.turn_planner.plan_turn",
        fallback_plan_turn,
    )

    plan = plan_message(text="yes", user_id="user-1")

    assert plan["recovered"] is True
    assert plan["recovery_reason"] == "empty_response"
    assert plan["message_applied"] is False
