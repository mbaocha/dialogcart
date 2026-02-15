"""
Clarification Template System

Centralized, deterministic, template-driven clarification system.
Maps ClarificationReason → reusable templates → rendered prompts.

Templates are loaded from JSON configuration at src/core/rendering/templates/clarification.json.
"""

from .models import Clarification
from .reasons import ClarificationReason
from .renderer import render_clarification

__all__ = [
    "ClarificationReason",
    "Clarification",
    "render_clarification",
]
