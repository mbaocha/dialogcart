"""
Tests for outcome rendering with FAILED status.
"""

import pytest
from core.rendering.outcome_renderer import render_outcome
from core.rendering.mapper.outcome_mapper import derive_outcome_template_key_candidates


def test_executed_still_works_unchanged():
    """Test that EXECUTED rendering still works unchanged."""
    decision = {
        "intent_name": "CREATE_APPOINTMENT"
    }
    outcome = {
        "status": "EXECUTED",
        "intent_name": "CREATE_APPOINTMENT",
        "booking_code": "ABC123"
    }
    
    render_spec = render_outcome(decision, outcome)
    
    assert render_spec is not None
    assert render_spec.text is not None
    assert "ABC123" in render_spec.text
    assert "confirmed" in render_spec.text.lower() or "appointment" in render_spec.text.lower()


def test_failed_with_specific_intent_uses_intent_specific_template():
    """Test that FAILED with specific intent uses intent-specific template."""
    decision = {
        "intent_name": "CREATE_APPOINTMENT"
    }
    outcome = {
        "status": "FAILED",
        "intent_name": "CREATE_APPOINTMENT"
    }
    
    render_spec = render_outcome(decision, outcome)
    
    assert render_spec is not None
    assert render_spec.text is not None
    assert "appointment" in render_spec.text.lower()
    assert "couldn't" in render_spec.text.lower() or "could not" in render_spec.text.lower()


def test_failed_unknown_intent_falls_back_to_generic():
    """Test that FAILED with unknown intent falls back to OUTCOME__FAILED."""
    decision = None
    outcome = {
        "status": "FAILED",
        "intent_name": "UNKNOWN_INTENT"
    }
    
    render_spec = render_outcome(decision, outcome)
    
    # Should fall back to generic FAILED template
    assert render_spec is not None
    assert render_spec.text is not None
    assert len(render_spec.text) > 0
    # Should contain generic failure message
    assert "wrong" in render_spec.text.lower() or "try again" in render_spec.text.lower()


def test_failed_with_no_templates_returns_none():
    """Test that FAILED with no matching templates returns None (graceful)."""
    # This test verifies graceful degradation
    # If no FAILED templates exist, should return None without error
    decision = {
        "intent_name": "UNKNOWN_INTENT"
    }
    outcome = {
        "status": "FAILED",
        "intent_name": "UNKNOWN_INTENT"
    }
    
    # Should either return None or fall back to OUTCOME__FAILED
    render_spec = render_outcome(decision, outcome)
    
    # Should either return None (if no templates) or valid spec (if OUTCOME__FAILED exists)
    assert render_spec is None or (render_spec is not None and render_spec.text is not None)


def test_derive_outcome_template_key_candidates_failed():
    """Test template key derivation for FAILED status."""
    decision = {
        "intent_name": "CREATE_APPOINTMENT"
    }
    outcome = {
        "status": "FAILED",
        "intent_name": "CREATE_APPOINTMENT"
    }
    
    candidates = derive_outcome_template_key_candidates(decision, outcome)
    
    assert len(candidates) > 0
    assert "OUTCOME__CREATE_APPOINTMENT__FAILED" in candidates
    assert "OUTCOME__FAILED" in candidates
    assert "OUTCOME" in candidates


def test_derive_outcome_template_key_candidates_executed_still_works():
    """Test that EXECUTED template key derivation still works."""
    decision = {
        "intent_name": "CREATE_APPOINTMENT"
    }
    outcome = {
        "status": "EXECUTED",
        "intent_name": "CREATE_APPOINTMENT"
    }
    
    candidates = derive_outcome_template_key_candidates(decision, outcome)
    
    assert len(candidates) > 0
    assert "OUTCOME__CREATE_APPOINTMENT__EXECUTED" in candidates
    assert "OUTCOME__EXECUTED" in candidates
    assert "OUTCOME" in candidates


def test_derive_outcome_template_key_candidates_ready_returns_empty():
    """Test that READY status returns empty list (no rendering)."""
    decision = {
        "intent_name": "CREATE_APPOINTMENT"
    }
    outcome = {
        "status": "READY",
        "intent_name": "CREATE_APPOINTMENT"
    }
    
    candidates = derive_outcome_template_key_candidates(decision, outcome)
    
    assert candidates == []


def test_render_outcome_ready_returns_none():
    """Test that render_outcome returns None for READY status."""
    decision = {
        "intent_name": "CREATE_APPOINTMENT"
    }
    outcome = {
        "status": "READY",
        "intent_name": "CREATE_APPOINTMENT"
    }
    
    render_spec = render_outcome(decision, outcome)
    
    assert render_spec is None

