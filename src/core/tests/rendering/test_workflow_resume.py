"""Workflow-owned resume instructions from persisted session state."""

from core.rendering.workflow_resume import build_resume_instruction


def test_cold_start_resume_invites_booking():
    resume = build_resume_instruction({})
    assert resume is not None
    assert "invite" in resume.text.lower() or "book" in resume.text.lower()
    assert "time works best" not in resume.text.lower()


def test_service_disambiguation_resume_asks_service_only():
    resume = build_resume_instruction(
        {
            "intent_name": "CREATE_APPOINTMENT",
            "missing_slots": ["service_id", "date", "time"],
            "service_candidates": [
                {"text": "premium haircut"},
                {"text": "flexi haircut + pruning"},
            ],
        }
    )
    assert resume is not None
    lowered = resume.text.lower()
    assert "service" in lowered
    assert "premium haircut" in lowered
    assert "flexi haircut" in lowered
    assert "ask which date" not in lowered
    assert "time works best" not in lowered


def test_time_selection_awaiting_resume():
    resume = build_resume_instruction(
        {
            "intent_name": "CREATE_APPOINTMENT",
            "awaiting": "TIME_SELECTION",
            "slots": {"service_id": "premium haircut"},
        }
    )
    assert resume is not None
    assert "time" in resume.text.lower()


def test_confirmation_pending_resume():
    resume = build_resume_instruction(
        {
            "intent_name": "CREATE_APPOINTMENT",
            "confirmation_state": "pending",
            "slots": {"service_id": "premium haircut", "date": "2026-07-09", "time": "10:00"},
        }
    )
    assert resume is not None
    assert "confirm" in resume.text.lower()
