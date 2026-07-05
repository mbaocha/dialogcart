"""
Stage 1 prompt builder — lightweight intent classification only.

Stage 1 does NOT extract slots. Its sole output is intent + confidence.
Slot extraction happens in Stage 2, routed per intent group.

Token budget target: ~500 tokens (vs ~1,800 for the combined extractor).
"""
from typing import Any, Dict, Optional

from ...registry.intent_groups import ALL_INTENTS, format_intent_registry
from ..shared.context import format_conversation_context

_TOOL = {
    "name": "classify_intent",
    "description": "Classify the user's intent from their message and conversation context. Do not extract slots.",
    "input_schema": {
        "type": "object",
        "properties": {
            "intent": {
                "type": "string",
                "enum": ALL_INTENTS,
                "description": "Detected user intent.",
            },
            "confidence": {
                "type": "number",
                "description": "Confidence score 0.0–1.0.",
            },
        },
        "required": ["intent", "confidence"],
    },
}


def build_system_prompt(
    now: str,
    conversation_context: Optional[Dict[str, Any]] = None,
) -> str:
    ctx_block = format_conversation_context(conversation_context or {})
    ctx_section = f"\n{ctx_block}\n" if ctx_block else ""
    return f"""You are an intent classifier for a booking platform.
Classify the user's intent only — do NOT extract slots, dates, times, or services.

Current date/time: {now}
{ctx_section}
{format_intent_registry()}

════════════════════════════════════════
CLASSIFICATION RULES
════════════════════════════════════════

SLOT-FILL CONTINUATION (active booking context only):
When CONVERSATION CONTEXT shows last_intent or active_booking_intent is
CREATE_APPOINTMENT, CREATE_RESERVATION, or MODIFY_BOOKING, AND the user
supplies ONLY missing slot material (bare date, bare time, date range, or service name/choice) with
NO new booking verb and NO correction language:
→ Return the SAME booking intent from context (NOT UNKNOWN, NOT CORRECTION).
  last_intent=CREATE_APPOINTMENT + "tomorrow"         → CREATE_APPOINTMENT
  last_intent=CREATE_APPOINTMENT + "11am"             → CREATE_APPOINTMENT
  last_intent=CREATE_RESERVATION + "march 10 to 15"  → CREATE_RESERVATION
  last_intent=CREATE_APPOINTMENT + "premium"          → CREATE_APPOINTMENT  (service reply)
  last_intent=CREATE_APPOINTMENT + "the standard one" → CREATE_APPOINTMENT  (service reply)
Do NOT apply without CONVERSATION CONTEXT (cold start).
Explicit booking verb in the utterance → classify normally, not slot-fill.

CORRECTION (active booking context only):
When context shows an active booking intent AND user replaces/corrects a slot
("actually X", "make it X instead", "wait I meant X"):
→ CORRECTION

BOOKING VERB RULE:
An explicit booking verb (book, schedule, reserve, cancel, modify, change, reschedule) is
sufficient to classify the intent — even when the service is generic or unspecified.
Service resolution is Stage 2's responsibility. Never return UNKNOWN when a booking verb is present.

COLD-START examples (no CONVERSATION CONTEXT):
  "haircut tomorrow"               → UNKNOWN  (no booking verb)
  "friday"                         → UNKNOWN  (bare weekday, no context)
  "book haircut at 10am"           → CREATE_APPOINTMENT
  "i want to book a service"       → CREATE_APPOINTMENT  (booking verb present; service unspecified)
  "book something for friday"      → CREATE_APPOINTMENT  (booking verb present; service unspecified)
  "i'd like to make a reservation" → CREATE_RESERVATION  (booking verb present)
  "cancel my booking"              → CANCEL_BOOKING
  "what services do you have"      → DISCOVERY"""


def get_tool() -> dict:
    return _TOOL
