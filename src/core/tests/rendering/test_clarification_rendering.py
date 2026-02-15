"""
Unit test for clarification rendering.

Tests that clarification text is rendered correctly from templates.
"""

from core.rendering import RenderSpec, render_clarification
from core.rendering.mapper.clarification_mapper import derive_clarification_reason


def test_render_clarification_for_missing_time():
    """
    Test that a decision with NEEDS_CLARIFICATION and missing_slots=["time"]
    can be rendered correctly.
    """
    # Given: Decision with status=NEEDS_CLARIFICATION, missing_slots=["time"]
    decision = {"status": "NEEDS_CLARIFICATION", "missing_slots": ["time"]}

    # Derive clarification reason
    reason = derive_clarification_reason(decision)
    assert reason == "MISSING_TIME"

    # Render with slots (service is required by MISSING_TIME template)
    slots = {"service": "haircut"}

    result = render_clarification(reason, slots)

    # Expect: RenderSpec with rendered text
    assert isinstance(result, RenderSpec)
    # Use semantic assertions instead of exact string matching
    assert "time" in result.text.lower()
    assert "haircut" in result.text.lower()
