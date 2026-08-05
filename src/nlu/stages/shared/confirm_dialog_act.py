"""Shared CONFIRM_ACTION dialog-act boundary for Stage 1 and Stage 2.

Identical wording is intentional: Stage 1 often emits CONFIRM_ACTION with no
Stage 2 pass, and Stage 2 may independently select CONFIRM_ACTION when validating.
"""


def confirm_action_dialog_act_section() -> str:
    """Semantic rule: authorization vs questions about a pending proposed action."""
    return """CONFIRM_ACTION (dialog act — all workflows):
HARD CONSTRAINT: CONFIRM_ACTION is valid only when CONVERSATION CONTEXT contains an
assistant confirmation ask (go ahead / confirm / proceed). If that ask is absent
or context is empty, CONFIRM_ACTION is invalid — do not emit or promote to it.

With a pending confirmation ask, authorize that proposal:
  yes, yes please, go ahead, proceed, confirm, okay, sounds good, that's fine, do it.
  Pending-only override of booking verbs: "book it", "reserve it", "schedule it"
  → CONFIRM_ACTION (not CREATE_APPOINTMENT / CREATE_RESERVATION) for this turn only.
  Empty context + "book it" + Stage 1 CREATE_* → keep CREATE_* (not CONFIRM_ACTION).

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
