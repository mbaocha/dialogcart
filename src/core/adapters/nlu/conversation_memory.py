"""
Conversation memory helpers for NLU context passing.

Stores the last 5 turns under ``conversation.memory`` so that follow-up messages
("and for groups?", "how long is it?") can be resolved by NLU without the
orchestrator needing to touch the booking kernel.

Also maintains ``conversation.history`` — a simple [{role, text}] list capped at 5
turn-pairs (10 entries) — written every turn for both durable and non-durable
intents, enabling cross-turn RAG context regardless of intent type.

Schema (session["conversation"]["memory"]):
    {
        "last_intent":      str | None,   # intent from the previous turn
        "last_search_query": str | None,  # search_query from the previous turn
        "turns": [                        # prior turns, max 5, FIFO
            {
                "user":         str,
                "intent":       str,
                "search_query": str | None,
                "assistant":    str,       # optional
            }
        ]
    }

Schema (session["conversation"]["history"]):
    [{"role": "user" | "assistant", "text": str}, ...]  # max 10 entries (5 turns)
"""
from typing import Any, Dict, List, Optional

from ...session.session_schema_v2 import (
    get_conversation_history,
    get_conversation_memory,
)

# Booking intents that accept bare slot-fill follow-ups (not FAQ/RAG).
_SLOT_FILL_BOOKING_INTENTS = frozenset(
    {"CREATE_APPOINTMENT", "CREATE_RESERVATION", "MODIFY_BOOKING"}
)


def _attach_active_booking_intent(
    result: Dict[str, Any], session: Dict[str, Any]
) -> Dict[str, Any]:
    """Expose durable session booking intent when last turn was informational (FAQ detour)."""
    intent_name = session.get("intent_name")
    if not isinstance(intent_name, str) or not intent_name:
        return result
    if intent_name not in _SLOT_FILL_BOOKING_INTENTS:
        return result
    from core.session.durable_intents import is_durable_intent

    if not is_durable_intent(intent_name):
        return result
    last = result.get("last_intent")
    if last in _SLOT_FILL_BOOKING_INTENTS:
        return result
    return {**result, "active_booking_intent": intent_name}


def _attach_resolved_service_id(
    result: Dict[str, Any], session: Dict[str, Any]
) -> Dict[str, Any]:
    """Tell NLU which service is already locked when service_id is satisfied."""
    missing = session.get("missing_slots")
    if isinstance(missing, list) and "service_id" in missing:
        return result
    slots = session.get("slots")
    if not isinstance(slots, dict):
        facts = session.get("facts")
        if isinstance(facts, dict):
            nested = facts.get("slots")
            if isinstance(nested, dict):
                slots = nested
    if not isinstance(slots, dict):
        return result
    service_id = slots.get("service_id")
    if isinstance(service_id, str) and service_id:
        return {**result, "resolved_service_id": service_id}
    return result


def _attach_service_candidates(
    result: Dict[str, Any], session: Dict[str, Any]
) -> Dict[str, Any]:
    """Pass active disambiguation options to NLU when service_id is still missing."""
    missing = session.get("missing_slots")
    if not isinstance(missing, list) or "service_id" not in missing:
        return result
    cands = session.get("service_candidates")
    if isinstance(cands, list) and cands:
        return {**result, "service_candidates": cands}
    return result


def build_conversation_context(
    session: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Return the NLU conversation_context dict from session, or None.

    Merges session["conversation"] (structured turn data) with session["messages"]
    (flat role/text list). Returns None when session carries no useful data.
    """
    if not session or not isinstance(session, dict):
        return None
    conv = get_conversation_memory(session)
    messages = get_conversation_history(session)

    has_conv = (
        isinstance(conv, dict)
        and (conv.get("last_intent") or conv.get("last_search_query") or conv.get("turns"))
    )
    has_messages = isinstance(messages, list) and len(messages) > 0

    if not has_conv and not has_messages:
        # Durable booking sessions may lack session["conversation"] when the prior
        # turn's merged Luma response was not persisted (e.g. handle_message strips
        # _merged_luma_response). Synthesize last_intent so NLU can bind slot-fill
        # dates ("march 10 to 15") against the active booking intent.
        intent_name = session.get("intent_name")
        if isinstance(intent_name, str) and intent_name:
            from core.session.durable_intents import is_durable_intent

            if is_durable_intent(intent_name):
                result = {"last_intent": intent_name}
                last_dp = session.get("date_proposal")
                if not isinstance(last_dp, dict):
                    facts = session.get("facts")
                    if isinstance(facts, dict):
                        last_dp = facts.get("date_proposal")
                if isinstance(last_dp, dict) and last_dp.get("start"):
                    result["last_date_proposal"] = last_dp
                missing = session.get("missing_slots")
                if isinstance(missing, list) and missing:
                    result["missing_slots"] = missing
                result = _attach_resolved_service_id(result, session)
                result = _attach_service_candidates(result, session)
                return result
        return None

    result: Dict[str, Any] = dict(conv) if has_conv else {}
    if has_messages:
        result["messages"] = messages

    last_dp = session.get("date_proposal")
    if not isinstance(last_dp, dict):
        facts = session.get("facts")
        if isinstance(facts, dict):
            last_dp = facts.get("date_proposal")
    if isinstance(last_dp, dict) and last_dp.get("start"):
        result["last_date_proposal"] = last_dp

    result = _attach_active_booking_intent(result, session)
    missing = session.get("missing_slots")
    if isinstance(missing, list) and missing:
        result = {**result, "missing_slots": missing}
    result = _attach_resolved_service_id(result, session)
    result = _attach_service_candidates(result, session)
    return result


def update_conversation(
    session: Dict[str, Any],
    *,
    user_text: str,
    intent: str,
    search_query: Optional[str],
    assistant_text: Optional[str] = None,
) -> Dict[str, Any]:
    """Return a new session dict with session["conversation"] updated for this turn.

    Appends the current turn to the turns list and caps the list at 5 (FIFO).
    Does NOT mutate the input dict.
    """
    prev = get_conversation_memory(session)
    turns: List[Dict[str, Any]] = list(prev.get("turns") or [])

    turn: Dict[str, Any] = {
        "user": user_text,
        "intent": intent,
        "search_query": search_query,
    }
    if assistant_text is not None:
        turn["assistant"] = assistant_text
    turns.append(turn)

    if len(turns) > 5:
        turns = turns[-5:]

    updated_conv: Dict[str, Any] = {
        "last_intent": intent,
        "last_search_query": search_query,
        "turns": turns,
    }
    result = dict(session)
    conversation = result.get("conversation")
    if isinstance(conversation, dict) and (
        "memory" in conversation or "history" in conversation
    ):
        result["conversation"] = {**conversation, "memory": updated_conv}
    else:
        result["conversation"] = updated_conv
    return result


def append_messages_turn(
    session: Dict[str, Any],
    user_text: str,
    assistant_text: Optional[str],
    max_turns: int = 5,
) -> Dict[str, Any]:
    """Return a new session dict with session["messages"] updated for this turn.

    Appends {role, text} entries for the user and (when present) assistant.
    Caps the list at max_turns * 2 entries (FIFO). Does NOT mutate the input.
    """
    messages: List[Dict[str, str]] = list(get_conversation_history(session))
    messages.append({"role": "user", "text": user_text})
    if assistant_text:
        messages.append({"role": "assistant", "text": assistant_text})
    max_entries = max_turns * 2
    if len(messages) > max_entries:
        messages = messages[-max_entries:]
    result = {**session, "messages": messages}
    conversation = result.get("conversation")
    if isinstance(conversation, dict) and (
        "history" in conversation or "memory" in conversation
    ):
        result["conversation"] = {**conversation, "history": list(messages)}
    return result
