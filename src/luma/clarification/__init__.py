"""
Clarification Template System

Centralized, deterministic, template-driven clarification system.
Maps ClarificationReason → reusable templates → rendered prompts.

Templates are loaded from JSON configuration at templates/clarification.json.
"""

from .reasons import ClarificationReason
from .models import Clarification
from .renderer import render_clarification

__all__ = [
    "ClarificationReason",
    "Clarification",
    "render_clarification",
]
