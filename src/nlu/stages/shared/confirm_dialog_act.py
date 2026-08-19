"""Shared CONFIRM_ACTION dialog-act boundary for Stage 1 and Stage 2.

Identical wording is intentional: Stage 1 often emits CONFIRM_ACTION with no
Stage 2 pass, and Stage 2 may independently select CONFIRM_ACTION when validating.
"""


def confirm_action_dialog_act_section() -> str:
    """Semantic rule: authorization vs questions about a pending proposed action."""
    return """CONFIRM_ACTION (dialog act — all workflows):
HARD CONSTRAINT: CONFIRM_ACTION requires BOTH a genuine pending assistant
confirmation ask in CONVERSATION CONTEXT AND semantic acceptance evidence in the
CURRENT USER MESSAGE. Words such as "confirm" in assistant text, system instructions,
formatted history, or a pending-request description are context only and never user
acceptance evidence. If either requirement is absent, do not emit or promote to it.

A pending profile request (including CUSTOMER_CONTACT_NAME) is slot-fill context,
not a confirmation ask. A plausible answer to it retains the active booking intent
and extracts the requested entity. A genuine competing user act keeps its own intent.
Names such as "Godswill Mbaocha" or "Maya", unrelated questions such as "Who is the
prime minister?", and questions such as "What are you confirming?" are not acceptance.

With a pending confirmation ask, authorize that proposal:
  yes, yes please, go ahead, proceed, confirm, okay, sounds good, that's fine, do it.
  Pending-only override of booking verbs: "book it", "reserve it", "schedule it"
  → CONFIRM_ACTION (not CREATE_APPOINTMENT / CREATE_RESERVATION) for this turn only.
  Empty context + "book it" → not CONFIRM_ACTION. If booking type (appointment vs
  reservation) is not identifiable, emit UNKNOWN — do not keep or guess a CREATE_*.

CORRECTION OVERRIDES AN AFFIRMATIVE PREFIX:
When an affirmative opening is followed by an explicit replacement or correction
of a workflow slot, the turn is CORRECTION — not CONFIRM_ACTION and not CREATE_*.
The affirmative clause does not authorize the stale proposal. Extract the corrected
slot in Stage 2 and require fresh confirmation for the revised proposal.
  "Yes, but make it 11." → CORRECTION (11 replaces the pending time; do not authorize 10).

Time-selection prompts are NOT confirmation asks:
  "Which time works best?", "Please choose one of these available times", offered
  clock lists, or resume-after-digression time prompts do NOT authorize CONFIRM_ACTION.
  Clock replies ("1.30", "1:30", "1.30pm", "10am", "13:30") after those asks are
  booking slot-fill — never CONFIRM_ACTION.

Meta-questions are never CONFIRM_ACTION (a pending ask does not change this):
Questions about state, correctness, saved/applied changes, whether the flow continues,
or consequences of confirming stay informational — never promote to CONFIRM_ACTION.
  Not CONFIRM_ACTION: "Did you update it?", "Did you note my correction?",
  "Did you apply my correction?", "Is everything correct now?",
  "Did you save that?", "Did you save my changes?", "Are we still proceeding?",
  "Are we still booking?", "What happens if I confirm?", "Before confirming…",
  "Can you explain that first?"
"""
