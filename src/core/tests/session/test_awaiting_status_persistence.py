"""Session projection preserves AWAITING_CONFIRMATION distinctly."""

from core.session.persist import assemble_session_projection_fields


def test_awaiting_confirmation_status_not_collapsed_to_needs():
    outcome = {
        "intent_name": "CREATE_APPOINTMENT",
        "status": "AWAITING_CONFIRMATION",
        "slots": {
            "service_id": "premium haircut",
            "date": "2026-07-03",
            "time": "10:00",
        },
        "missing_slots": [],
    }
    session = assemble_session_projection_fields(
        outcome=outcome,
        outcome_status="AWAITING_CONFIRMATION",
        organization_id=1,
        merged_luma_response={
            "slots": outcome["slots"],
            "confirmation_state": "pending",
        },
        user_id="test-user",
    )
    assert session is not None
    assert session["status"] == "AWAITING_CONFIRMATION"
