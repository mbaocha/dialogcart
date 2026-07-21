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
  This applies even when nothing can be extracted from the utterance — leave facts/temporal empty.
  last_intent=CREATE_APPOINTMENT + "premium"  → validated_intent=CREATE_APPOINTMENT
  last_intent=CREATE_APPOINTMENT + "9am"       → validated_intent=CREATE_APPOINTMENT
  last_intent=CREATE_APPOINTMENT + "aaaa"      → validated_intent=CREATE_APPOINTMENT, empty facts
  last_intent=CREATE_APPOINTMENT + "tell me a joke" → validated_intent=OFF_TOPIC
Do NOT invent booking slots the user did not mention."""
