"""Shared assertions for confirmation-interruption E2E scenarios."""

from __future__ import annotations

from typing import Any, Dict, Optional

from core.rendering.booking_confirmation_renderer import render_booking_confirmation_prompt
from core.tests.e2e.framework.conversation import (
    BookingConversation,
    _presentation_page_index,
    extract_presented_times,
)
from core.tests.e2e.framework.fixtures import TARGET_DATE


def _planning_section(session: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(session, dict):
        return {}
    planning = session.get("planning")
    return planning if isinstance(planning, dict) else {}


def _planning_slots(session: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    planning = _planning_section(session)
    if planning.get("slots"):
        return dict(planning.get("slots") or {})
    if isinstance(session, dict):
        return dict(session.get("slots") or {})
    return {}


def _bound_datetime(session: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    planning = _planning_section(session)
    bound = planning.get("bound_datetime")
    if isinstance(bound, dict) and bound.get("start"):
        return bound
    if isinstance(session, dict):
        legacy = session.get("resolved_datetime_range")
        if isinstance(legacy, dict) and legacy.get("start"):
            return legacy
    return None


def _availability_section(session: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(session, dict):
        return {}
    availability = session.get("availability")
    return availability if isinstance(availability, dict) else {}


def _fingerprint(session: Optional[Dict[str, Any]]) -> Any:
    availability = _availability_section(session)
    if availability.get("fingerprint") is not None:
        return availability.get("fingerprint")
    if isinstance(session, dict):
        return session.get("availability_fingerprint")
    return None


def _cached_search_result(session: Optional[Dict[str, Any]]) -> Any:
    availability = _availability_section(session)
    cache = availability.get("cache") if isinstance(availability.get("cache"), dict) else {}
    if cache.get("search_result") is not None:
        return cache.get("search_result")
    if isinstance(session, dict):
        return session.get("last_execution_result")
    return None


def gate_action_from_trace(body: Dict[str, Any]) -> Optional[str]:
    """Read matched confirmation gate action from a traced API response."""
    trace = body.get("decision_trace")
    if not isinstance(trace, dict):
        return None

    # Forensic DAG: decision.confirmation.classify_turn winner / matched candidates.
    records = trace.get("records")
    if isinstance(records, list):
        for record in records:
            if not isinstance(record, dict):
                continue
            if record.get("id") != "decision.confirmation.classify_turn":
                continue
            winner = record.get("winner")
            if winner in {"ANOTHER_REQUEST", "YES", "NO"}:
                return str(winner)
            candidates = record.get("candidates")
            if isinstance(candidates, list):
                for candidate in candidates:
                    if not isinstance(candidate, dict):
                        continue
                    if candidate.get("matched") and candidate.get("id") in {
                        "ANOTHER_REQUEST",
                        "YES",
                        "NO",
                    }:
                        return str(candidate.get("id"))

    stages = trace.get("stages")
    if isinstance(stages, list):
        for stage in stages:
            if not isinstance(stage, dict):
                continue
            if stage.get("stage") not in ("confirmation", "intent", "planning"):
                continue
            candidates = stage.get("candidates")
            if not isinstance(candidates, list):
                continue
            for candidate in candidates:
                if not isinstance(candidate, dict):
                    continue
                if candidate.get("matched") and candidate.get("id") in {
                    "ANOTHER_REQUEST",
                    "YES",
                    "NO",
                }:
                    return str(candidate.get("id"))

    evidence = trace.get("evidence")
    if isinstance(evidence, list):
        for item in evidence:
            if not isinstance(item, dict):
                continue
            facts = item.get("facts")
            if isinstance(facts, dict) and facts.get("gate_action"):
                return str(facts.get("gate_action"))
    # Forensic evidence records may also carry gate_action.
    if isinstance(records, list):
        for record in records:
            if not isinstance(record, dict):
                continue
            facts = record.get("facts")
            if isinstance(facts, dict) and facts.get("gate_action"):
                return str(facts.get("gate_action"))
    return None


def turn_operation_from_response(conv: BookingConversation) -> Optional[str]:
    plan = conv.plan
    if plan.get("turn_operation"):
        return str(plan.get("turn_operation"))
    body_plan = conv.last_body.get("plan")
    if isinstance(body_plan, dict) and body_plan.get("turn_operation"):
        return str(body_plan.get("turn_operation"))
    nested = conv.outcome.get("plan")
    if isinstance(nested, dict) and nested.get("turn_operation"):
        return str(nested.get("turn_operation"))
    # Forensic decision plan may carry the Stage 2 operation.
    trace = conv.last_body.get("decision_trace")
    if isinstance(trace, dict):
        records = trace.get("records")
        if isinstance(records, list):
            for record in records:
                if not isinstance(record, dict):
                    continue
                facts = record.get("facts")
                if isinstance(facts, dict) and facts.get("turn_operation"):
                    return str(facts.get("turn_operation"))
                inputs = record.get("inputs_evaluated")
                if isinstance(inputs, dict) and inputs.get("turn_operation"):
                    return str(inputs.get("turn_operation"))
    return None


def capture_pre_interruption_state(conv: BookingConversation) -> Dict[str, Any]:
    sess = conv.session() or {}
    return {
        "search_count": None,
        "fingerprint": _fingerprint(sess),
        "page_index": _presentation_page_index(sess),
        "presented_times": extract_presented_times(conv.last_body, sess),
        "cached_slots": len((_cached_search_result(sess) or {}).get("slots") or []),
    }


def attach_search_count(state: Dict[str, Any], availability_client) -> None:
    if availability_client is not None:
        state["search_count"] = availability_client.get_service_availability.call_count


def assert_gate_action(conv: BookingConversation, expected: str) -> None:
    actual = gate_action_from_trace(conv.last_body)
    conv._assert(
        actual == expected,
        (
            f"turn {conv.turn}: gate_action expected {expected!r}, got {actual!r} "
            f"(enable trace=1 on this turn)"
        ),
    )


def assert_turn_operation(conv: BookingConversation, expected: str) -> None:
    actual = turn_operation_from_response(conv)
    conv._assert(
        actual == expected,
        f"turn {conv.turn}: turn_operation expected {expected!r}, got {actual!r}",
    )


def assert_planning_intent_preserved(conv: BookingConversation) -> None:
    conv.assert_intent("CREATE_APPOINTMENT")


def assert_cleared_confirmation_binding(conv: BookingConversation) -> None:
    sess = conv.session() or {}
    conv.assert_confirmation(None)
    conv._assert(
        _bound_datetime(sess) is None,
        (
            f"turn {conv.turn}: expected planning.bound_datetime cleared, "
            f"got {_bound_datetime(sess)!r}"
        ),
    )
    slots = _planning_slots(sess)
    conv._assert(
        slots.get("time") in (None, ""),
        f"turn {conv.turn}: expected planning.slots.time cleared, got {slots.get('time')!r}",
    )


def assert_service_preserved(conv: BookingConversation, service_id: str) -> None:
    slots = _planning_slots(conv.session())
    conv._assert(
        slots.get("service_id") == service_id,
        (
            f"turn {conv.turn}: expected service_id {service_id!r}, "
            f"got {slots.get('service_id')!r}"
        ),
    )


def assert_availability_cache_preserved(
    conv: BookingConversation,
    before: Dict[str, Any],
) -> None:
    sess = conv.session() or {}
    conv._assert(
        _fingerprint(sess) == before.get("fingerprint"),
        (
            f"turn {conv.turn}: expected availability fingerprint preserved "
            f"({before.get('fingerprint')!r} -> {_fingerprint(sess)!r})"
        ),
    )
    conv._assert(
        _presentation_page_index(sess) == before.get("page_index"),
        (
            f"turn {conv.turn}: expected presentation page_index preserved "
            f"({before.get('page_index')} -> {_presentation_page_index(sess)})"
        ),
    )
    after_times = extract_presented_times(conv.last_body, sess)
    conv._assert(
        after_times == before.get("presented_times"),
        (
            f"turn {conv.turn}: expected current availability page re-rendered "
            f"({before.get('presented_times')!r} -> {after_times!r})"
        ),
    )
    conv._assert(
        len((_cached_search_result(sess) or {}).get("slots") or [])
        == before.get("cached_slots", 0),
        f"turn {conv.turn}: expected cached search_result slot count preserved",
    )


def assert_no_search_since(
    conv: BookingConversation,
    availability_client,
    baseline: int,
) -> None:
    conv._assert(
        availability_client.get_service_availability.call_count == baseline,
        (
            f"turn {conv.turn}: expected zero availability searches since baseline "
            f"{baseline}, got {availability_client.get_service_availability.call_count}"
        ),
    )


def assert_exactly_one_search_since(
    conv: BookingConversation,
    availability_client,
    baseline: int,
) -> None:
    actual = availability_client.get_service_availability.call_count
    conv._assert(
        actual == baseline + 1,
        (
            f"turn {conv.turn}: expected exactly one SEARCH_AVAILABILITY since "
            f"baseline {baseline}, got {actual - baseline}"
        ),
    )


def assert_not_confirmation_rendered(conv: BookingConversation) -> None:
    conv._assert(
        conv.outcome.get("status") != "AWAITING_CONFIRMATION",
        (
            f"turn {conv.turn}: expected status != AWAITING_CONFIRMATION after "
            f"ANOTHER_REQUEST, got {conv.outcome.get('status')!r}"
        ),
    )
    text = str(conv.last_body.get("text") or "")
    confirm_probe = render_booking_confirmation_prompt(
        {"service_id": "probe", "date": TARGET_DATE, "time": "10:00"}
    )
    conv._assert(
        "Would you like me to go ahead" not in text,
        f"turn {conv.turn}: confirmation prompt must not be rendered, got {text!r}",
    )


def assert_availability_rendered(conv: BookingConversation) -> None:
    conv.assert_response_text_present()
    text = str(conv.last_body.get("text") or "").lower()
    conv._assert(
        any(token in text for token in ("available", "availability", "am", "pm", ":")),
        f"turn {conv.turn}: expected availability rendering, got {text!r}",
    )


def assert_returns_to_pending_confirmation(conv: BookingConversation) -> None:
    conv.assert_turn(
        response_status="AWAITING_CONFIRMATION",
        planner_status="AWAITING_CONFIRMATION",
        stage="CONFIRM",
        awaiting="USER_CONFIRMATION",
        action=None,
        confirmation="pending",
        intent="CREATE_APPOINTMENT",
    )
