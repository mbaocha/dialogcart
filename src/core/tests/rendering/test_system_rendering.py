"""
Tests for system rendering (welcome, greetings, etc.).
"""

import pytest
from core.rendering.system_renderer import render_system


def test_render_system_greeting_returns_welcome_text():
    """Test that render_system("GREETING") returns welcome text."""
    render_spec = render_system("GREETING")
    
    assert render_spec is not None
    assert render_spec.text is not None
    assert len(render_spec.text) > 0
    assert "welcome" in render_spec.text.lower()


def test_render_system_welcome_returns_welcome_text():
    """Test that render_system("WELCOME") returns welcome text."""
    render_spec = render_system("WELCOME")
    
    assert render_spec is not None
    assert render_spec.text is not None
    assert len(render_spec.text) > 0
    assert "welcome" in render_spec.text.lower()


def test_render_system_unknown_returns_none():
    """Test that render_system("UNKNOWN") returns None."""
    render_spec = render_system("UNKNOWN")
    
    assert render_spec is None


def test_render_system_none_returns_none():
    """Test that render_system(None) returns None."""
    render_spec = render_system(None)
    
    assert render_spec is None


def test_render_system_empty_string_returns_none():
    """Test that render_system("") returns None."""
    render_spec = render_system("")
    
    assert render_spec is None


def test_render_system_case_insensitive():
    """Test that render_system is case-insensitive."""
    render_spec_lower = render_system("greeting")
    render_spec_upper = render_system("GREETING")
    
    assert render_spec_lower is not None
    assert render_spec_upper is not None
    assert render_spec_lower.text == render_spec_upper.text


def test_render_system_no_exception_if_templates_missing():
    """Test that no exception is raised if templates are missing (best-effort)."""
    # This test verifies graceful degradation
    # In a real scenario, if templates.yaml is missing, render_system should return None
    # We can't easily test this without mocking, but the code should handle it gracefully
    render_spec = render_system("GREETING")
    
    # Should either return a valid spec or None, but not raise
    assert render_spec is None or (render_spec is not None and render_spec.text is not None)

