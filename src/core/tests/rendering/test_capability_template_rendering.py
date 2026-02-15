"""
Tests for capability template rendering.
"""

from unittest.mock import MagicMock, patch

import pytest

from core.rendering.capability_renderer import (
    _render_capability_template,
    _render_payment_capability,
    render_capability,
)


def test_payment_capability_renders_template_text():
    """Test that payment capability renders template text."""
    # Mock the payment client and adapter
    mock_payment_client = MagicMock()
    mock_payment_client.get_payment_url.return_value = {
        "success": True,
        "data": {
            "has_payment_intent": True,
            "payment_url": "https://payment.example.com/pay/123",
        },
    }

    mock_payment_adapter = MagicMock()
    mock_payment_adapter.payment_client = mock_payment_client

    with patch(
        "core.rendering.capability_renderer.get_adapter",
        return_value=mock_payment_adapter,
    ):
        facts = {}
        slots = {"booking_code": "ABC123"}
        context = {"session_slots": {"booking_code": "ABC123"}, "session_facts": {}}

        text = _render_payment_capability(facts, slots, context)

        assert text is not None
        assert "30 minutes" in text
        assert "payment" in text.lower()
        assert "https://payment.example.com/pay/123" in text


def test_render_capability_template_payment():
    """Test that _render_capability_template renders payment template correctly."""
    data = {"payment_url": "https://payment.example.com/pay/123"}

    rendered = _render_capability_template("PAYMENT", data)

    assert rendered is not None
    assert "30 minutes" in rendered
    assert "payment" in rendered.lower()
    assert "https://payment.example.com/pay/123" in rendered


def test_render_capability_template_missing_template_falls_back():
    """Test that missing template falls back safely."""
    # This test verifies graceful degradation
    # If templates.yaml is missing, _render_capability_template should return None
    # and _render_payment_capability should fall back to hardcoded string

    # Mock the payment client and adapter
    mock_payment_client = MagicMock()
    mock_payment_client.get_payment_url.return_value = {
        "success": True,
        "data": {
            "has_payment_intent": True,
            "payment_url": "https://payment.example.com/pay/123",
        },
    }

    mock_payment_adapter = MagicMock()
    mock_payment_adapter.payment_client = mock_payment_client

    with patch(
        "core.rendering.capability_renderer.get_adapter",
        return_value=mock_payment_adapter,
    ):
        # Mock template loading to fail
        with patch(
            "core.rendering.capability_renderer._load_capability_templates",
            side_effect=FileNotFoundError,
        ):
            facts = {}
            slots = {"booking_code": "ABC123"}
            context = {"session_slots": {"booking_code": "ABC123"}, "session_facts": {}}

            text = _render_payment_capability(facts, slots, context)

            # Should fall back to hardcoded string
            assert text is not None
            assert "30 minutes" in text
            assert "payment" in text.lower()
            assert "https://payment.example.com/pay/123" in text


def test_render_capability_template_missing_required_field():
    """Test that missing required field returns None."""
    # Missing payment_url
    data = {}

    rendered = _render_capability_template("PAYMENT", data)

    # Should return None if required field missing
    assert rendered is None


def test_render_capability_non_payment_unchanged():
    """Test that non-payment capability behavior is unchanged."""
    status = "AWAITING_CAPABILITY"
    active_capability = "unknown_capability"
    facts = {}
    slots = {}

    render_spec = render_capability(status, active_capability, facts, slots)

    # Unknown capability should return None (unchanged behavior)
    assert render_spec is None


def test_render_capability_payment_with_template():
    """Test that render_capability returns template text for payment."""
    # Mock the payment client and adapter
    mock_payment_client = MagicMock()
    mock_payment_client.get_payment_url.return_value = {
        "success": True,
        "data": {
            "has_payment_intent": True,
            "payment_url": "https://payment.example.com/pay/123",
        },
    }

    mock_payment_adapter = MagicMock()
    mock_payment_adapter.payment_client = mock_payment_client

    with patch(
        "core.rendering.capability_renderer.get_adapter",
        return_value=mock_payment_adapter,
    ):
        status = "AWAITING_CAPABILITY"
        active_capability = "payment"
        facts = {}
        slots = {"booking_code": "ABC123"}
        context = {"session_slots": {"booking_code": "ABC123"}, "session_facts": {}}

        render_spec = render_capability(
            status, active_capability, facts, slots, context
        )

        assert render_spec is not None
        assert render_spec.text is not None
        assert "30 minutes" in render_spec.text
        assert "payment" in render_spec.text.lower()
        assert "https://payment.example.com/pay/123" in render_spec.text
