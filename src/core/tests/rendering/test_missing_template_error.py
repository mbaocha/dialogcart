"""
Unit Test: Missing Template Error Handling

Tests that missing templates raise clear exceptions.
"""

from core.rendering import render_clarification
import pytest


def test_missing_template_raises_keyerror():
    """
    Test 4: Missing template fails fast
    
    Call renderer with a clarification_reason that has no template.
    Assert that a clear exception is raised.
    """
    # Use a reason that doesn't exist in clarifications.yaml
    unknown_reason = "UNKNOWN_REASON_THAT_DOES_NOT_EXIST"
    slots = {"service": "haircut"}
    
    # Assert that KeyError is raised with a clear message
    with pytest.raises(KeyError) as exc_info:
        render_clarification(unknown_reason, slots)
    
    # Assert the error message is clear and helpful
    error_message = str(exc_info.value)
    assert "UNKNOWN_REASON_THAT_DOES_NOT_EXIST" in error_message, \
        f"Expected error message to mention the unknown reason, got: {error_message}"
    assert "template" in error_message.lower() or "not found" in error_message.lower(), \
        f"Expected error message to mention template or 'not found', got: {error_message}"


def test_missing_required_fields_raises_valueerror():
    """
    Additional test: Missing required fields should raise ValueError.
    """
    # MISSING_TIME requires "service" field
    reason = "MISSING_TIME"
    slots = {}  # Missing required "service" field
    
    # Assert that ValueError is raised
    with pytest.raises(ValueError) as exc_info:
        render_clarification(reason, slots)
    
    # Assert the error message mentions missing fields
    error_message = str(exc_info.value)
    assert "service" in error_message.lower() or "required" in error_message.lower(), \
        f"Expected error message to mention missing required fields, got: {error_message}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

