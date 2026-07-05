"""
Stage 2 group: AVAILABILITY — handles AVAILABILITY (maps to CHECK_AVAILABILITY in policy).

Slot focus: service_id, date(s), time.
"""
import logging
import os
from typing import Any, Dict, Optional

import anthropic

from ..base_prompt import (
    build_tool,
    date_rules,
    intent_validation_section,
    service_rules,
    time_rules,
)
from ...shared.context import format_conversation_context

logger = logging.getLogger(__name__)

_MODEL = "claude-haiku-4-5-20251001"

_TOOL = build_tool(
    name="extract_availability_slots",
    description="Extract slots for AVAILABILITY intent (checking free slots).",
    facts_fields=["dates", "times", "date_time_pairs", "service_id"],
    include_time_constraint=True,
    include_validated_intent=True,
    include_service_candidates=True,
)


def _system_prompt(
    now: str,
    tenant_context: Dict[str, Any],
    conversation_context: Optional[Dict[str, Any]],
    candidate_intent: str,
) -> str:
    aliases = tenant_context.get("aliases", {})
    ctx_block = format_conversation_context(conversation_context or {})
    ctx_section = f"\n{ctx_block}\n" if ctx_block else ""
    return f"""You are a slot extractor for a booking platform.
Extract availability query slots from the user message.

Current date/time: {now}
{ctx_section}
{intent_validation_section(candidate_intent)}

The user is asking about available time slots, not booking yet.
Extract which service, date, and time window they want to check.

{service_rules(aliases)}

{date_rules(now)}

{time_rules()}"""


class AvailabilityGroupExtractor:
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
        system = _system_prompt(now, tenant_context, conversation_context, candidate_intent)
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
            logger.exception("AvailabilityGroupExtractor failed for text=%r", text)
            return _empty(candidate_intent)

        for block in response.content:
            if block.type == "tool_use" and block.name == "extract_availability_slots":
                return _merge(block.input, candidate_intent)

        logger.warning("AvailabilityGroupExtractor: no tool_use block for text=%r", text)
        return _empty(candidate_intent)


def _merge(raw: Dict[str, Any], candidate_intent: str) -> Dict[str, Any]:
    validated = raw.get("validated_intent") or candidate_intent
    facts = raw.get("facts") or {}
    return {
        "intent": validated,
        "confidence": float(raw.get("confidence", 0.8)),
        "facts": {
            "dates": facts.get("dates") or [],
            "times": facts.get("times") or [],
            "date_time_pairs": facts.get("date_time_pairs") or [],
            "service_id": facts.get("service_id"),
            "booking_id": None,
        },
        "time_constraint": raw.get("time_constraint"),
        "search_query": None,
        "service_candidates": raw.get("service_candidates") or [],
    }


def _empty(candidate_intent: str) -> Dict[str, Any]:
    return {
        "intent": candidate_intent,
        "confidence": 0.0,
        "facts": {"dates": [], "times": [], "date_time_pairs": [], "service_id": None, "booking_id": None},
        "time_constraint": None,
        "search_query": None,
    }
