"""
Rendering Module

Contains rendering modules for converting outcomes to user-facing messages:
- response_builder: Outcome-to-message rendering
"""

from dataclasses import dataclass

# Import RenderSpec from clarification_renderer to avoid duplication
from .clarification_renderer import RenderSpec, render_clarification
from .renderer import render

__all__ = ["RenderSpec", "render_clarification", "render"]
