"""
Response Builder

Renders structured outcome objects into user-facing messages.

This module handles:
- Template lookup by template_key
- Variable interpolation from outcome data
- Required fields validation
- Message formatting (text, buttons, etc.)

Constraints:
- Must consume outcome objects only
- Must not call Luma or business APIs
- Must not contain orchestration logic
"""

# Re-export from original location for backward compatibility
# The actual implementation remains in rendering/whatsapp_renderer.py
# This allows gradual migration while maintaining existing functionality
from core.rendering.whatsapp_renderer import render_outcome_to_whatsapp

__all__ = [
    "render_outcome_to_whatsapp",
]

