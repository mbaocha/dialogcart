"""
Shared LLM wording boundary for rendering.

Takes a render_instruction and facts (from handlers, availability, recovery, and
other rendering callers) and produces user-facing text via Claude.

Callers own the instruction and facts; this module owns wording only.
Authoritative business knowledge comes from facts["structured_context"] when
present. Retrieved FAQ chunks are supporting evidence only.

Uses the shared rendering LLM client (``core.rendering.llm_client``).
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.rendering.llm_client import get_anthropic_client, resolve_model

logger = logging.getLogger(__name__)

_FALLBACK_TEXT = "I'm unable to respond right now. Please try again."
_MAX_TOKENS = 512

# Conversational principles for answers drawn from Business Knowledge.
# Does not alter structured_context — guidance only; the model owns presentation.
_BUSINESS_KNOWLEDGE_PRESENTATION = (
    "When answering from Business Knowledge, sound like an experienced "
    "front-desk colleague helping a customer in person.\n"
    "- Introduce the business naturally when it helps orientation, then present "
    "offerings so the customer can compare options and decide.\n"
    "- Surface decision-useful details when relevant — such as what each option "
    "is, what it costs, how long it takes, and when you are open — woven into "
    "clear, readable language rather than raw data dumps.\n"
    "- Prefer continuing the conversation here: invite the next useful step "
    "(for example choosing or booking something) instead of sending the "
    "customer elsewhere.\n"
    "- Share phone numbers, emails, or contact instructions only when the "
    "Current user request explicitly asks for contact information.\n"
    "- Never encourage calling or contacting the business when you can keep "
    "helping in this chat.\n"
    "Choose structure and wording that fit the vertical and the question; "
    "do not follow a fixed template."
)


@dataclass
class LlmRenderRequest:
    render_instruction: str
    facts: Dict[str, Any]
    conversation_history: List[Dict[str, str]] = field(default_factory=list)
    user_request: Optional[str] = None


def _format_structured_data(value: Any) -> str:
    """Render arbitrary structured data as indented JSON (stable, key-preserving)."""
    return json.dumps(value, default=str, ensure_ascii=False, indent=2)


def _build_system_prompt(structured_context: Dict[str, Any]) -> str:
    del structured_context  # identity/knowledge live in the user message sections
    lines = [
        "You are a helpful assistant for this business.",
        "Respond naturally and concisely.",
        "Business Knowledge is authoritative business information.",
        "Supporting Evidence provides additional retrieved context.",
        "If Business Knowledge and Supporting Evidence conflict, Business Knowledge wins.",
        "Do not invent business facts absent from Business Knowledge and Supporting Evidence.",
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
        "The Conversation, Current user request, Facts, Resume, Business Knowledge, "
        "Supporting Evidence, Availability, Recovery, and any other supplied sections "
        "are internal instructions for you only.\n"
        "Never mention, describe, compare, reconcile, or explain those internal sections.\n"
        "Never tell the user that supplied facts are relevant or irrelevant to the conversation.\n"
        "Never mention context mismatches.\n"
        "Never explain why you answered a question.\n"
        "Never describe your reasoning process.",
    ]
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

    # World-knowledge facts from Stage-2 OFF_TOPIC evidence (not business RAG).
    answer = request.facts.get("answer")
    if isinstance(answer, str) and answer.strip():
        parts.append(f"Facts:\n- {answer.strip()}")

    # Authoritative org facts — full structured_context, no field allowlist.
    structured_context = request.facts.get("structured_context")
    if isinstance(structured_context, dict) and structured_context:
        parts.append(
            "Business Knowledge (Authoritative):\n"
            + _format_structured_data(structured_context)
        )
        parts.append(_BUSINESS_KNOWLEDGE_PRESENTATION)

    # Retrieved chunks — supporting FAQ/RAG evidence only (not merged into knowledge).
    chunks = request.facts.get("chunks") or []
    evidence_lines = [
        f"- {c.get('content', '').strip()}"
        for c in chunks[:3]
        if isinstance(c, dict) and (c.get("content") or "").strip()
    ]
    if evidence_lines:
        parts.append("Supporting Evidence:\n" + "\n".join(evidence_lines))

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

    Browse-status and TIME_MATCH_MISMATCH requests are resolved deterministically
    so exhaustion / unavailable-time wording cannot collapse into a generic
    availability list from conversation history or facts alone.
    """
    facts = request.facts if isinstance(request.facts, dict) else {}
    browse_status = facts.get("browse_status")
    if isinstance(browse_status, str) and browse_status.strip():
        from core.rendering.availability_renderer import resolve_browse_status_text

        return resolve_browse_status_text(
            browse_status=browse_status,
            direction=str(facts.get("direction") or "next"),
            browse_hints=(
                facts.get("browse_hints")
                if isinstance(facts.get("browse_hints"), dict)
                else None
            ),
            search_date=(
                str(facts["search_date"])
                if facts.get("search_date") is not None
                else None
            ),
            recovery_actions=(
                facts.get("recovery_actions")
                if isinstance(facts.get("recovery_actions"), list)
                else None
            ),
        )

    # Unavailable presented-time clarification: keep wording deterministic so a
    # generic availability list cannot replace the mismatch explanation.
    time_resolution = facts.get("time_resolution")
    if isinstance(time_resolution, dict):
        outcome = time_resolution.get("outcome") or time_resolution.get("status")
        if outcome in ("TIME_MATCH_MISMATCH", "no_match"):
            from core.rendering.availability_renderer import resolve_time_mismatch_text

            availability = facts.get("availability")
            availability = availability if isinstance(availability, dict) else {}
            return resolve_time_mismatch_text(
                requested_time=(
                    str(time_resolution["requested_time"])
                    if time_resolution.get("requested_time") is not None
                    else None
                ),
                times=(
                    list(availability.get("times") or [])
                    if isinstance(availability.get("times"), list)
                    else None
                ),
                alternatives=(
                    list(time_resolution.get("alternatives") or [])
                    if isinstance(time_resolution.get("alternatives"), list)
                    else None
                ),
                mismatch_location=(
                    str(time_resolution["mismatch_location"])
                    if time_resolution.get("mismatch_location") is not None
                    else None
                ),
                search_date=(
                    str(availability["date"])
                    if availability.get("date") is not None
                    else (
                        str(availability["search_date"])
                        if availability.get("search_date") is not None
                        else None
                    )
                ),
                browse_hints=(
                    availability.get("browse_hints")
                    if isinstance(availability.get("browse_hints"), dict)
                    else None
                ),
                recovery_actions=(
                    time_resolution.get("recovery_actions")
                    if isinstance(time_resolution.get("recovery_actions"), list)
                    else (
                        availability.get("recovery_actions")
                        if isinstance(availability.get("recovery_actions"), list)
                        else None
                    )
                ),
            )

    try:
        sc = request.facts.get("structured_context") or {}
        if not isinstance(sc, dict):
            sc = {}
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
