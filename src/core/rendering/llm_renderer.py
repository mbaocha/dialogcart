"""
Shared LLM wording boundary for rendering.

Takes a render_instruction and facts (from handlers, availability, recovery, and
other rendering callers) and produces user-facing text via Claude.

Callers own the instruction and facts; this module owns wording only.
Business context is extracted from facts["structured_context"] when present.

Uses the shared rendering LLM client (``core.rendering.llm_client``) — same client
as ``answer_off_topic``.
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.rendering.llm_client import get_anthropic_client, resolve_model

logger = logging.getLogger(__name__)

_FALLBACK_TEXT = "I'm unable to respond right now. Please try again."
_MAX_TOKENS = 512


@dataclass
class LlmRenderRequest:
    render_instruction: str
    facts: Dict[str, Any]
    conversation_history: List[Dict[str, str]] = field(default_factory=list)
    user_request: Optional[str] = None


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
        "Do not invent details beyond the provided context.\n"
        "When a Facts section is supplied, it contains the response to the user's latest request. "
        "Always answer using Facts first, directly and without preamble. "
        "After the answer is complete, follow the Resume instruction if one is supplied.\n"
        "Treat Recent conversation only as background for continuity. "
        "It must never replace or suppress the answer in Facts.\n"
        "Do not comment on whether the user's request is related to the business, booking, "
        "or prior conversation. Do not mention that the request is off-topic.\n"
        "Do not add introductory acknowledgements (for example "
        "\"I'd be happy to help\", \"I appreciate you sharing that\", or "
        "\"Based on the available information\"). "
        "Do not add generic closing questions (for example "
        "\"Is there anything else I can assist you with?\").\n"
        "Produce only the direct answer and, when supplied, the natural workflow resume.\n"
        "The Conversation, Current user request, Facts, Resume, Evidence, Availability, "
        "Recovery, and any other supplied sections are internal instructions for you only.\n"
        "Never mention, describe, compare, reconcile, or explain those internal sections.\n"
        "Never tell the user that supplied facts are relevant or irrelevant to the conversation.\n"
        "Never mention context mismatches.\n"
        "Never explain why you answered a question.\n"
        "Never describe your reasoning process."
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

    user_request = request.user_request
    if isinstance(user_request, str) and user_request.strip():
        parts.append(f"Current user request:\n{user_request.strip()}")

    # World-knowledge facts from answer_off_topic (not business RAG).
    answer = request.facts.get("answer")
    if isinstance(answer, str) and answer.strip():
        parts.append(f"Facts:\n- {answer.strip()}")

    # Retrieved chunks (top 3) — business FAQ / RAG
    chunks = request.facts.get("chunks") or []
    evidence_lines = [
        f"- {c.get('content', '').strip()}"
        for c in chunks[:3]
        if (c.get("content") or "").strip()
    ]
    if evidence_lines:
        parts.append("Evidence:\n" + "\n".join(evidence_lines))

    resume = request.facts.get("resume_instruction")
    if isinstance(resume, str) and resume.strip():
        parts.append(f"Resume:\n{resume.strip()}")

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
        backend_message = availability.get("backend_message")
        if isinstance(backend_message, str) and backend_message.strip():
            avail_lines.append(f"Service message: {backend_message.strip()}")
        elif availability.get("empty"):
            avail_lines.append("Available times: none")
        if avail_lines:
            parts.append("Availability:\n" + "\n".join(avail_lines))

    execution = request.facts.get("execution")
    if isinstance(execution, dict):
        parts.append(
            "Execution evidence:\n"
            + json.dumps(execution, default=str, ensure_ascii=False, indent=2)
        )

    recovery = request.facts.get("recovery")
    if isinstance(recovery, dict) and recovery:
        parts.append(
            "Recovery context:\n"
            + json.dumps(recovery, default=str, ensure_ascii=False, indent=2)
        )

    return "\n\n".join(parts)


def _provider_text_or_fallback(raw_text: Any) -> str:
    """Accept only non-empty provider strings; never coerce SDK/mock objects."""
    if not isinstance(raw_text, str):
        logger.warning(
            "LLM provider returned non-string text (%s) — returning fallback text",
            type(raw_text).__name__,
        )
        return _FALLBACK_TEXT
    rendered = raw_text.strip()
    if not rendered:
        logger.warning("LLM provider returned empty text — returning fallback text")
        return _FALLBACK_TEXT
    return rendered


def render_llm(request: LlmRenderRequest) -> str:
    """
    Call Claude to produce user-facing text from a render request.

    Returns a safe fallback string on any failure — never raises.
    """
    try:
        sc = request.facts.get("structured_context") or {}
        system_prompt = _build_system_prompt(sc)
        user_message = _build_user_message(request)

        client = get_anthropic_client()
        response = client.messages.create(
            model=resolve_model(),
            max_tokens=_MAX_TOKENS,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
        return _provider_text_or_fallback(response.content[0].text)

    except RuntimeError:
        logger.warning("ANTHROPIC_API_KEY not set — returning fallback text")
        return _FALLBACK_TEXT
    except Exception as e:
        logger.error("LLM render failed: %s", e)
        return _FALLBACK_TEXT
