"""
Rendering Module

Contains rendering modules for converting outcomes to user-facing messages:
- response_builder: Outcome-to-message rendering
"""

from dataclasses import dataclass

from .capability_renderer import render_capability

# Import RenderSpec from clarification_renderer to avoid duplication
from .clarification_renderer import RenderSpec, render_clarification
from .outcome_renderer import render_outcome
from .renderer import render
from .system_renderer import render_system

__all__ = [
    "RenderSpec",
    "render_clarification",
    "render",
    "render_capability",
    "render_outcome",
    "render_system",
]
