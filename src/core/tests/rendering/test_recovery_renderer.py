"""Focused tests for LLM-based conversation recovery rendering."""

from __future__ import annotations

from core.rendering.recovery_renderer import (
    RECOVERY_ORPHANED_CONFIRMATION_ACTION,
    RECOVERY_UNRECOGNIZED_INPUT,
    build_recovery_context,
    build_recovery_render_request,
    inject_recovery_text,
    should_render_recovery,
    turn_was_interpreted,
)
from core.rendering.response_renderer import ResponseRenderer


def _waiting_for_time_outcome():
    return {
        "status": "READY",
        "stage": "AVAILABILITY",
        "missing_slots": ["time"],
        "slots": {"service_id": "haircut", "date": "2026-07-22"},
        "awaiting": "TIME_SELECTION",
    }


def _waiting_for_date_outcome():
    return {
        "status": "READY",
        "stage": "AVAILABILITY",
        "missing_slots": ["date", "time"],
        "slots": {"service_id": "haircut"},
    }


def _general_outcome():
    return {
        "status": "READY",
        "stage": "CONFIRM",
        "missing_slots": [],
        "slots": {
            "service_id": "haircut",
            "date": "2026-07-22",
            "time": "09:00",
        },
    }


def test_recovery_context_waiting_for_time():
    outcome = _waiting_for_time_outcome()
    session = {
        "presented_availability": {
            "search_date": "2026-07-22",
            "times": ["09:00", "10:00"],
            "slots": [{"starts_at": "2026-07-22T09:00:00Z"}],
        },
        "slot_attempts": {"time": 1},
    }
    ctx = build_recovery_context(
        reason=RECOVERY_UNRECOGNIZED_INPUT,
        outcome=outcome,
        plan={"status": "READY", "stage": "AVAILABILITY"},
        session_state=session,
        user_input="xxx",
    )
    assert ctx["reason"] == RECOVERY_UNRECOGNIZED_INPUT
    assert ctx["awaiting"] == "TIME_SELECTION"
    assert "time" in ctx["missing_slots"]
    assert ctx["selected_service"] == "haircut"
    assert ctx["selected_date"] == "2026-07-22"
    assert ctx["presented_availability"]["times"] == ["09:00", "10:00"]
    assert ctx["user_input"] == "xxx"
    assert ctx["slot_attempts"] == {"time": 1}


def test_recovery_context_waiting_for_date():
    ctx = build_recovery_context(
        reason=RECOVERY_UNRECOGNIZED_INPUT,
        outcome=_waiting_for_date_outcome(),
        plan={"status": "READY"},
        session_state={},
        user_input="22bnd",
    )
    assert ctx["reason"] == RECOVERY_UNRECOGNIZED_INPUT
    assert "date" in ctx["missing_slots"]
    assert ctx["selected_service"] == "haircut"
    assert ctx["user_input"] == "22bnd"
    assert "presented_availability" not in ctx


def test_recovery_context_general_booking():
    ctx = build_recovery_context(
        reason=RECOVERY_UNRECOGNIZED_INPUT,
        outcome=_general_outcome(),
        plan={"status": "READY", "stage": "CONFIRM"},
        session_state={},
        user_input="blah",
    )
    assert ctx["reason"] == RECOVERY_UNRECOGNIZED_INPUT
    assert ctx["selected_service"] == "haircut"
    assert ctx["selected_date"] == "2026-07-22"
    assert ctx["selected_time"] == "09:00"
    assert ctx.get("missing_slots") in (None, [])


def test_build_recovery_render_request_uses_llm_path_shape():
    request = build_recovery_render_request(
        reason=RECOVERY_UNRECOGNIZED_INPUT,
        outcome=_waiting_for_time_outcome(),
        plan={"status": "READY"},
        session_state={
            "presented_availability": {
                "search_date": "2026-07-22",
                "times": ["09:00"],
            }
        },
        user_input="xxx",
        structured_context={"business_name": "Test Salon"},
    )
    assert "recovery" in request.facts
    assert request.facts["recovery"]["reason"] == RECOVERY_UNRECOGNIZED_INPUT
    assert "UNRECOGNIZED_INPUT" in request.render_instruction
    assert "Do not invent" in request.render_instruction
    assert "advance the booking" in request.render_instruction
    # No hard-coded user-facing apology strings in the instruction.
    assert "Sorry, I didn't quite understand that." not in request.render_instruction
    assert request.facts.get("availability", {}).get("times") == ["09:00"]


def test_general_recovery_instruction_is_intent_neutral():
    """No active workflow evidence → do not steer toward booking."""
    request = build_recovery_render_request(
        reason=RECOVERY_UNRECOGNIZED_INPUT,
        outcome={
            "status": "NEEDS_CLARIFICATION",
            "stage": "AVAILABILITY",
            "missing_slots": [],
            "slots": {},
            "intent_name": "UNKNOWN",
            "turn": {"understanding": "UNRECOGNIZED_INPUT"},
        },
        plan={"status": "NEEDS_CLARIFICATION"},
        session_state={},
        user_input="aaa",
    )
    instruction = request.render_instruction
    assert "Do NOT assume they want to book" in instruction
    assert "Do NOT mention appointments" in instruction
    assert "rephrase" in instruction.lower() or "how you can help" in instruction.lower()
    assert "advance the booking" not in instruction
    assert "booking, changing, or cancelling" not in instruction
    assert request.facts["recovery"]["reason"] == RECOVERY_UNRECOGNIZED_INPUT
    assert "selected_service" not in request.facts["recovery"]
    assert "missing_slots" not in request.facts["recovery"]

def test_interpreted_turn_does_not_trigger_recovery():
    outcome = _waiting_for_time_outcome()
    plan = {"turn_operation": "PROVIDE_SLOT_VALUE", "status": "READY"}
    assert turn_was_interpreted(plan=plan, outcome=outcome) is True
    result = {"success": True, "outcome": outcome}
    assert (
        should_render_recovery(
            result=result,
            plan=plan,
            availability_client_present=True,
        )
        is False
    )


def test_orphaned_confirmation_actions_trigger_distinct_recovery():
    for operation in ("CONFIRM_ACTION", "REJECT_ACTION"):
        outcome = {
            "status": "READY",
            "action": None,
            "slots": {},
            "turn": {"understanding": "UNDERSTOOD"},
            "facts": {
                "current_turn_planning_evidence": True,
                "_raw_luma_response": {"intent": {"name": operation}},
            },
        }
        assert should_render_recovery(
            result={
                "success": True,
                "outcome": outcome,
                "_merged_luma_response": {"intent": {"name": operation}},
            },
            plan={"status": "READY", "action": None, "turn_operation": "NONE"},
            session_state={},
            availability_client_present=True,
        )


def test_orphaned_yes_recovers_when_planner_action_left_no_text():
    """Stale CONFIRM_ACTION after the gate closed must not stay silent.

    Planner may still select SEARCH_AVAILABILITY; if execution produced no
    reply, recovery must ask what to do next.
    """
    outcome = {
        "status": "READY",
        "action": "SEARCH_AVAILABILITY",
        "slots": {"service_id": "flexi haircut + pruning"},
        "missing_slots": ["time"],
        "turn": {"understanding": "UNDERSTOOD"},
        "facts": {
            "current_turn_planning_evidence": True,
            "_raw_luma_response": {"intent": {"name": "CONFIRM_ACTION"}},
        },
    }
    result = {
        "success": True,
        "outcome": outcome,
        "_merged_luma_response": {"intent": {"name": "CONFIRM_ACTION"}},
        "_execution_result": {"status": "succeeded", "availability": {"slots": []}},
    }
    assert should_render_recovery(
        result=result,
        plan={
            "status": "READY",
            "action": "SEARCH_AVAILABILITY",
            "turn_operation": "CONFIRM_ACTION",
        },
        session_state={"confirmation_state": None},
        availability_client_present=True,
    )


def test_pending_confirmation_actions_remain_suppressed():
    for operation in ("CONFIRM_ACTION", "REJECT_ACTION"):
        outcome = {
            "status": "READY",
            "action": None,
            "slots": {},
            "turn": {"understanding": "UNDERSTOOD"},
            "facts": {
                "current_turn_planning_evidence": True,
                "_raw_luma_response": {"intent": {"name": operation}},
            },
        }
        assert not should_render_recovery(
            result={"success": True, "outcome": outcome},
            plan={"status": "READY", "action": None, "turn_operation": "NONE"},
            session_state={"confirmation_state": "pending"},
            availability_client_present=True,
        )


def test_orphaned_confirmation_action_uses_truthful_instruction(monkeypatch):
    from core.rendering import recovery_renderer as rr

    captured = {}

    def _fake_render(request):
        captured["reason"] = request.facts["recovery"]["reason"]
        captured["instruction"] = request.render_instruction
        return "There's nothing waiting for confirmation right now. What would you like to do next?"

    monkeypatch.setattr(rr, "render_llm", _fake_render)
    outcome = {
        "status": "READY",
        "action": None,
        "slots": {},
        "turn": {"understanding": "UNDERSTOOD"},
        "facts": {
            "current_turn_planning_evidence": True,
            "_raw_luma_response": {"intent": {"name": "CONFIRM_ACTION"}},
        },
    }
    result = {
        "success": True,
        "outcome": outcome,
        "_merged_luma_response": {"intent": {"name": "CONFIRM_ACTION"}},
    }
    inject_recovery_text(
        result,
        plan={"status": "READY", "action": None, "turn_operation": "NONE"},
        session_state={},
        user_input="go on",
        availability_client_present=True,
    )

    assert captured["reason"] == RECOVERY_ORPHANED_CONFIRMATION_ACTION
    assert "was understood" in captured["instruction"]
    assert "could not be understood" not in captured["instruction"]
    assert result["text"]


def test_should_render_recovery_for_unrecognized_ready():
    result = {"success": True, "outcome": _waiting_for_date_outcome()}
    assert (
        should_render_recovery(
            result=result,
            plan={"status": "READY"},
            availability_client_present=True,
        )
        is True
    )


def test_should_render_recovery_from_turn_understanding():
    outcome = {
        **_waiting_for_date_outcome(),
        "turn": {"understanding": "UNRECOGNIZED_INPUT"},
    }
    result = {"success": True, "outcome": outcome}
    assert (
        should_render_recovery(
            result=result,
            plan={"status": "READY", "turn_operation": "NONE"},
            availability_client_present=True,
        )
        is True
    )


def test_should_render_recovery_for_unrecognized_needs_clarification():
    """Cold-start UNKNOWN → NEEDS_CLARIFICATION with UNRECOGNIZED_INPUT."""
    outcome = {
        "status": "NEEDS_CLARIFICATION",
        "stage": "AVAILABILITY",
        "missing_slots": [],
        "slots": {},
        "intent_name": "UNKNOWN",
        "turn": {"understanding": "UNRECOGNIZED_INPUT"},
    }
    result = {"success": True, "outcome": outcome}
    assert (
        should_render_recovery(
            result=result,
            plan={"status": "NEEDS_CLARIFICATION"},
            availability_client_present=True,
        )
        is True
    )


def test_should_not_render_recovery_for_clarification_without_unrecognized():
    """Genuine NEEDS_CLARIFICATION (missing slots) must not steal the clarification path."""
    outcome = {
        "status": "NEEDS_CLARIFICATION",
        "stage": "AVAILABILITY",
        "missing_slots": ["service_id", "time"],
        "slots": {},
        "intent_name": "CREATE_APPOINTMENT",
        "turn": {"understanding": "UNDERSTOOD"},
    }
    result = {"success": True, "outcome": outcome}
    assert (
        should_render_recovery(
            result=result,
            plan={"status": "NEEDS_CLARIFICATION"},
            availability_client_present=True,
        )
        is False
    )


def test_should_not_render_recovery_when_turn_understood():
    outcome = {
        **_waiting_for_date_outcome(),
        "turn": {"understanding": "UNDERSTOOD"},
    }
    result = {"success": True, "outcome": outcome}
    assert (
        should_render_recovery(
            result=result,
            plan={"status": "READY", "turn_operation": "NONE"},
            availability_client_present=True,
        )
        is False
    )


def test_should_not_render_without_availability_client():
    result = {"success": True, "outcome": _waiting_for_date_outcome()}
    assert (
        should_render_recovery(
            result=result,
            plan={"status": "READY"},
            availability_client_present=False,
        )
        is False
    )


def test_inject_recovery_calls_render_llm(monkeypatch):
    from core.rendering import recovery_renderer as rr

    calls = {"n": 0}

    def _fake_render(request):
        calls["n"] += 1
        assert request.facts["recovery"]["reason"] == RECOVERY_UNRECOGNIZED_INPUT
        assert request.facts["recovery"]["user_input"] == "xxx"
        return "Sorry, I didn't quite understand that. Please pick a time."

    monkeypatch.setattr(rr, "render_llm", _fake_render)

    result = {
        "success": True,
        "outcome": _waiting_for_time_outcome(),
    }
    inject_recovery_text(
        result,
        plan={"status": "READY"},
        session_state={
            "presented_availability": {
                "times": ["09:00"],
                "search_date": "2026-07-22",
            }
        },
        user_input="xxx",
        availability_client_present=True,
    )
    assert calls["n"] == 1
    assert result.get("text")
    assert "understand" in result["text"].lower()
    assert result["outcome"]["text"] == result["text"]


def test_inject_skips_interpreted_turn(monkeypatch):
    from core.rendering import recovery_renderer as rr

    def _should_not_run(_request):
        raise AssertionError("render_llm must not be called for interpreted turns")

    monkeypatch.setattr(rr, "render_llm", _should_not_run)
    result = {"success": True, "outcome": _waiting_for_time_outcome()}
    inject_recovery_text(
        result,
        plan={"turn_operation": "PROVIDE_SLOT_VALUE"},
        session_state={},
        user_input="9am",
        availability_client_present=True,
    )
    assert "text" not in result


def test_response_renderer_recovery_path(monkeypatch):
    from core.rendering import recovery_renderer as rr

    monkeypatch.setattr(
        rr,
        "render_llm",
        lambda request: f"recovered:{request.facts['recovery']['reason']}",
    )
    renderer = ResponseRenderer()
    result = {"success": True, "outcome": _waiting_for_date_outcome()}
    renderer.render_recovery(
        result,
        plan={"status": "READY"},
        session_state={},
        user_input="22bnd",
        availability_client_present=True,
    )
    assert result["text"] == f"recovered:{RECOVERY_UNRECOGNIZED_INPUT}"
