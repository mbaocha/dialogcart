"""Regression: turn.understanding must survive planning-only response shaping."""

from core.engine.outcome_builder import (
    build_outcome_from_decision,
    build_planning_only_response,
    build_planning_response_from_plan,
)


def test_build_outcome_from_decision_copies_turn_from_plan():
    outcome = build_outcome_from_decision(
        {
            "intent_name": "CREATE_APPOINTMENT",
            "plan": {
                "status": "READY",
                "stage": "COLLECT",
                "action": None,
                "turn": {"understanding": "UNRECOGNIZED_INPUT"},
            },
            "facts": {"slots": {}, "missing_slots": ["time"]},
        }
    )

    assert outcome["turn"] == {"understanding": "UNRECOGNIZED_INPUT"}
    assert outcome["plan"]["turn"] == {"understanding": "UNRECOGNIZED_INPUT"}


def test_planning_only_response_keeps_top_level_turn_when_decision_lacks_it():
    """Stage 09 stamps plan.turn after the decision snapshot — no_step must not drop it."""
    response = build_planning_only_response(
        {
            "intent_name": "CREATE_APPOINTMENT",
            "status": "READY",
            "turn": {"understanding": "UNRECOGNIZED_INPUT"},
            "slots": {"service_id": "premium-haircut", "date": "2026-07-20"},
            "missing_slots": ["time"],
            "_decision": {
                "intent_name": "CREATE_APPOINTMENT",
                "plan": {
                    "status": "READY",
                    "stage": "COLLECT",
                    "action": None,
                },
                "facts": {
                    "slots": {"service_id": "premium-haircut", "date": "2026-07-20"},
                    "missing_slots": ["time"],
                },
            },
        }
    )

    assert response["outcome"]["turn"] == {"understanding": "UNRECOGNIZED_INPUT"}
    assert response["result"]["turn"] == {"understanding": "UNRECOGNIZED_INPUT"}
    assert response["outcome"]["plan"]["turn"] == {
        "understanding": "UNRECOGNIZED_INPUT"
    }


def test_planning_response_from_plan_keeps_turn_without_decision():
    response = build_planning_response_from_plan(
        {
            "intent_name": "CREATE_APPOINTMENT",
            "status": "NEEDS_CLARIFICATION",
            "turn": {"understanding": "UNDERSTOOD"},
            "slots": {},
            "missing_slots": ["service_id"],
            "plan": {"status": "NEEDS_CLARIFICATION", "stage": "COLLECT", "action": None},
        }
    )

    assert response["outcome"]["turn"] == {"understanding": "UNDERSTOOD"}
