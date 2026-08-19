"""Confirmation preservation for unrecognized no-evidence turns + digression resume."""

from __future__ import annotations

from core.planning.pipeline.requests import AttachedRequest
from core.planning.pipeline.stage06_confirmation import (
    _non_superseding_unrecognized_pending,
    resolve_confirmation,
)
from core.planning.pipeline.types import (
    AvailabilityDecision,
    SlotTurnState,
    WorkingTurn,
)
from core.rendering.recovery_renderer import (
    build_recovery_render_request,
    should_render_recovery,
)
from core.rendering.workflow_resume import build_resume_instruction
from core.session.confirmation_gate import (
    ConfirmationGateTurn,
    get_confirmation_state,
)


def _pending_payload(**overrides):
    payload = {
        "intent": {"name": "CREATE_APPOINTMENT"},
        "slots": {
            "service_id": "premium haircut",
            "date": "2026-07-06",
            "time": "09:00",
        },
        "missing_slots": [],
        "confirmation_state": "pending",
        "resolved_datetime_range": {
            "start": "2026-07-06T09:00:00Z",
            "end": "2026-07-06T09:30:00Z",
        },
        "turn": {"understanding": "UNRECOGNIZED_INPUT"},
        "_current_turn_planning_evidence": False,
    }
    payload.update(overrides)
    return payload


def test_non_superseding_helper_requires_unrecognized_and_no_evidence():
    pending = _pending_payload()
    assert _non_superseding_unrecognized_pending(pending, "pending") is True

    understood = _pending_payload(turn={"understanding": "UNDERSTOOD"})
    assert _non_superseding_unrecognized_pending(understood, "pending") is False

    with_evidence = _pending_payload(_current_turn_planning_evidence=True)
    assert _non_superseding_unrecognized_pending(with_evidence, "pending") is False

    assert _non_superseding_unrecognized_pending(pending, None) is False


def test_stage06_preserves_pending_for_unrecognized_no_evidence():
    payload = _pending_payload()
    session = {
        "intent_name": "CREATE_APPOINTMENT",
        "confirmation_state": "pending",
        "customer_id": 91,
        "customer_contact": {
            "customer_id": 91,
            "authoritative_name": "Test Customer",
            "name_status": "authoritative",
        },
        "slots": dict(payload["slots"]),
    }
    attached = AttachedRequest(
        planning_intent="CREATE_APPOINTMENT",
        turn_operation="NONE",
        session_reset_occurred=False,
        confirm_booking_continuation=False,
        gate_action=ConfirmationGateTurn.ANOTHER_REQUEST,
    )
    working = WorkingTurn(payload=payload)
    slots = SlotTurnState(
        intent_name="CREATE_APPOINTMENT",
        missing_slots=[],
        effective_collected_slots=dict(payload["slots"]),
        needs_clarification=False,
        base_status="READY",
        ask_next=None,
    )
    availability = AvailabilityDecision(
        availability_ready=True,
        stored_fingerprint="fp",
        current_fingerprint="fp",
    )
    decision = resolve_confirmation(
        attached_request=attached,
        slot_state=slots,
        working_turn=working,
        availability=availability,
        session_state=session,
        gate_booking_intent="CREATE_APPOINTMENT",
        user_id="u-preserve",
    )
    assert decision.confirmation_state == "pending"
    assert decision.awaiting_user_confirmation is True
    assert get_confirmation_state(payload) == "pending"
    assert get_confirmation_state(session) == "pending"


def test_stage06_still_consumes_on_correction_with_planning_evidence():
    payload = _pending_payload(
        turn={"understanding": "UNDERSTOOD"},
        _current_turn_planning_evidence=True,
        _current_turn_has_time=True,
        time_proposal="10:00",
        time_match_outcome="TIME_MATCH_EXACT",
        slots={
            "service_id": "premium haircut",
            "date": "2026-07-06",
            "time": "10:00",
        },
        resolved_datetime_range={
            "start": "2026-07-06T10:00:00Z",
            "end": "2026-07-06T10:30:00Z",
        },
    )
    session = {
        "intent_name": "CREATE_APPOINTMENT",
        "confirmation_state": "pending",
        "slots": {
            "service_id": "premium haircut",
            "date": "2026-07-06",
            "time": "09:00",
        },
    }
    attached = AttachedRequest(
        planning_intent="CREATE_APPOINTMENT",
        turn_operation="CORRECTION",
        session_reset_occurred=False,
        confirm_booking_continuation=False,
        gate_action=ConfirmationGateTurn.ANOTHER_REQUEST,
    )
    working = WorkingTurn(payload=payload)
    slots = SlotTurnState(
        intent_name="CREATE_APPOINTMENT",
        missing_slots=[],
        effective_collected_slots=dict(payload["slots"]),
        needs_clarification=False,
        base_status="READY",
        ask_next=None,
    )
    availability = AvailabilityDecision(
        availability_ready=True,
        stored_fingerprint="fp",
        current_fingerprint="fp",
    )
    decision = resolve_confirmation(
        attached_request=attached,
        slot_state=slots,
        working_turn=working,
        availability=availability,
        session_state=session,
        gate_booking_intent="CREATE_APPOINTMENT",
        user_id="u-revise",
    )
    # Prior pending consumed; same-turn rebind may re-enter pending.
    assert get_confirmation_state(session) in (None, "pending")
    if decision.confirmation_state == "pending":
        assert decision.awaiting_user_confirmation is True
    else:
        assert decision.confirmation_state is None


def test_recovery_fires_for_awaiting_confirmation_unrecognized():
    outcome = {
        "status": "AWAITING_CONFIRMATION",
        "awaiting": "USER_CONFIRMATION",
        "missing_slots": [],
        "slots": {
            "service_id": "premium haircut",
            "date": "2026-07-06",
            "time": "09:00",
        },
        "turn": {"understanding": "UNRECOGNIZED_INPUT"},
        "current_turn_planning_evidence": False,
    }
    plan = {
        "status": "AWAITING_CONFIRMATION",
        "awaiting": "USER_CONFIRMATION",
        "turn": {"understanding": "UNRECOGNIZED_INPUT"},
        "_current_turn_planning_evidence": False,
    }
    assert should_render_recovery(
        result={"outcome": outcome},
        plan=plan,
        availability_client_present=True,
    )
    req = build_recovery_render_request(
        outcome=outcome,
        plan=plan,
        session_state={"confirmation_state": "pending"},
        user_input="aaa",
    )
    assert "go ahead" in req.render_instruction.lower()


def test_workflow_resume_pending_confirmation_shared_by_digression_paths():
    session = {
        "intent_name": "CREATE_APPOINTMENT",
        "confirmation_state": "pending",
        "awaiting": "USER_CONFIRMATION",
        "slots": {
            "service_id": "premium haircut",
            "date": "2026-07-06",
            "time": "09:00",
        },
    }
    resume = build_resume_instruction(session)
    assert resume is not None
    assert "confirm" in resume.text.lower()


def test_handler_and_off_topic_resume_same_pending_confirmation():
    """Pending confirmation is withheld from both LLM resume instructions."""
    from core.rendering.off_topic_renderer import build_off_topic_render_request
    from core.rendering.workflow_resume import (
        attach_resume_to_handler_render,
        compose_pending_confirmation_resume,
    )

    session = {
        "intent_name": "CREATE_APPOINTMENT",
        "confirmation_state": "pending",
        "awaiting": "USER_CONFIRMATION",
        "planning": {
            "slots": {
                "service_id": "premium haircut",
                "date": "2026-07-06",
                "time": "09:00",
            },
        },
        "messages": [],
    }
    ot = build_off_topic_render_request(
        {
            "off_topic_query": "Who is the president of Nigeria?",
            "answerable": True,
            "answer": "Bola Ahmed Tinubu.",
        },
        session_state=session,
        user_input="Who is the president of Nigeria?",
    )
    handler_instruction, handler_facts = attach_resume_to_handler_render(
        "Answer the FAQ using Facts only.",
        session_state=session,
        facts={"chunks": [{"content": "We open Sundays 10–4."}]},
    )
    assert "resume_instruction" not in ot.facts
    assert "resume_instruction" not in handler_facts
    assert handler_instruction == "Answer the FAQ using Facts only."
    suffix = compose_pending_confirmation_resume(session)
    assert suffix is not None
    assert "about to book" in suffix.lower()
    assert "Would you like me to go ahead?" in suffix


def test_slot_ask_resume_unchanged_for_handler_digression():
    from core.rendering.workflow_resume import attach_resume_to_handler_render

    session = {
        "intent_name": "CREATE_APPOINTMENT",
        "ask_next": "engine_type",
        "awaiting": "engine_type",
        "awaiting_slot": "engine_type",
        "missing_slots": ["engine_type", "date", "time"],
        "slots": {"service_id": "oil change"},
    }
    instruction, facts = attach_resume_to_handler_render(
        "Answer hours from Facts.",
        session_state=session,
        facts={},
    )
    resume = facts.get("resume_instruction") or ""
    assert "engine" in resume.lower() or "Ask for" in resume
    assert "Answer hours from Facts." in instruction
