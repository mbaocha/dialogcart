from __future__ import annotations

from core.tracing.spine import TURN_OUTCOME_ERROR, resolve_turn_outcome
def test_resolve_turn_outcome_preserves_plan_intent_on_error():
    winner, kind, _reason = resolve_turn_outcome(
        outcome=None,
        success=False,
        result={
            "plan": {
                "intent_name": "CANCEL_BOOKING",
                "action": "FETCH_BOOKING",
                "stage": "FETCH",
            }
        },
    )
    assert kind == TURN_OUTCOME_ERROR
    assert winner["status"] == "error"
    assert winner["intent"] == "CANCEL_BOOKING"
    assert winner["action"] == "FETCH_BOOKING"
    assert winner["stage"] == "FETCH"


def test_resolve_turn_outcome_empty_intent_when_no_plan():
    winner, kind, _reason = resolve_turn_outcome(
        outcome=None,
        success=False,
        result={},
    )
    assert kind == TURN_OUTCOME_ERROR
    assert winner["intent"] == ""
