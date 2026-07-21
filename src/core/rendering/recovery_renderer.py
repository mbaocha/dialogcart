"""LLM recovery rendering — when conversation cannot advance from the current turn.

Deterministic code selects *when* recovery is needed and assembles structured
facts. ``render_llm`` owns *how* the reply is worded.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from core.rendering.llm_renderer import LlmRenderRequest, render_llm

logger = logging.getLogger(__name__)

# Extensible recovery reason codes (only UNRECOGNIZED_INPUT is wired today).
RECOVERY_UNRECOGNIZED_INPUT = "UNRECOGNIZED_INPUT"

_INTERPRETED_TURN_OPERATIONS = frozenset(
    {
        "PROVIDE_SLOT_VALUE",
        "AVAILABILITY",
        "CHECK_AVAILABILITY",
        "CORRECTION",
        "MODIFY_BOOKING",
        "INFORMATIONAL",
        "CONFIRM_ACTION",
        "REJECT_ACTION",
        "browse_next",
        "browse_previous",
        "browse_more_times",
        "browse_more_days",
    }
)


def _plan_turn_operation(plan: Optional[Dict[str, Any]]) -> Optional[str]:
    if not isinstance(plan, dict):
        return None
    turn_op = plan.get("turn_operation")
    if turn_op:
        return str(turn_op)
    nested = plan.get("plan")
    if isinstance(nested, dict):
        nested_op = nested.get("turn_operation")
        if nested_op:
            return str(nested_op)
    return None


def _outcome_from_result(result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    outcome = result.get("outcome")
    if isinstance(outcome, dict):
        return outcome
    alt = result.get("result")
    if isinstance(alt, dict):
        return alt
    return None


def _missing_slots(outcome: Dict[str, Any]) -> List[str]:
    missing = outcome.get("missing_slots")
    if isinstance(missing, list):
        return [str(slot) for slot in missing if slot]
    facts = outcome.get("facts")
    if isinstance(facts, dict):
        facts_missing = facts.get("missing_slots")
        if isinstance(facts_missing, list):
            return [str(slot) for slot in facts_missing if slot]
    return []


def _slots(
    outcome: Dict[str, Any],
    session_state: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    slots = outcome.get("slots")
    if isinstance(slots, dict) and slots:
        return slots
    if isinstance(session_state, dict):
        session_slots = session_state.get("slots")
        if isinstance(session_slots, dict) and session_slots:
            return session_slots
    facts = outcome.get("facts")
    if isinstance(facts, dict):
        facts_slots = facts.get("slots")
        if isinstance(facts_slots, dict):
            return facts_slots
    return {}


def _presented_availability_summary(
    session_state: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    if not isinstance(session_state, dict):
        return None
    presented = session_state.get("presented_availability")
    if not isinstance(presented, dict):
        return None
    times = presented.get("times")
    slots = presented.get("slots")
    has_times = isinstance(times, list) and any(times)
    has_slots = isinstance(slots, list) and bool(slots)
    if not has_times and not has_slots:
        return None
    summary: Dict[str, Any] = {}
    if presented.get("search_date"):
        summary["search_date"] = presented.get("search_date")
    if has_times:
        summary["times"] = list(times)[:8]
        more = presented.get("more_count")
        if more:
            summary["more_count"] = more
    elif has_slots:
        summary["slot_count"] = len(slots)
    return summary


def turn_was_interpreted(*, plan: Optional[Dict[str, Any]], outcome: Dict[str, Any]) -> bool:
    """True when the current turn successfully advanced booking state."""
    if outcome.get("message_applied") is False:
        return False
    understanding = _turn_understanding(plan=plan, outcome=outcome)
    if understanding == "UNRECOGNIZED_INPUT":
        return False
    if understanding == "UNDERSTOOD":
        # Explicit NLU understanding — still require an interpreted operation for
        # "advanced booking state"; UNDERSTOOD alone does not imply progress.
        pass
    turn_op = _plan_turn_operation(plan)
    if turn_op in _INTERPRETED_TURN_OPERATIONS:
        return True
    return False


def _turn_understanding(
    *,
    plan: Optional[Dict[str, Any]],
    outcome: Dict[str, Any],
) -> Optional[str]:
    for source in (outcome, plan if isinstance(plan, dict) else None):
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
    return None


def should_render_recovery(
    *,
    result: Dict[str, Any],
    plan: Optional[Dict[str, Any]] = None,
    availability_client_present: bool = True,
) -> bool:
    """Deterministic gate: unrecognized / no-reply turns need recovery text.

    Fires when planning left no rendered reply and either:
    - status is READY (in-flow no-op / unrecognized), or
    - status is NEEDS_CLARIFICATION with ``turn.understanding=UNRECOGNIZED_INPUT``
      (cold-start gibberish — clarification has nothing useful to ask).

    Genuine clarification that already produced text is skipped via the text checks.
    NEEDS_CLARIFICATION without UNRECOGNIZED_INPUT is left to the clarification path.
    """
    if not availability_client_present:
        return False
    if result.get("text"):
        return False
    outcome = _outcome_from_result(result)
    if not isinstance(outcome, dict):
        return False
    if outcome.get("text"):
        return False

    status = outcome.get("status")
    understanding = _turn_understanding(plan=plan, outcome=outcome)

    if status == "NEEDS_CLARIFICATION":
        # Cold-start / act-indeterminate unrecognized input — not slot clarification.
        return understanding == "UNRECOGNIZED_INPUT"

    if status != "READY":
        return False

    if understanding == "UNDERSTOOD":
        return False
    if understanding == "UNRECOGNIZED_INPUT":
        return True
    if turn_was_interpreted(plan=plan, outcome=outcome):
        return False
    turn_op = _plan_turn_operation(plan)
    if turn_op in _INTERPRETED_TURN_OPERATIONS:
        return False
    # Unrecognized / no-op turn: absent, empty, or NONE turn_operation,
    # or explicit message_applied=False from NLU failure replay.
    if outcome.get("message_applied") is False:
        return True
    return turn_op in (None, "", "NONE")


def build_recovery_context(
    *,
    reason: str,
    outcome: Dict[str, Any],
    plan: Optional[Dict[str, Any]] = None,
    session_state: Optional[Dict[str, Any]] = None,
    user_input: Optional[str] = None,
) -> Dict[str, Any]:
    """Assemble structured recovery facts (no English wording)."""
    slots = _slots(outcome, session_state)
    missing = _missing_slots(outcome)
    presented = _presented_availability_summary(session_state)

    stage = outcome.get("stage")
    if not stage and isinstance(plan, dict):
        stage = plan.get("stage")

    context: Dict[str, Any] = {
        "reason": reason,
        "awaiting": outcome.get("awaiting"),
        "missing_slots": missing,
        "conversation_stage": stage,
        "selected_service": slots.get("service_id"),
        "selected_date": slots.get("date") or slots.get("date_range"),
        "selected_time": slots.get("time"),
    }
    if presented is not None:
        context["presented_availability"] = presented
    if user_input is not None and str(user_input).strip():
        context["user_input"] = str(user_input).strip()
    if isinstance(session_state, dict):
        slot_attempts = session_state.get("slot_attempts")
        if isinstance(slot_attempts, dict) and slot_attempts:
            context["slot_attempts"] = slot_attempts
        awaiting_slot = session_state.get("awaiting_slot")
        if awaiting_slot:
            context["awaiting_slot"] = awaiting_slot
    # Drop null/empty values for a clean evidence bundle.
    return {k: v for k, v in context.items() if v not in (None, "", [], {})}


def _has_active_workflow_evidence(context: Dict[str, Any]) -> bool:
    """True when recovery evidence implies an in-progress booking workflow."""
    if context.get("awaiting") or context.get("awaiting_slot"):
        return True
    if context.get("missing_slots"):
        return True
    if context.get("selected_service") or context.get("selected_date") or context.get(
        "selected_time"
    ):
        return True
    presented = context.get("presented_availability")
    if isinstance(presented, dict) and (
        presented.get("times") or presented.get("slot_count")
    ):
        return True
    return False


def _recovery_instruction(context: Dict[str, Any]) -> str:
    """Instruction for wording only — must not invent facts beyond evidence."""
    reason = context.get("reason") or RECOVERY_UNRECOGNIZED_INPUT

    if not _has_active_workflow_evidence(context):
        # Cold-start / no active workflow: stay intent-neutral.
        return (
            f"Conversation recovery is required (reason={reason}). "
            "The latest user message could not be understood. "
            "There is no active booking or workflow in progress. "
            "Apologise briefly that you did not understand, then invite the user "
            "to rephrase or say how you can help. "
            "Do NOT assume they want to book, change, or cancel anything. "
            "Do NOT mention appointments, services, dates, times, or availability. "
            "Do not invent facts. Be natural, warm, and brief (1–2 sentences)."
        )

    missing = context.get("missing_slots") or []
    awaiting = context.get("awaiting")
    presented = context.get("presented_availability")

    focus_parts: List[str] = []
    if awaiting:
        focus_parts.append(f"awaiting={awaiting}")
    if missing:
        focus_parts.append(f"still need: {', '.join(missing)}")
    if presented and presented.get("times"):
        focus_parts.append("times were already offered to the user")
    elif "date" in missing or "date_range" in missing:
        focus_parts.append("a booking date is still needed")
    elif "time" in missing:
        focus_parts.append("a booking time is still needed")

    focus = "; ".join(focus_parts) if focus_parts else "continue the current booking"

    return (
        f"Conversation recovery is required (reason={reason}). "
        f"The latest user message could not be applied to advance the booking. "
        f"Current focus: {focus}. "
        "Apologise briefly that you did not understand, then guide the user on "
        "what they can say or do next using ONLY the Recovery context evidence. "
        "If presented times or a selected date/service appear in the evidence, "
        "you may refer to them. "
        "Do not invent times, dates, services, or availability. "
        "Do not claim a booking was made. Be natural, warm, and brief (1–3 sentences)."
    )


def build_recovery_render_request(
    *,
    reason: str = RECOVERY_UNRECOGNIZED_INPUT,
    outcome: Dict[str, Any],
    plan: Optional[Dict[str, Any]] = None,
    session_state: Optional[Dict[str, Any]] = None,
    user_input: Optional[str] = None,
    structured_context: Optional[Dict[str, Any]] = None,
    conversation_history: Optional[List[Dict[str, str]]] = None,
) -> LlmRenderRequest:
    """Build an LLM render request for conversation recovery."""
    recovery = build_recovery_context(
        reason=reason,
        outcome=outcome,
        plan=plan,
        session_state=session_state,
        user_input=user_input,
    )
    facts: Dict[str, Any] = {
        "recovery": recovery,
        "structured_context": structured_context or {},
    }
    presented = recovery.get("presented_availability")
    if isinstance(presented, dict) and presented.get("times"):
        facts["availability"] = {
            "service_name": recovery.get("selected_service"),
            "date": presented.get("search_date") or recovery.get("selected_date"),
            "times": presented.get("times"),
            "more_count": presented.get("more_count") or 0,
        }
    return LlmRenderRequest(
        render_instruction=_recovery_instruction(recovery),
        facts=facts,
        conversation_history=list(conversation_history or []),
    )


def inject_recovery_text(
    result: Dict[str, Any],
    *,
    plan: Optional[Dict[str, Any]] = None,
    session_state: Optional[Dict[str, Any]] = None,
    user_input: Optional[str] = None,
    availability_client_present: bool = True,
    decision: Optional[Dict[str, Any]] = None,
) -> None:
    """Best-effort: render recovery text when the recovery gate fires."""
    if not should_render_recovery(
        result=result,
        plan=plan,
        availability_client_present=availability_client_present,
    ):
        return

    outcome = _outcome_from_result(result)
    if not isinstance(outcome, dict):
        return

    try:
        from core.rendering.response_renderer import _structured_context_from_decision

        structured = _structured_context_from_decision(
            decision or (plan or {}).get("_decision") or {}
        )
        conversation_history = (
            (session_state or {}).get("messages", [])
            if isinstance(session_state, dict)
            else []
        )
        request = build_recovery_render_request(
            reason=RECOVERY_UNRECOGNIZED_INPUT,
            outcome=outcome,
            plan=plan,
            session_state=session_state,
            user_input=user_input,
            structured_context=structured,
            conversation_history=conversation_history,
        )
        rendered = render_llm(request)
        if rendered:
            result["text"] = rendered
            outcome["text"] = rendered
    except Exception as e:
        logger.warning(
            "Failed to render recovery text: %s. Rendering is best-effort.",
            e,
        )
