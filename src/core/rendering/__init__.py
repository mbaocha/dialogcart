"""
Rendering Module

LLM-based rendering for user-facing messages.
"""

from .availability_renderer import (
    build_availability_render_request,
    build_presented_availability,
    summarize_availability_slots,
)
from .llm_renderer import LlmRenderRequest, render_llm

__all__ = [
    "LlmRenderRequest",
    "render_llm",
    "build_availability_render_request",
    "build_presented_availability",
    "summarize_availability_slots",
]
