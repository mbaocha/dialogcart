"""
Rendering package — conversational output for Core.

Owns wording, composition, and domain response families.
Does not own planning, orchestration, business policy, or session state.
"""

from .availability_renderer import (
    build_availability_browse_status_render_request,
    build_availability_no_more_render_request,
    build_availability_render_request,
)
from .llm_renderer import LlmRenderRequest, render_llm
from .off_topic import OffTopicEvidence, answer_off_topic
from .recovery_renderer import (
    RECOVERY_UNRECOGNIZED_INPUT,
    build_recovery_render_request,
)
from .workflow_resume import ResumeInstruction, build_resume_instruction

__all__ = [
    "LlmRenderRequest",
    "OffTopicEvidence",
    "RECOVERY_UNRECOGNIZED_INPUT",
    "ResumeInstruction",
    "answer_off_topic",
    "build_availability_browse_status_render_request",
    "build_availability_no_more_render_request",
    "build_availability_render_request",
    "build_recovery_render_request",
    "build_resume_instruction",
    "render_llm",
]
