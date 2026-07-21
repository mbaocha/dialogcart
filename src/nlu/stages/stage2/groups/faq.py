"""
Stage 2 group: FAQ — DISCOVERY, DETAILS, QUOTE, RECOMMENDATION, GENERAL_INQUIRY, OFF_TOPIC.

Slot focus: search_query for business FAQ intents; OFF_TOPIC uses off_topic_query
(canonical question) with search_query null — never business RAG.
"""
import logging
import os
from typing import Any, Dict, Optional

import anthropic

from ..base_prompt import build_tool, intent_validation_section, search_query_rules
from ...shared.context import format_conversation_context
from ....registry.intent_groups import RAG_INTENTS

logger = logging.getLogger(__name__)

_MODEL = "claude-haiku-4-5-20251001"

_TOOL = build_tool(
    name="extract_faq_slots",
    description=(
        "Validate informational / off-topic intent and extract search_query "
        "for business FAQ intents (DISCOVERY, DETAILS, QUOTE, etc.), or "
        "off_topic_query for OFF_TOPIC."
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
    return f"""You are a slot extractor for a booking platform.
Validate whether this message is a business FAQ or outside business scope.

Current date/time: {now}
{ctx_section}
{intent_validation_section(candidate_intent)}

GENERAL_INQUIRY vs OFF_TOPIC (authoritative for this group):
  GENERAL_INQUIRY — about this business (services, pricing, policies, hours, location, FAQs).
  OFF_TOPIC — coherent request outside this business (world knowledge, jokes, unrelated topics).
  UNKNOWN — not understood (gibberish); prefer OFF_TOPIC when the request is coherent but out of scope.
  "what services do you offer?"           → DISCOVERY (or GENERAL_INQUIRY)
  "where are you located?"                → GENERAL_INQUIRY
  "who is the president of Nigeria?"      → OFF_TOPIC
  "tell me a joke"                        → OFF_TOPIC
  "explain Java virtual threads"          → OFF_TOPIC

The user is asking a question, not making a booking.
For business FAQ intents, produce a clean search_query noun phrase for RAG lookup.
For OFF_TOPIC:
  - set search_query to null (never route off-topic into business FAQ RAG)
  - set off_topic_query to the user's question as a clear standalone question
    (preserve meaning; light capitalization/punctuation cleanup only)
  Example: "who is president of nigeria" → off_topic_query="Who is the president of Nigeria?"
For all non-OFF_TOPIC intents, off_topic_query must be null.
Do NOT answer the question. Do NOT extract dates, times, or service slots.

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
                max_tokens=256,
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


def _merge(raw: Dict[str, Any], candidate_intent: str) -> Dict[str, Any]:
    validated = raw.get("validated_intent") or candidate_intent
    search_query = raw.get("search_query")
    if validated not in RAG_INTENTS:
        search_query = None
    off_topic_query = None
    if validated == "OFF_TOPIC":
        off_topic_query = _normalize_off_topic_query(raw.get("off_topic_query"))
        search_query = None
    return {
        "intent": validated,
        "confidence": float(raw.get("confidence", 0.8)),
        "facts": {"dates": [], "times": [], "date_time_pairs": [], "service_id": None, "booking_id": None},
        "time_constraint": None,
        "search_query": search_query,
        "off_topic_query": off_topic_query,
    }


def _empty(candidate_intent: str) -> Dict[str, Any]:
    return {
        "intent": candidate_intent,
        "confidence": 0.0,
        "facts": {"dates": [], "times": [], "date_time_pairs": [], "service_id": None, "booking_id": None},
        "time_constraint": None,
        "search_query": None,
        "off_topic_query": None,
    }
