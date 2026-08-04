"""
Stage 2 group: VIEW — handles BOOKING_INQUIRY, PAYMENT, PAYMENT_STATUS.

Maps to VIEW_BOOKING execution intent in intent_policy.yaml.
Slot focus: booking_id only.
"""
import logging
import os
from typing import Any, Dict, Optional

import anthropic

from ..base_prompt import booking_id_rules, build_tool, intent_validation_section
from ...shared.context import format_conversation_context

logger = logging.getLogger(__name__)

_MODEL = "claude-haiku-4-5-20251001"

_TOOL = build_tool(
    name="extract_view_slots",
    description="Extract slots for BOOKING_INQUIRY, PAYMENT, or PAYMENT_STATUS intents.",
    facts_fields=["booking_id"],
    include_time_constraint=False,
    include_validated_intent=True,
)


def _system_prompt(
    now: str,
    tenant_context: Dict[str, Any],
    conversation_context: Optional[Dict[str, Any]],
    candidate_intent: str,
) -> str:
    ctx_block = format_conversation_context(conversation_context or {})
    ctx_section = f"\n{ctx_block}\n" if ctx_block else ""
    return f"""{intent_validation_section(candidate_intent)}

── EXTRACTION (VIEW) ───────────────────────────────────────────────────────
Extract booking reference slots for validated_intent only.

Current date/time: {now}
{ctx_section}
The user is asking about or managing an existing booking.
Extract the booking_id if they provide one. If not provided, leave it null.

{booking_id_rules(tenant_context)}"""


class ViewGroupExtractor:
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
                max_tokens=256,
                system=system,
                tools=[_TOOL],
                tool_choice={"type": "any"},
                messages=[{"role": "user", "content": user_content}],
            )
        except Exception:
            logger.exception("ViewGroupExtractor failed for text=%r", text)
            return _empty(candidate_intent)

        for block in response.content:
            if block.type == "tool_use" and block.name == "extract_view_slots":
                return _merge(block.input, candidate_intent)

        logger.warning("ViewGroupExtractor: no tool_use block for text=%r", text)
        return _empty(candidate_intent)


def _merge(raw: Dict[str, Any], candidate_intent: str) -> Dict[str, Any]:
    validated = raw.get("validated_intent") or candidate_intent
    facts = raw.get("facts") or {}
    return {
        "intent": validated,
        "confidence": float(raw.get("confidence", 0.8)),
        "facts": {
            "dates": [],
            "times": [],
            "date_time_pairs": [],
            "service_id": None,
            "booking_id": facts.get("booking_id"),
        },
        "time_constraint": None,
        "search_query": None,
    }


def _empty(candidate_intent: str) -> Dict[str, Any]:
    return {
        "intent": candidate_intent,
        "confidence": 0.0,
        "facts": {"dates": [], "times": [], "date_time_pairs": [], "service_id": None, "booking_id": None},
        "time_constraint": None,
        "search_query": None,
    }
