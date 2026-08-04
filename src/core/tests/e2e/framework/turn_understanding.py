"""E2E helpers for turn.understanding regressions (utterance vs session stickiness)."""

from __future__ import annotations

from typing import Any, Optional

from core.tests.e2e.framework.conversation import BookingConversation


def nlu_understanding(luma_client: Any) -> Optional[str]:
    """Read ``turn.understanding`` from the last recorded ``/resolve`` response."""
    response = getattr(luma_client, "last_response", None)
    if not isinstance(response, dict):
        return None
    turn = response.get("turn")
    if isinstance(turn, dict):
        value = turn.get("understanding")
        if isinstance(value, str) and value:
            return value
    return None


def outcome_understanding(conv: BookingConversation) -> Optional[str]:
    for source in (conv.outcome, conv.plan, conv.last_body):
        if not isinstance(source, dict):
            continue
        turn = source.get("turn")
        if isinstance(turn, dict):
            value = turn.get("understanding")
            if isinstance(value, str) and value:
                return value
        nested = source.get("plan")
        if isinstance(nested, dict):
            turn = nested.get("turn")
            if isinstance(turn, dict):
                value = turn.get("understanding")
                if isinstance(value, str) and value:
                    return value
        result = source.get("result")
        if isinstance(result, dict):
            turn = result.get("turn")
            if isinstance(turn, dict):
                value = turn.get("understanding")
                if isinstance(value, str) and value:
                    return value
    # decision_trace may still carry planner plan.turn when HTTP outcome rebuilt.
    trace = conv.last_body.get("decision_trace") if isinstance(conv.last_body, dict) else None
    if isinstance(trace, dict):
        for key in ("plan", "outcome", "result"):
            section = trace.get(key)
            if not isinstance(section, dict):
                continue
            turn = section.get("turn")
            if isinstance(turn, dict):
                value = turn.get("understanding")
                if isinstance(value, str) and value:
                    return value
    return None


def assert_understanding_everywhere(
    conv: BookingConversation,
    luma_client: Any,
    expected: str,
    *,
    require_result: bool = True,
) -> None:
    """Assert recorded NLU payload and (when required) planner understanding agree."""
    nlu_value = nlu_understanding(luma_client)
    conv._assert(
        nlu_value == expected,
        (
            f"turn {conv.turn}: NLU turn.understanding expected {expected!r}, "
            f"got {nlu_value!r} from {getattr(luma_client, 'last_response', None)!r}"
        ),
    )
    outcome_value = outcome_understanding(conv)
    if outcome_value is None and not require_result:
        return
    conv._assert(
        outcome_value == expected,
        (
            f"turn {conv.turn}: RESULT/outcome turn.understanding expected "
            f"{expected!r}, got {outcome_value!r}"
        ),
    )


def session_fingerprint(session: Optional[dict]) -> Any:
    from core.workflows.availability.presentation import (
        availability_fingerprint_from_session,
    )

    return availability_fingerprint_from_session(session)


def session_service_id(session: Optional[dict]) -> Optional[str]:
    if not isinstance(session, dict):
        return None
    slots = session.get("slots")
    if isinstance(slots, dict) and slots.get("service_id"):
        return str(slots.get("service_id"))
    return None
