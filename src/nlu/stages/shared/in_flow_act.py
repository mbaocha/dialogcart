"""
Shared in-flow booking act resolution for Stage 2 validation and pipeline fallback.
"""
import re
from typing import Any, Dict, Optional

IN_FLOW_BOOKING_INTENTS = frozenset(
    {"CREATE_APPOINTMENT", "CREATE_RESERVATION", "MODIFY_BOOKING"}
)

_IN_FLOW_BLOCKING_VERBS = re.compile(
    r"\b(book|schedule|reserve|cancel|modify|reschedule|change\s+booking)\b",
    re.IGNORECASE,
)

_IN_FLOW_CORRECTION_SIGNAL = re.compile(
    r"\b(instead|actually)\b|"
    r"\bwait,?\s*i\s+meant\b|"
    r"\bno,?\s*make\s+it\b|"
    r"\bchange\s+it\s+to\b",
    re.IGNORECASE,
)


def active_booking_intent_from_context(
    conversation_context: Optional[Dict[str, Any]],
) -> Optional[str]:
    if not isinstance(conversation_context, dict):
        return None
    last_intent = conversation_context.get("last_intent")
    if last_intent in IN_FLOW_BOOKING_INTENTS:
        return last_intent
    active = conversation_context.get("active_booking_intent")
    if active in IN_FLOW_BOOKING_INTENTS:
        return active
    return None


def promote_in_flow_booking_intent(
    intent: str,
    text: str,
    conversation_context: Optional[Dict[str, Any]] = None,
) -> str:
    """Promote UNKNOWN to the active booking intent during in-flow continuation."""
    if intent != "UNKNOWN":
        return intent
    if not isinstance(conversation_context, dict):
        return intent
    booking_intent = active_booking_intent_from_context(conversation_context)
    if not booking_intent:
        return intent
    if _IN_FLOW_BLOCKING_VERBS.search(text or ""):
        return intent
    if _IN_FLOW_CORRECTION_SIGNAL.search(text or ""):
        return intent
    return booking_intent
