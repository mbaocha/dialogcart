"""Stage 2 prompt block for in-flow booking act validation."""


def in_flow_act_validation_rules(candidate_intent: str) -> str:
    if candidate_intent != "UNKNOWN":
        return ""
    return """
IN-FLOW BOOKING ACT VALIDATION (candidate UNKNOWN only):
When CONVERSATION CONTEXT shows last_intent or active_booking_intent is
CREATE_APPOINTMENT, CREATE_RESERVATION, or MODIFY_BOOKING, AND the user
does NOT express a new booking verb, correction, cancel, informational question,
or off-topic digression:
→ Set validated_intent to the SAME booking intent from context (NOT UNKNOWN).
  Prefer active_booking_intent when last_intent is a digression (OFF_TOPIC, QUOTE,
  FAQ/RAG) or recovery turn. This applies even when nothing can be extracted from
  the utterance — leave facts/temporal empty only when the utterance has no slot
  material; clear clocks ("1.30", "1:30", "3pm") must still be extracted as times.
  last_intent=CREATE_APPOINTMENT + "premium"  → validated_intent=CREATE_APPOINTMENT
  last_intent=CREATE_APPOINTMENT + "9am"       → validated_intent=CREATE_APPOINTMENT
  last_intent=CREATE_APPOINTMENT + "1.30"      → validated_intent=CREATE_APPOINTMENT + time
  last_intent=CREATE_APPOINTMENT + "aaaa"      → validated_intent=CREATE_APPOINTMENT, empty facts
  last_intent=CREATE_APPOINTMENT + "tell me a joke" → validated_intent=OFF_TOPIC

AVAILABILITY BROWSE EXCEPTION (overrides in-flow continuation):
When the user is navigating previously presented times ("next", "show more",
"more", "previous", "show previous", "back", or equivalent more-times phrasing),
including after an assistant exhaustion / no-more-times reply:
→ validated_intent = AVAILABILITY and set operation = browse_next or browse_previous.
  Do NOT treat as unrecognized in-flow gibberish.
  Do NOT leave operation null for these utterances.

Do NOT invent booking slots the user did not mention.
Do NOT refuse slot-fill solely because last_intent is QUOTE/FAQ when
active_booking_intent is a booking intent."""
