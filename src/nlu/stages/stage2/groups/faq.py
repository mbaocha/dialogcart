"""
Stage 2 group: FAQ — DISCOVERY, DETAILS, QUOTE, RECOMMENDATION, GENERAL_INQUIRY, OFF_TOPIC.

Slot focus: search_query for business FAQ intents; OFF_TOPIC uses off_topic_query
(canonical question) plus answerable/answer world-knowledge evidence, with
search_query null — never business RAG.
"""
import logging
import os
from typing import Any, Dict, Optional, Tuple

import anthropic

from ..base_prompt import build_tool, intent_validation_section, search_query_rules
from ...shared.context import format_conversation_context
from ....registry.intent_groups import RAG_INTENTS

logger = logging.getLogger(__name__)

_MODEL = "claude-haiku-4-5-20251001"

_TOOL = build_tool(
    name="extract_faq_slots",
    description=(
        "Extract search_query for business FAQ intents, or off_topic_query plus "
        "answerable/answer evidence for OFF_TOPIC, after Stage 2 intent validation."
    ),
    facts_fields=[],
    include_time_constraint=False,
    include_search_query=True,
    include_off_topic_query=True,
    include_validated_intent=True,
)


def _system_prompt(
    now: str,
    conversation_context: Optional[Dict[str, Any]],
    candidate_intent: str,
) -> str:
    ctx_block = format_conversation_context(conversation_context or {})
    ctx_section = f"\n{ctx_block}\n" if ctx_block else ""
    return f"""{intent_validation_section(candidate_intent)}

── EXTRACTION (FAQ / OFF_TOPIC) ────────────────────────────────────────────
Extract FAQ / off-topic fields for validated_intent only.

Current date/time: {now}
{ctx_section}
When validated_intent is a business FAQ (DISCOVERY, DETAILS, QUOTE, RECOMMENDATION,
GENERAL_INQUIRY):
  - produce a clean search_query noun phrase for RAG lookup
  - Do NOT extract dates, times, or service slots on the FAQ path
  - off_topic_query, answerable, and answer must be null

When validated_intent is OFF_TOPIC:
  - set search_query to null (never route off-topic into business FAQ RAG)
  - set off_topic_query to the user's question as a clear standalone question
    (preserve meaning; light capitalization/punctuation cleanup only)
  Example: "who is president of nigeria" → off_topic_query="Who is the president of Nigeria?"
  - also produce answerable and answer (world-knowledge evidence, not conversational wording):
    * answerable=true with a brief answer (typically 1–2 sentences) when a short safe
      response is appropriate (factual, explanatory, temporal, humorous, inspirational,
      or lightly creative)
    * answerable=false and answer=null for unsafe requests, personal opinions or
      speculation, professional advice, or requests that are excessively broad or
      unsuitable for a brief interruption
    * Do not greet, redirect, mention booking, or add conversational filler in answer
    * If a question concerns a future event that has not yet occurred, answer that it
      has not yet happened rather than marking it unanswerable

CONVERSATIONAL ANSWER vs FAQ EXTRACTION:
When INTENT VALIDATION set a booking/workflow intent because the user answered an
assistant prompt (offered values / unambiguous reference):
  → Set search_query to null (do not route answers into FAQ RAG).
Examples (extraction only — intent already decided above):
  Assistant offered services + "Executive Oil Change" → search_query null
  Assistant asked which engine + "Petrol" → search_query null

{search_query_rules()}"""


class FAQGroupExtractor:
    def __init__(self):
        self._client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    def extract(
        self,
        text: str,
        now: str,
        tenant_context: Dict[str, Any],
        candidate_intent: str,
        conversation_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        system = _system_prompt(now, conversation_context, candidate_intent)
        ctx_block = format_conversation_context(conversation_context or {})
        user_content = f"CURRENT USER MESSAGE:\n{text}" if ctx_block else text

        try:
            response = self._client.messages.create(
                model=_MODEL,
                max_tokens=384,
                system=system,
                tools=[_TOOL],
                tool_choice={"type": "any"},
                messages=[{"role": "user", "content": user_content}],
            )
        except Exception:
            logger.exception("FAQGroupExtractor failed for text=%r", text)
            return _empty(candidate_intent)

        for block in response.content:
            if block.type == "tool_use" and block.name == "extract_faq_slots":
                return _merge(block.input, candidate_intent)

        logger.warning("FAQGroupExtractor: no tool_use block for text=%r", text)
        return _empty(candidate_intent)


def _normalize_off_topic_query(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _normalize_off_topic_evidence(raw: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """Coerce Stage 2 answerable/answer; empty or inconsistent → unanswerable."""
    answerable = bool(raw.get("answerable"))
    answer = raw.get("answer")
    if isinstance(answer, str):
        answer = answer.strip() or None
    else:
        answer = None
    if not answerable or not answer:
        return False, None
    return True, answer


def _merge(raw: Dict[str, Any], candidate_intent: str) -> Dict[str, Any]:
    validated = raw.get("validated_intent") or candidate_intent
    search_query = raw.get("search_query")
    if validated not in RAG_INTENTS:
        search_query = None
    off_topic_query = None
    answerable = None
    answer = None
    if validated == "OFF_TOPIC":
        off_topic_query = _normalize_off_topic_query(raw.get("off_topic_query"))
        search_query = None
        answerable, answer = _normalize_off_topic_evidence(raw)
    return {
        "intent": validated,
        "proposal_response": raw.get("proposal_response"),
        "confidence": float(raw.get("confidence", 0.8)),
        "facts": {"dates": [], "times": [], "date_time_pairs": [], "service_id": None, "booking_id": None},
        "time_constraint": None,
        "search_query": search_query,
        "off_topic_query": off_topic_query,
        "answerable": answerable,
        "answer": answer,
    }


def _empty(candidate_intent: str) -> Dict[str, Any]:
    return {
        "intent": candidate_intent,
        "proposal_response": None,
        "confidence": 0.0,
        "facts": {"dates": [], "times": [], "date_time_pairs": [], "service_id": None, "booking_id": None},
        "time_constraint": None,
        "search_query": None,
        "off_topic_query": None,
        "answerable": None,
        "answer": None,
    }
