"""
Stage 2 group: FAQ — handles DISCOVERY, DETAILS, QUOTE, RECOMMENDATION, GENERAL_INQUIRY.

Slot focus: search_query only (RAG lookup string).
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
    description="Extract search_query for informational intents (DISCOVERY, DETAILS, QUOTE, etc.).",
    facts_fields=[],
    include_time_constraint=False,
    include_search_query=True,
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
Extract the search query from an informational user message.

Current date/time: {now}
{ctx_section}
{intent_validation_section(candidate_intent)}

The user is asking a question, not making a booking.
Produce a clean search_query noun phrase for RAG lookup.
Do NOT extract dates, times, or service slots — only search_query.

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


def _merge(raw: Dict[str, Any], candidate_intent: str) -> Dict[str, Any]:
    validated = raw.get("validated_intent") or candidate_intent
    search_query = raw.get("search_query")
    if validated not in RAG_INTENTS:
        search_query = None
    return {
        "intent": validated,
        "confidence": float(raw.get("confidence", 0.8)),
        "facts": {"dates": [], "times": [], "date_time_pairs": [], "service_id": None, "booking_id": None},
        "time_constraint": None,
        "search_query": search_query,
    }


def _empty(candidate_intent: str) -> Dict[str, Any]:
    return {
        "intent": candidate_intent,
        "confidence": 0.0,
        "facts": {"dates": [], "times": [], "date_time_pairs": [], "service_id": None, "booking_id": None},
        "time_constraint": None,
        "search_query": None,
    }
