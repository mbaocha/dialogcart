"""
OFF_TOPIC factual answer — structured world-knowledge for the renderer.

Produces OffTopicEvidence only. Not conversational wording, redirects, or FAQ.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Optional

from core.rendering.llm_client import get_anthropic_client, resolve_model

logger = logging.getLogger(__name__)

_DIAG = "[OFF_TOPIC_DIAG]"

_MAX_TOKENS = 128

_TOOL = {
    "name": "provide_factual_answer",
    "description": (
        "Return a brief, safe response to a common off-topic interruption, "
        "or mark it unanswerable when a short reply is not appropriate."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "answerable": {
                "type": "boolean",
                "description": (
                    "True when the request can be satisfied with a brief, safe "
                    "response (factual, explanatory, temporal, humorous, "
                    "inspirational, or lightly creative). "
                    "False for unsafe requests, personal opinions or speculation, "
                    "professional advice, or requests that are excessively broad "
                    "or unsuitable for a brief interruption."
                ),
            },
            "answer": {
                "type": ["string", "null"],
                "description": (
                    "Brief response when answerable (typically 1–2 sentences); "
                    "may be factual, explanatory, creative, humorous, or "
                    "inspirational. Null when not answerable. "
                    "No greetings, no redirects, no business pitch."
                ),
            },
        },
        "required": ["answerable", "answer"],
    },
}


@dataclass(frozen=True)
class OffTopicEvidence:
    """Structured world-knowledge evidence for OFF_TOPIC rendering."""

    answer: Optional[str]
    answerable: bool


def _empty_unanswerable() -> OffTopicEvidence:
    return OffTopicEvidence(answer=None, answerable=False)


def _diag_serialize_block(block: Any) -> dict[str, Any]:
    """Serialize one Anthropic content block for temporary diagnostics."""
    out: dict[str, Any] = {"type": getattr(block, "type", None)}
    if out["type"] == "tool_use":
        out["name"] = getattr(block, "name", None)
        out["id"] = getattr(block, "id", None)
        out["input"] = getattr(block, "input", None)
    elif out["type"] == "text":
        out["text"] = getattr(block, "text", None)
    else:
        out["repr"] = repr(block)
    return out


def _diag_log_anthropic_response(response: Any, *, question: str) -> None:
    """Temporary diagnostics — raw tool response before parsing."""
    content = getattr(response, "content", []) or []
    serialized = [_diag_serialize_block(block) for block in content]
    logger.info(
        "%s question=%r raw response.content=%s",
        _DIAG,
        question,
        json.dumps(serialized, default=str, ensure_ascii=False),
    )
    for block in content:
        if getattr(block, "type", None) != "tool_use":
            continue
        logger.info(
            "%s tool_use block name=%r id=%r input=%s",
            _DIAG,
            getattr(block, "name", None),
            getattr(block, "id", None),
            json.dumps(getattr(block, "input", None), default=str, ensure_ascii=False),
        )


def _parse_tool_input(raw: Any) -> OffTopicEvidence:
    if not isinstance(raw, dict):
        return _empty_unanswerable()
    answerable = bool(raw.get("answerable"))
    answer = raw.get("answer")
    if isinstance(answer, str):
        answer = answer.strip() or None
    else:
        answer = None
    if not answerable or not answer:
        return OffTopicEvidence(answer=None, answerable=False)
    return OffTopicEvidence(answer=answer, answerable=True)


def answer_off_topic(question: str, *, client: Any = None) -> OffTopicEvidence:
    """
    Produce a concise factual answer for a canonical OFF_TOPIC question.

    Returns structured evidence only — never user-facing wording or redirects.
    """
    q = (question or "").strip()
    if not q:
        return _empty_unanswerable()

    system = (
        "Provide a brief, safe response to common off-topic interruptions.\n"
        "\n"
        "This may be:\n"
        "- a factual answer,\n"
        "- a short creative response,\n"
        "- a simple explanation,\n"
        "- a brief joke,\n"
        "- or a short quote.\n"
        "\n"
        "Use general world knowledge and straightforward reasoning when appropriate.\n"
        "Use simple temporal reasoning where applicable.\n"
        "If a question concerns a future event that has not yet occurred, answer "
        "that it has not yet happened rather than marking it unanswerable.\n"
        "\n"
        "Keep responses concise (typically 1–2 sentences).\n"
        "Do not greet, redirect, mention the booking, or add conversational filler.\n"
        "Do not role-play extensively, produce long essays, become a general-purpose "
        "assistant, or engage in extended conversations.\n"
        "\n"
        "Return answerable=false only for unsafe requests, requests requiring personal "
        "opinions or speculation, requests requiring professional advice, or requests "
        "that are excessively broad or unsuitable for a brief interruption."
    )
    user = f"Question:\n{q}"

    try:
        llm = get_anthropic_client(client)
        response = llm.messages.create(
            model=resolve_model(),
            max_tokens=_MAX_TOKENS,
            system=system,
            tools=[_TOOL],
            tool_choice={"type": "any"},
            messages=[{"role": "user", "content": user}],
        )
    except RuntimeError as exc:
        logger.warning("%s RuntimeError for question=%r: %s", _DIAG, q, exc)
        logger.warning("answer_off_topic unavailable: %s", exc)
        return _empty_unanswerable()
    except Exception:
        logger.exception("%s exception for question=%r", _DIAG, q)
        logger.exception("answer_off_topic failed for question=%r", q[:120])
        return _empty_unanswerable()

    _diag_log_anthropic_response(response, question=q)

    for block in getattr(response, "content", []) or []:
        if getattr(block, "type", None) == "tool_use" and getattr(
            block, "name", None
        ) == "provide_factual_answer":
            parsed = _parse_tool_input(getattr(block, "input", None) or {})
            logger.info(
                "%s parsed answerable=%s answer=%r",
                _DIAG,
                parsed.answerable,
                parsed.answer,
            )
            return parsed

    logger.warning("%s no provide_factual_answer tool_use for question=%r", _DIAG, q)
    return _empty_unanswerable()
