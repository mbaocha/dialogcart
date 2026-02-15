"""
Tests for outcome rendering (EXECUTED only).
"""

import pytest

from core.rendering.mapper.outcome_mapper import (
    derive_outcome_template_key_candidates,
    extract_intent,
)
from core.rendering.outcome_renderer import render_outcome


def test_render_executed_create_appointment_with_booking_code():
    """Test that EXECUTED CREATE_APPOINTMENT with booking_code returns text containing booking_code."""
    decision = {"intent_name": "CREATE_APPOINTMENT"}
    outcome = {
        "status": "EXECUTED",
        "intent_name": "CREATE_APPOINTMENT",
        "booking_code": "ABC123",
    }

    render_spec = render_outcome(decision, outcome)

    assert render_spec is not None
    assert render_spec.text is not None
    assert "ABC123" in render_spec.text
    assert (
        "confirmed" in render_spec.text.lower()
        or "appointment" in render_spec.text.lower()
    )


def test_render_executed_unknown_intent_falls_back_to_generic():
    """Test that EXECUTED with unknown intent falls back to OUTCOME__EXECUTED or OUTCOME."""
    decision = None
    outcome = {"status": "EXECUTED", "intent_name": "UNKNOWN_INTENT"}

    render_spec = render_outcome(decision, outcome)

    # Should fall back to generic template
    assert render_spec is not None
    assert render_spec.text is not None
    assert len(render_spec.text) > 0


def test_render_ready_returns_none():
    """Test that READY status returns None (no rendering)."""
    decision = {"intent_name": "CREATE_APPOINTMENT"}
    outcome = {"status": "READY", "intent_name": "CREATE_APPOINTMENT"}

    render_spec = render_outcome(decision, outcome)

    assert render_spec is None


def test_render_executed_missing_required_booking_code_falls_back():
    """Test that EXECUTED CREATE_APPOINTMENT without booking_code falls back to generic template."""
    decision = {"intent_name": "CREATE_APPOINTMENT"}
    outcome = {
        "status": "EXECUTED",
        "intent_name": "CREATE_APPOINTMENT",
        # booking_code is missing
    }

    render_spec = render_outcome(decision, outcome)

    # Should fall back to generic template (OUTCOME__EXECUTED or OUTCOME)
    assert render_spec is not None
    assert render_spec.text is not None
    assert len(render_spec.text) > 0


def test_render_executed_cancel_booking():
    """Test that EXECUTED CANCEL_BOOKING returns appropriate text."""
    decision = {"intent_name": "CANCEL_BOOKING"}
    outcome = {"status": "EXECUTED", "intent_name": "CANCEL_BOOKING"}

    render_spec = render_outcome(decision, outcome)

    assert render_spec is not None
    assert render_spec.text is not None
    assert (
        "cancelled" in render_spec.text.lower() or "cancel" in render_spec.text.lower()
    )


def test_render_executed_modify_booking():
    """Test that EXECUTED MODIFY_BOOKING returns appropriate text."""
    decision = {"intent_name": "MODIFY_BOOKING"}
    outcome = {"status": "EXECUTED", "intent_name": "MODIFY_BOOKING"}

    render_spec = render_outcome(decision, outcome)

    assert render_spec is not None
    assert render_spec.text is not None
    assert "updated" in render_spec.text.lower() or "modify" in render_spec.text.lower()


def test_extract_intent_from_decision():
    """Test intent extraction from decision.intent_name."""
    decision = {"intent_name": "CREATE_APPOINTMENT"}
    outcome = {}

    intent = extract_intent(decision, outcome)
    assert intent == "CREATE_APPOINTMENT"


def test_extract_intent_from_decision_plan():
    """Test intent extraction from decision.plan.intent_name."""
    decision = {"plan": {"intent_name": "CANCEL_BOOKING"}}
    outcome = {}

    intent = extract_intent(decision, outcome)
    assert intent == "CANCEL_BOOKING"


def test_extract_intent_from_outcome():
    """Test intent extraction from outcome.intent_name when decision is None."""
    decision = None
    outcome = {"intent_name": "MODIFY_BOOKING"}

    intent = extract_intent(decision, outcome)
    assert intent == "MODIFY_BOOKING"


def test_extract_intent_fallback_to_generic():
    """Test intent extraction falls back to GENERIC when no intent found."""
    decision = None
    outcome = {}

    intent = extract_intent(decision, outcome)
    assert intent == "GENERIC"


def test_derive_outcome_template_key_candidates_executed():
    """Test template key derivation for EXECUTED status."""
    decision = {"intent_name": "CREATE_APPOINTMENT"}
    outcome = {"status": "EXECUTED", "intent_name": "CREATE_APPOINTMENT"}

    candidates = derive_outcome_template_key_candidates(decision, outcome)

    assert len(candidates) > 0
    assert "OUTCOME__CREATE_APPOINTMENT__EXECUTED" in candidates
    assert "OUTCOME__EXECUTED" in candidates
    assert "OUTCOME" in candidates


def test_derive_outcome_template_key_candidates_not_executed():
    """Test template key derivation returns empty list for non-EXECUTED status."""
    decision = {"intent_name": "CREATE_APPOINTMENT"}
    outcome = {"status": "READY", "intent_name": "CREATE_APPOINTMENT"}

    candidates = derive_outcome_template_key_candidates(decision, outcome)

    assert candidates == []


def test_derive_outcome_template_key_candidates_generic_intent():
    """Test template key derivation for GENERIC intent (no intent-specific template)."""
    decision = None
    outcome = {"status": "EXECUTED"}

    candidates = derive_outcome_template_key_candidates(decision, outcome)

    # Should not include intent-specific template
    assert "OUTCOME__GENERIC__EXECUTED" not in candidates
    assert "OUTCOME__EXECUTED" in candidates
    assert "OUTCOME" in candidates
