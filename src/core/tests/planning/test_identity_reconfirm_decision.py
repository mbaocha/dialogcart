"""Stage 08 identity-resolved reconfirm Decision rule."""

from core.planning.pipeline.stage08_decision_plan import (
    _identity_clarification_requires_reconfirm,
)


def test_identity_reconfirm_only_after_needs_clarification_with_customer():
    base = dict(
        action="CONFIRM_APPOINTMENT",
        missing_slots=[],
        confirmation_state="pending",
    )
    assert _identity_clarification_requires_reconfirm(
        session_state={"status": "NEEDS_CLARIFICATION", "customer_id": 9},
        **base,
    )
    assert not _identity_clarification_requires_reconfirm(
        session_state={"status": "AWAITING_CONFIRMATION", "customer_id": 9},
        **base,
    )
    assert not _identity_clarification_requires_reconfirm(
        session_state={"status": "NEEDS_CLARIFICATION"},
        **base,
    )
