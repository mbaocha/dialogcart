"""
Universal LLM Renderer

Single rendering point for all HANDLER_DELEGATED turns. Takes a render_instruction
and facts from any extension handler and produces user-facing text via Claude.

Extensions own the instruction; core owns the execution.
Business context is extracted from facts["structured_context"] when present.
"""

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

_FALLBACK_TEXT = "I'm unable to respond right now. Please try again."
_MODEL = os.getenv("LLM_RENDER_MODEL", "claude-haiku-4-5-20251001")
_MAX_TOKENS = 512


@dataclass
class LlmRenderRequest:
    render_instruction: str
    facts: Dict[str, Any]
    conversation_history: List[Dict[str, str]] = field(default_factory=list)


def _build_system_prompt(structured_context: Dict[str, Any]) -> str:
    business_name = structured_context.get("business_name") or "this business"
    lines = [f"You are a helpful assistant for {business_name}."]

    about = structured_context.get("business_about")
    if about:
        lines.append(about)

    phone = structured_context.get("business_phone")
    if phone:
        lines.append(f"Contact phone: {phone}")

    lines.append(
        "Respond naturally and concisely. "
        "Only use information from the provided context. "
        "Do not invent details not present in the evidence."
    )
    return "\n".join(lines)


def _build_user_message(request: LlmRenderRequest) -> str:
    parts = []

    # Recent conversation as context prefix (last 3 turns)
    history = request.conversation_history[-6:] if request.conversation_history else []
    if history:
        history_lines = [
            f"{m.get('role', '').capitalize()}: {m.get('text', '')}"
            for m in history
            if m.get("role") and m.get("text")
        ]
        if history_lines:
            parts.append("Recent conversation:\n" + "\n".join(history_lines))

    parts.append(request.render_instruction)

    # Evidence from chunks (top 3)
    chunks = request.facts.get("chunks") or []
    evidence_lines = [
        f"- {c.get('content', '').strip()}"
        for c in chunks[:3]
        if (c.get("content") or "").strip()
    ]
    if evidence_lines:
        parts.append("Evidence:\n" + "\n".join(evidence_lines))

    availability = request.facts.get("availability")
    if isinstance(availability, dict):
        avail_lines: List[str] = []
        service_name = availability.get("service_name")
        if service_name:
            avail_lines.append(f"Service: {service_name}")
        date_label = availability.get("date")
        if date_label:
            avail_lines.append(f"Date: {date_label}")
        times = availability.get("times") or []
        if times:
            avail_lines.append("Available times:")
            avail_lines.extend(f"- {t}" for t in times)
        more_count = availability.get("more_count") or 0
        if more_count:
            avail_lines.append(f"(+ {more_count} more times not shown)")
        if avail_lines:
            parts.append("Availability:\n" + "\n".join(avail_lines))

    return "\n\n".join(parts)


def render_llm(request: LlmRenderRequest) -> str:
    """
    Call Claude to produce user-facing text for a HANDLER_DELEGATED turn.

    Returns a safe fallback string on any failure — never raises.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY not set — returning fallback text")
        return _FALLBACK_TEXT

    try:
        import anthropic

        sc = request.facts.get("structured_context") or {}
        system_prompt = _build_system_prompt(sc)
        user_message = _build_user_message(request)

        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=_MODEL,
            max_tokens=_MAX_TOKENS,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
        return response.content[0].text.strip()

    except Exception as e:
        logger.error("LLM render failed: %s", e)
        return _FALLBACK_TEXT
