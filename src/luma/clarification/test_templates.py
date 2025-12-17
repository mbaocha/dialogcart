"""
Tests for clarification template system.

Verifies:
- Every ClarificationReason has a template
- Missing data raises an error
- Rendered output matches expected string exactly
- No free-text clarifications can be produced
- Templates load from JSON configuration
"""

import pytest
import json
from pathlib import Path
from .reasons import ClarificationReason
from .models import Clarification
from .renderer import render_clarification


def _load_templates_from_json() -> dict:
    """Helper to load templates from JSON for testing."""
    current_file = Path(__file__)
    templates_path = current_file.parent.parent.parent.parent / \
        "templates" / "clarification.json"

    with open(templates_path, "r", encoding="utf-8") as f:
        return json.load(f)


def test_every_reason_has_template():
    """Test that every ClarificationReason has a corresponding template in JSON."""
    templates = _load_templates_from_json()

    for reason in ClarificationReason:
        assert reason.value in templates, (
            f"ClarificationReason.{reason.name} ({reason.value}) "
            f"does not have a template in clarification.json"
        )
        template_config = templates[reason.value]
        assert "template" in template_config, (
            f"Template for {reason.value} missing 'template' key"
        )
        assert "required_fields" in template_config, (
            f"Template for {reason.value} missing 'required_fields' key"
        )
        assert isinstance(template_config["required_fields"], list), (
            f"Template for {reason.value} has 'required_fields' that is not a list"
        )


def test_missing_required_fields_raises_error():
    """Test that missing required fields raise ValueError."""
    # Test with AMBIGUOUS_TIME_NO_WINDOW which requires "time"
    clarification = Clarification(
        reason=ClarificationReason.AMBIGUOUS_TIME_NO_WINDOW,
        data={}  # Missing "time"
    )

    with pytest.raises(ValueError) as exc_info:
        render_clarification(clarification)

    assert "Missing required fields" in str(exc_info.value)
    assert "time" in str(exc_info.value)


def test_missing_placeholder_in_data_raises_error():
    """Test that missing placeholder in data raises ValueError."""
    # Template has {{time}} but data doesn't have it
    clarification = Clarification(
        reason=ClarificationReason.AMBIGUOUS_TIME_NO_WINDOW,
        data={"other_field": "value"}  # Missing "time"
    )

    with pytest.raises(ValueError) as exc_info:
        render_clarification(clarification)

    assert "Placeholder" in str(
        exc_info.value) or "Missing required fields" in str(exc_info.value)


def test_ambiguous_time_no_window_rendering():
    """Test AMBIGUOUS_TIME_NO_WINDOW template rendering."""
    clarification = Clarification(
        reason=ClarificationReason.AMBIGUOUS_TIME_NO_WINDOW,
        data={"time": "3"}
    )

    result = render_clarification(clarification)
    assert result == "Do you mean 3am or 3pm?"


def test_missing_time_rendering():
    """Test MISSING_TIME template rendering."""
    clarification = Clarification(
        reason=ClarificationReason.MISSING_TIME,
        data={"service": "haircut"}
    )

    result = render_clarification(clarification)
    assert result == "What time would you like the haircut appointment?"


def test_missing_date_rendering():
    """Test MISSING_DATE template rendering."""
    clarification = Clarification(
        reason=ClarificationReason.MISSING_DATE,
        data={"service": "massage"}
    )

    result = render_clarification(clarification)
    assert result == "What day should I book the massage for?"


def test_missing_service_rendering():
    """Test MISSING_SERVICE template rendering."""
    clarification = Clarification(
        reason=ClarificationReason.MISSING_SERVICE,
        data={}
    )

    result = render_clarification(clarification)
    assert result == "Which service would you like to book?"


def test_locale_ambiguous_date_rendering():
    """Test LOCALE_AMBIGUOUS_DATE template rendering."""
    clarification = Clarification(
        reason=ClarificationReason.LOCALE_AMBIGUOUS_DATE,
        data={"date_text": "07/12"}
    )

    result = render_clarification(clarification)
    assert result == "Just to confirm — is this 07/12?"


def test_vague_date_reference_rendering():
    """Test VAGUE_DATE_REFERENCE template rendering."""
    clarification = Clarification(
        reason=ClarificationReason.VAGUE_DATE_REFERENCE,
        data={}
    )

    result = render_clarification(clarification)
    assert result == "Do you have a specific day in mind, or should I check availability for the whole period?"


def test_ambiguous_plural_weekday_rendering():
    """Test AMBIGUOUS_PLURAL_WEEKDAY template rendering."""
    clarification = Clarification(
        reason=ClarificationReason.AMBIGUOUS_PLURAL_WEEKDAY,
        data={}
    )

    result = render_clarification(clarification)
    assert result == "Which specific day should I use?"


def test_conflicting_signals_rendering():
    """Test CONFLICTING_SIGNALS template rendering."""
    clarification = Clarification(
        reason=ClarificationReason.CONFLICTING_SIGNALS,
        data={}
    )

    result = render_clarification(clarification)
    assert result == "Just to confirm — which date should I use?"


def test_ambiguous_date_multiple_rendering():
    """Test AMBIGUOUS_DATE_MULTIPLE template rendering."""
    clarification = Clarification(
        reason=ClarificationReason.AMBIGUOUS_DATE_MULTIPLE,
        data={}
    )

    result = render_clarification(clarification)
    assert result == "I found multiple dates. Which one should I use?"


def test_context_dependent_date_rendering():
    """Test CONTEXT_DEPENDENT_DATE template rendering."""
    clarification = Clarification(
        reason=ClarificationReason.CONTEXT_DEPENDENT_DATE,
        data={}
    )

    result = render_clarification(clarification)
    assert result == "Could you specify the exact date you have in mind?"


def test_unknown_reason_raises_error():
    """Test that unknown reason raises KeyError."""
    # Create a mock enum value that doesn't exist in templates
    class MockReason:
        value = "UNKNOWN_REASON"

    clarification = Clarification(
        reason=MockReason(),  # type: ignore
        data={}
    )

    with pytest.raises(KeyError) as exc_info:
        render_clarification(clarification)

    assert "No template found" in str(exc_info.value)
    assert "UNKNOWN_REASON" in str(exc_info.value)


def test_templates_load_from_json():
    """Test that templates are successfully loaded from JSON file."""
    templates = _load_templates_from_json()

    # Verify it's a dictionary
    assert isinstance(templates, dict)

    # Verify it has at least the expected number of templates
    assert len(templates) >= len(ClarificationReason)

    # Verify structure of a sample template
    sample_key = list(templates.keys())[0]
    sample_template = templates[sample_key]
    assert "template" in sample_template
    assert "required_fields" in sample_template
    assert isinstance(sample_template["template"], str)
    assert isinstance(sample_template["required_fields"], list)


def test_template_placeholders_replaced_correctly():
    """Test that all placeholders in template are replaced."""
    clarification = Clarification(
        reason=ClarificationReason.MISSING_TIME,
        data={"service": "facial treatment"}
    )

    result = render_clarification(clarification)
    # Verify no {{placeholders}} remain
    assert "{{" not in result
    assert "}}" not in result
    assert "facial treatment" in result


def test_data_values_are_stringified():
    """Test that non-string data values are converted to strings."""
    clarification = Clarification(
        reason=ClarificationReason.AMBIGUOUS_TIME_NO_WINDOW,
        data={"time": 3}  # Integer instead of string
    )

    result = render_clarification(clarification)
    assert result == "Do you mean 3am or 3pm?"


def test_deterministic_rendering():
    """Test that same input always produces same output."""
    clarification = Clarification(
        reason=ClarificationReason.MISSING_SERVICE,
        data={}
    )

    result1 = render_clarification(clarification)
    result2 = render_clarification(clarification)
    result3 = render_clarification(clarification)

    assert result1 == result2 == result3
    assert result1 == "Which service would you like to book?"


def test_no_free_text_clarifications():
    """Test that renderer only produces template-based output."""
    templates = _load_templates_from_json()
    all_reasons = list(ClarificationReason)

    for reason in all_reasons:
        # Create minimal valid data for each reason
        if reason == ClarificationReason.AMBIGUOUS_TIME_NO_WINDOW:
            data = {"time": "3"}
        elif reason == ClarificationReason.MISSING_TIME:
            data = {"service": "test"}
        elif reason == ClarificationReason.MISSING_DATE:
            data = {"service": "test"}
        elif reason == ClarificationReason.LOCALE_AMBIGUOUS_DATE:
            data = {"date_text": "07/12"}
        else:
            data = {}

        clarification = Clarification(reason=reason, data=data)
        result = render_clarification(clarification)

        # Verify result is based on template structure
        assert len(result) > 0
        assert "{{" not in result  # No unresolved placeholders
        assert "}}" not in result  # No unresolved placeholders
