"""Workflow-owned resume instructions from persisted session state."""

from copy import deepcopy

from core.rendering.workflow_resume import (
    build_resume_instruction,
    compose_pending_confirmation_resume,
)


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


def test_presented_offers_resume_without_awaiting_flag():
    """Presented availability + missing time ⇒ resume offers, not restart/date ask."""
    resume = build_resume_instruction(
        {
            "intent_name": "CREATE_APPOINTMENT",
            "missing_slots": ["date", "time"],
            "slots": {"service_id": "premium haircut"},
            "presented_availability": {
                "search_date": "2026-07-03",
                "times": ["10:00 AM", "11:30 AM", "2:00 PM"],
            },
        }
    )
    assert resume is not None
    lowered = resume.text.lower()
    assert "10:00 am" in lowered
    assert "11:30 am" in lowered
    assert "works best" in lowered
    assert "ask which date" not in lowered
    assert "restart" in lowered


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


def test_pending_confirmation_composes_canonical_prompt_from_planning(monkeypatch):
    session = {
        "confirmation_state": "pending",
        "planning": {
            "intent_name": "CREATE_APPOINTMENT",
            "slots": {
                "service_id": "integration spa treatment",
                "date": "2026-08-28",
                "time": "11:00",
            },
        },
        "conversation": {
            "history": [
                {
                    "role": "assistant",
                    "text": "The booking is for Thursday at 4:00 PM.",
                }
            ]
        },
    }
    original = deepcopy(session)
    calls = []

    def canonical(slots, *, entity_schema=None):
        calls.append((dict(slots), entity_schema))
        return (
            "You're about to book an Integration Spa Treatment on August 28 "
            "at 11:00 AM. Would you like me to go ahead?"
        )

    monkeypatch.setattr(
        "core.rendering.booking_confirmation_renderer.render_booking_confirmation_prompt",
        canonical,
    )
    rendered = compose_pending_confirmation_resume(
        session, "We're closed on weekends."
    )

    assert rendered == (
        "We're closed on weekends.\n\n"
        "You're about to book an Integration Spa Treatment on August 28 at "
        "11:00 AM. Would you like me to go ahead?"
    )
    assert calls == [(session["planning"]["slots"], None)]
    assert rendered.count("Would you like me to go ahead?") == 1
    assert "Thursday" not in rendered
    assert "is booked" not in rendered.lower()
    assert session == original


def test_pending_confirmation_missing_authoritative_evidence_uses_safe_fallback():
    session = {
        "confirmation_state": "pending",
        "planning": {"slots": {"service_id": 17}},
        "conversation": {
            "history": [{"role": "assistant", "text": "Booked for Thursday."}]
        },
    }
    assert compose_pending_confirmation_resume(session, "FAQ answer") == (
        "FAQ answer\n\nWould you like me to go ahead?"
    )


def test_pending_confirmation_composition_is_absent_for_other_states():
    assert compose_pending_confirmation_resume(
        {
            "confirmation_state": None,
            "planning": {
                "slots": {
                    "service_id": "spa",
                    "date": "2026-08-28",
                    "time": "11:00",
                }
            },
        },
        "FAQ answer",
    ) is None
