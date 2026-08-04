"""Shared REJECT_ACTION dialog-act boundary for Stage 1 and Stage 2.

Identical wording is intentional: Stage 1 often emits REJECT_ACTION with no
Stage 2 pass, and Stage 2 may independently select REJECT_ACTION when validating.
"""


def reject_action_dialog_act_section() -> str:
    """Semantic rule: refuse pending proposal vs cancel an existing booking."""
    return """REJECT_ACTION (dialog act — all workflows):
REJECT_ACTION means the user refuses, dismisses, or withdraws authorization for
the currently pending proposed action (confirmation ask / go ahead / proceed).

HARD CONSTRAINT: Under a pending confirmation ask, dismissal of that proposal is
REJECT_ACTION — not CANCEL_BOOKING, not UNKNOWN, not CORRECTION.
  → REJECT_ACTION: "No", "Cancel that", "Never mind", "Not anymore", "Forget it",
    "Don't proceed", "I don't want to go ahead", "No thanks", "Stop", "Don't".

CANCEL_BOOKING remains cancellation of an existing booking (committed or identified):
  → CANCEL_BOOKING: "Cancel my booking", "Cancel booking ABC123",
    "Cancel tomorrow's confirmed appointment", "Cancel my reservation".
The word "cancel" alone must not determine intent — "cancel that" under a pending
confirmation ask refuses the proposal (REJECT_ACTION); "cancel my booking" targets
an existing booking (CANCEL_BOOKING).

Without a pending confirmation ask, bare refusal language is not REJECT_ACTION
unless context clearly shows a pending proposed action to dismiss.
"""
