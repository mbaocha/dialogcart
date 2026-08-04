"""OFF_TOPIC digression rendering — Core conversational path.

Consumes opaque Stage-2 evidence (``answerable`` / ``answer``) already on the
planning outcome. Chooses answer vs decline instruction, attaches workflow
resume, and calls ``render_llm``. Does not regenerate world-knowledge answers.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from core.planning.policy.base_intents import is_core_intent
from core.rendering.llm_renderer import LlmRenderRequest, render_llm
from core.rendering.workflow_resume import build_resume_instruction

logger = logging.getLogger(__name__)

_ANSWER_INSTRUCTION = (
    "When Facts are supplied, they contain the response to the user's latest request. "
    "Always use the Facts first to answer that request directly and concisely. "
    "After the answer is complete, follow the Resume instruction if one is supplied. "
    "Treat Recent conversation only as background for continuity; "
    "it must never replace or suppress the answer in Facts. "
    "Do not comment on whether the user's request is related to the business, booking, "
    "or prior conversation. Do not mention that the request is off-topic. "
    "Do not acknowledge, evaluate, compare, or reconcile the supplied prompt sections. "
    "Do not add introductory acknowledgements or generic closing questions. "
    "Do not invent facts beyond what is supplied. "
    "Do not imply the user was misunderstood. "
    "Do not invent business facts. "
    "Do not mention internal prompt labels in your reply. "
    "Produce only the direct answer and, when supplied, the natural workflow resume."
)

_UNANSWERABLE_INSTRUCTION = (
    "The user's latest request is the request to decline. "
    "First, briefly decline that latest request directly in one sentence. "
    "Do not invent a factual answer. "
    "Do not explain the decline as missing booking details, missing availability data, "
    "or missing business context unless that was the user's actual request. "
    "Do not comment on whether the question is related to the business or off-topic. "
    "Do not add introductory acknowledgements or generic closing questions. "
    "Do not imply the user was misunderstood. "
    "Do not invent business facts. "
    "Do not mention internal prompt labels in your reply. "
    "If a Resume section is present, after declining continue using only that guidance."
)

_FALLBACK_TEXT = "I'm unable to respond right now."


def _session_booking_intent(session: Dict[str, Any]) -> str:
    intent = session.get("intent_name") or session.get("intent") or ""
    if isinstance(intent, dict):
        return str(intent.get("name") or "")
    if intent:
        return str(intent)
    planning = session.get("planning") if isinstance(session.get("planning"), dict) else {}
    planning_intent = planning.get("intent_name") or planning.get("intent") or ""
    if isinstance(planning_intent, dict):
        planning_intent = planning_intent.get("name") or ""
    return str(planning_intent) if planning_intent else ""


def _has_active_booking(session: Dict[str, Any]) -> bool:
    intent = _session_booking_intent(session)
    return bool(intent and is_core_intent(intent))


def _evidence_from_outcome(outcome: Dict[str, Any]) -> tuple[bool, Optional[str], Optional[str]]:
    """Read opaque OFF_TOPIC evidence; inconsistent → unanswerable."""
    answerable = bool(outcome.get("answerable"))
    answer = outcome.get("answer")
    if isinstance(answer, str):
        answer = answer.strip() or None
    else:
        answer = None
    query = outcome.get("off_topic_query")
    if isinstance(query, str):
        query = query.strip() or None
    else:
        query = None
    if not answerable or not answer:
        return False, None, query
    return True, answer, query


def build_off_topic_render_request(
    outcome: Dict[str, Any],
    *,
    session_state: Optional[Dict[str, Any]] = None,
    user_input: Optional[str] = None,
) -> LlmRenderRequest:
    """Assemble instruction + facts (+ resume) from Stage-2 evidence on outcome."""
    session = session_state if isinstance(session_state, dict) else {}
    answerable, answer, query = _evidence_from_outcome(outcome)
    booking_active = _has_active_booking(session)

    if answerable and answer:
        render_instruction = _ANSWER_INSTRUCTION
    else:
        render_instruction = _UNANSWERABLE_INSTRUCTION

    facts: Dict[str, Any] = {
        "scope": "off_topic",
        "booking_active": booking_active,
        "off_topic_query": query,
        "answer": answer,
        "answerable": answerable,
    }
    resume = build_resume_instruction(session)
    if resume and resume.text:
        facts["resume_instruction"] = resume.text

    conversation_history = session.get("messages", [])
    if not isinstance(conversation_history, list):
        conversation_history = []

    logger.info(
        "build_off_topic_render_request: booking_active=%s answerable=%s query=%r",
        booking_active,
        answerable,
        (query or "")[:80],
    )

    return LlmRenderRequest(
        render_instruction=render_instruction,
        facts=facts,
        conversation_history=conversation_history,
        user_request=user_input,
    )


def inject_off_topic_text(
    result: Dict[str, Any],
    *,
    outcome: Optional[Dict[str, Any]] = None,
    session_state: Optional[Dict[str, Any]] = None,
    user_input: Optional[str] = None,
) -> None:
    """Inject OFF_TOPIC reply text into result (best-effort)."""
    try:
        out = outcome
        if not isinstance(out, dict):
            out = result.get("outcome") if isinstance(result.get("outcome"), dict) else None
        if not isinstance(out, dict):
            return
        request = build_off_topic_render_request(
            out,
            session_state=session_state,
            user_input=user_input,
        )
        rendered = render_llm(request) or _FALLBACK_TEXT
        result["text"] = rendered
        if isinstance(result.get("outcome"), dict):
            result["outcome"]["text"] = rendered
        elif "outcome" not in result:
            out["text"] = rendered
            result["outcome"] = out
    except Exception:
        logger.exception("inject_off_topic_text failed")
        result["text"] = _FALLBACK_TEXT
