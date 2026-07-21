"""
Per-turn utterance understanding outcome.

Produced by NLU from extraction evidence — not inferred from planner status.
Extensible values (only UNDERSTOOD / UNRECOGNIZED_INPUT are emitted today):
  UNDERSTOOD, UNRECOGNIZED_INPUT, AMBIGUOUS, MISSING_REFERENCE
"""
from typing import Any, Dict, Optional

from .in_flow_act import (
    IN_FLOW_BOOKING_INTENTS,
    active_booking_intent_from_context,
)

UNDERSTOOD = "UNDERSTOOD"
UNRECOGNIZED_INPUT = "UNRECOGNIZED_INPUT"

# Dialog acts whose intent label alone is sufficient utterance understanding.
_DIALOG_ACT_INTENTS = frozenset({"CONFIRM_ACTION", "REJECT_ACTION"})


def _nonempty_list(value: Any) -> bool:
    return isinstance(value, list) and any(item not in (None, "", []) for item in value)


def _has_current_turn_service_id(
    slm: Dict[str, Any],
    facts: Dict[str, Any],
    conversation_context: Optional[Dict[str, Any]],
) -> bool:
    """True when facts.service_id came from this utterance, not session stickiness.

    ``_resolve_service_ambiguity`` reuses ``resolved_service_id`` into facts when
    the utterance has no service_term (date/time-only or gibberish follow-ups).
    That reuse must not count as utterance understanding.
    """
    service_id = facts.get("service_id")
    if not service_id:
        return False
    service_term = slm.get("service_term")
    if isinstance(service_term, str) and service_term.strip():
        return True
    ctx = conversation_context if isinstance(conversation_context, dict) else {}
    resolved = ctx.get("resolved_service_id")
    # Session-locked reuse with no term this turn — not utterance evidence.
    if resolved and service_id == resolved:
        return False
    return True


def _has_utterance_evidence(
    slm: Dict[str, Any],
    conversation_context: Optional[Dict[str, Any]] = None,
) -> bool:
    """True when the utterance yielded extractable content or a structured subtype."""
    if slm.get("operation"):
        return True
    search_query = slm.get("search_query")
    if isinstance(search_query, str) and search_query.strip():
        return True
    service_term = slm.get("service_term")
    if isinstance(service_term, str) and service_term.strip():
        return True
    # Do not treat service_candidates alone as evidence. With a null service_term,
    # resolve_service dumps the full catalog as candidates for clarification —
    # that is post-process, not utterance understanding. Ambiguous Stage-2
    # candidates always accompany a service_term (already handled above).

    facts = slm.get("facts") if isinstance(slm.get("facts"), dict) else {}
    if _has_current_turn_service_id(slm, facts, conversation_context):
        return True
    if facts.get("booking_id"):
        return True
    if _nonempty_list(facts.get("dates")):
        return True
    if _nonempty_list(facts.get("times")):
        return True
    if _nonempty_list(facts.get("date_time_pairs")):
        return True

    tc = slm.get("time_constraint")
    if isinstance(tc, dict) and tc.get("mode") not in (None, "none"):
        return True

    temporal = slm.get("temporal")
    if isinstance(temporal, dict):
        if temporal.get("mode") not in (None, "none"):
            return True
        for key in (
            "start_date",
            "start_time",
            "end_date",
            "end_time",
            "start_date_expression",
            "start_time_expression",
            "end_date_expression",
            "end_time_expression",
            "expression",
        ):
            value = temporal.get(key)
            if isinstance(value, str) and value.strip():
                return True
    return False


def derive_turn_understanding(
    slm: Dict[str, Any],
    conversation_context: Optional[Dict[str, Any]] = None,
) -> str:
    """Classify whether the current utterance was successfully understood.

    UNDERSTOOD — utterance yielded facts, temporal material, operation, search
    query, dialog act, or an intent that is not empty in-flow continuation.
    UNRECOGNIZED_INPUT — active booking continuation with no extractable content
    (e.g. gibberish), or UNKNOWN with no evidence.
    """
    if _has_utterance_evidence(slm, conversation_context):
        return UNDERSTOOD

    intent = slm.get("intent", "UNKNOWN")
    if isinstance(intent, dict):
        intent = intent.get("name", "UNKNOWN")
    intent = intent or "UNKNOWN"

    if intent in _DIALOG_ACT_INTENTS:
        return UNDERSTOOD

    if intent == "UNKNOWN":
        return UNRECOGNIZED_INPUT

    active = active_booking_intent_from_context(conversation_context)
    if active and intent in IN_FLOW_BOOKING_INTENTS and intent == active:
        # In-flow continuation of the same booking act with nothing extracted.
        return UNRECOGNIZED_INPUT

    # Explicit utterance-classified intent (booking verb, FAQ, cancel, …) with
    # no slots this turn — still understood as a conversational act.
    return UNDERSTOOD
