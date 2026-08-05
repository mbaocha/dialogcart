"""Shared SLOT-FILL continuation contract for Stage 1 and Stage 2 prompts.

Identical wording is intentional so digression recovery (FAQ / OFF_TOPIC /
invalid-input) cannot diverge between classifiers and extractors.
"""


def slot_fill_continuation_section() -> str:
    """Booking continuation + clock-form rules after digression/recovery."""
    return """SLOT-FILL CONTINUATION (active booking context only):
When CONVERSATION CONTEXT shows last_intent OR active_booking_intent is
CREATE_APPOINTMENT, CREATE_RESERVATION, or MODIFY_BOOKING, AND the user
does NOT express a new booking verb, correction, cancel, informational question,
or off-topic digression:
→ Continue that booking intent (NOT UNKNOWN, NOT CORRECTION, NOT CONFIRM_ACTION).
  Prefer active_booking_intent when it is set and last_intent is a digression
  (OFF_TOPIC, QUOTE, GENERAL_INQUIRY, DISCOVERY, DETAILS, or other FAQ/RAG) or
  an in-flow recovery turn. An intervening FAQ / OFF_TOPIC / unrecognized reply
  does NOT cancel slot-fill on a later clear slot-shaped utterance.

  last_intent=CREATE_APPOINTMENT + "tomorrow"         → CREATE_APPOINTMENT
  last_intent=CREATE_APPOINTMENT + "11am"             → CREATE_APPOINTMENT
  last_intent=CREATE_APPOINTMENT + "1.30" / "1:30" / "1.30pm" → CREATE_APPOINTMENT
  last_intent=CREATE_RESERVATION + "march 10 to 15"  → CREATE_RESERVATION
  last_intent=CREATE_APPOINTMENT + "premium"          → CREATE_APPOINTMENT  (service reply)
  last_intent=CREATE_APPOINTMENT + "the standard one" → CREATE_APPOINTMENT  (service reply)
  last_intent=CREATE_APPOINTMENT + "aaaa"             → CREATE_APPOINTMENT  (in-flow, no competing act)
  active_booking_intent=CREATE_APPOINTMENT + last_intent=QUOTE + "tomorrow at 5pm"
      → CREATE_APPOINTMENT (resume after FAQ; extract date/time)
  active_booking_intent=CREATE_APPOINTMENT + last_intent=OFF_TOPIC + "1.30"
      → CREATE_APPOINTMENT (resume after digression; extract time)
  last_intent=CREATE_APPOINTMENT after unrecognized "xxxxx" + "1.30"
      → CREATE_APPOINTMENT (resume after invalid-input recovery; extract time)
  last_intent=CREATE_APPOINTMENT + "tell me a joke"   → OFF_TOPIC  (off-topic digression)

Do NOT invent booking slots from the FAQ / OFF_TOPIC / digression utterance itself.
On a later clear slot-shaped reply, resume the active booking flow and extract the slot.
Do NOT refuse slot-fill solely because last_intent is QUOTE, GENERAL_INQUIRY,
DISCOVERY, DETAILS, or another FAQ/RAG intent when active_booking_intent is a
booking intent.

CLOCK FORMS (when last_intent OR active_booking_intent is a booking intent):
Unambiguous clock expressions such as "1.30", "1:30", "1.30pm", "13:30", "3pm"
→ booking slot-fill under that booking intent;
→ populate facts.times and temporal start_time (e.g. "1.30" / "1:30" → "01:30",
  "1.30pm" → "13:30", "3pm" → "15:00");
→ NEVER CONFIRM_ACTION merely because the previous assistant asked which time
  works best or re-showed available times.
A time-selection prompt ("Which time works best?", "Please choose one of these
available times", offered clock list) is NOT a confirmation ask.

Do NOT treat non-time decimals as clocks outside booking time slot-fill:
  "price is 1.30" / "rating is 1.30" / "I have 1.30 items" → not a booking time
  unless context clearly shows an active time-selection slot-fill and the
  utterance is answering that ask.

Do NOT apply without CONVERSATION CONTEXT (cold start).
Explicit booking verb in the utterance → classify normally, not slot-fill.
"""
