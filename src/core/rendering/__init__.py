"""
Rendering Module

LLM-based rendering for user-facing messages.
"""

from .availability_renderer import (
    build_availability_no_more_render_request,
    build_availability_presentation,
    build_availability_render_request,
    build_presented_availability,
    build_presented_availability_page,
    compute_target_page_index,
    dedupe_availability_slots,
    summarize_availability_slots,
)
from .llm_renderer import LlmRenderRequest, render_llm

__all__ = [
    "LlmRenderRequest",
    "render_llm",
    "build_availability_no_more_render_request",
    "build_availability_presentation",
    "build_availability_render_request",
    "build_presented_availability",
    "build_presented_availability_page",
    "compute_target_page_index",
    "dedupe_availability_slots",
    "summarize_availability_slots",
]
