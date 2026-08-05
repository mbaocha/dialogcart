"""
Stage 1 prompt builder — lightweight intent classification only.

Stage 1 does NOT extract slots. Its sole output is intent + confidence.
Slot extraction happens in Stage 2, routed per intent group.

Token budget target: ~500 tokens (vs ~1,800 for the combined extractor).
"""
from typing import Any, Dict, Optional

from ...registry.intent_groups import ALL_INTENTS, format_intent_registry
from ..shared.confirm_dialog_act import confirm_action_dialog_act_section
from ..shared.reject_dialog_act import reject_action_dialog_act_section
from ..shared.context import format_conversation_context
from ..shared.slot_fill_continuation import slot_fill_continuation_section

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

{slot_fill_continuation_section()}

CONVERSATIONAL ANSWER (assistant just requested or offered values):
When Immediately preceding assistant asked for or offered a finite set of values
(service, engine type, room, staff, membership, date/time choice, etc.), AND the
user replies with one of those values or an unambiguous reference to one
("the first one", "premium", "that one", "the deluxe room"):
→ Interpret the reply as answering that prompt (NOT UNKNOWN, NOT a fresh FAQ).
  Prefer the booking / workflow intent that the answer advances:
  - selecting a bookable service / starting a timed booking → CREATE_APPOINTMENT
  - selecting accommodation → CREATE_RESERVATION
  - answering a slot while last_intent/active_booking_intent is already a booking
    intent → keep that booking intent (same as slot-fill continuation)
  Do not invent values the user did not say. Entity extraction is Stage 2.
Examples (pattern — not intent-specific names):
  Assistant asked "Which service would you like?" + offered list
    + "Executive Oil Change"     → CREATE_APPOINTMENT
  Assistant asked "Which service would you like?"
    + "the first one" / "Premium" / "that one" → CREATE_APPOINTMENT
  Assistant asked "Which engine type?" + "Petrol"           → CREATE_APPOINTMENT
    (when prior context is an active booking / slot ask)
  Assistant asked "Which room?" + "Deluxe" / "the deluxe room" → CREATE_RESERVATION
  Assistant asked "Which stylist?" + "Sarah"                → CREATE_APPOINTMENT
  Assistant asked "Which membership?" + "Gold"              → CREATE_APPOINTMENT
Cold-start bare names without a preceding assistant ask still follow COLD-START rules.

AVAILABILITY BROWSE (overrides slot-fill continuation):
When the user is navigating previously presented times — "next", "show more",
"more", "previous", "show previous", "back", or equivalent "are there more times"
phrasing — classify as AVAILABILITY even if last_intent is CREATE_APPOINTMENT.
This remains AVAILABILITY after the assistant said there are no more times /
browse is exhausted: repeating the same browse request is still AVAILABILITY
browse, not unrecognized in-flow gibberish and not a new booking verb.
  last_intent=CREATE_APPOINTMENT + "show more"              → AVAILABILITY
  last_intent=CREATE_APPOINTMENT + "Are there more times?"  → AVAILABILITY
  (even when prior assistant said "no more available times") → AVAILABILITY

CORRECTION (active booking context only):
When context shows an active booking intent AND user replaces/corrects a slot
("actually X", "make it X instead", "wait I meant X"):
→ CORRECTION

{confirm_action_dialog_act_section()}
{reject_action_dialog_act_section()}
BOOKING VERB RULE:
An explicit booking verb (book, schedule, reserve, cancel, modify, change, reschedule) is
sufficient to classify the intent — even when the service is generic or unspecified.
Service resolution is Stage 2's responsibility. Never return UNKNOWN when a booking verb is present.
Exception (pending proposal only): when CONVERSATION CONTEXT shows a pending proposed
action awaiting confirmation, short authorize-the-proposal imperatives ("book it",
"reserve it", "schedule it") are CONFIRM_ACTION per the dialog-act rule above — not
CREATE_*. Cold start / no pending proposal is unchanged.

GENERAL_INQUIRY vs OFF_TOPIC vs UNKNOWN:
  GENERAL_INQUIRY — business-scoped FAQ (hours, location, policies, payments, store info).
  OFF_TOPIC — coherent request outside this business (world knowledge, jokes, unrelated tech).
  UNKNOWN — not understood (gibberish, bare fragments with no clear act).
  "where are you located?"              → GENERAL_INQUIRY
  "how much is a premium haircut?"      → QUOTE (or GENERAL_INQUIRY if no price intent)
  "who is the president of Nigeria?"    → OFF_TOPIC
  "tell me a joke"                      → OFF_TOPIC
  "explain Java virtual threads"        → OFF_TOPIC
  "aaa"                                 → UNKNOWN

COLD-START examples (no CONVERSATION CONTEXT):
  "haircut tomorrow"               → UNKNOWN  (no booking verb)
  "friday"                         → UNKNOWN  (bare weekday, no context)
  "book haircut at 10am"           → CREATE_APPOINTMENT
  "book it"                        → CREATE_APPOINTMENT  (no pending proposal → not CONFIRM_ACTION)
  "i want to book a service"       → CREATE_APPOINTMENT  (booking verb present; service unspecified)
  "book something for friday"      → CREATE_APPOINTMENT  (booking verb present; service unspecified)
  "i'd like to make a reservation" → CREATE_RESERVATION  (booking verb present)
  "cancel my booking"              → CANCEL_BOOKING
  "what services do you have"      → DISCOVERY
  "who is the president of Nigeria?" → OFF_TOPIC
  "aaa"                            → UNKNOWN"""


def get_tool() -> dict:
    return _TOOL
