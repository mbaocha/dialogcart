"""
Stage 2 group: MODIFY — handles MODIFY_BOOKING, MODIFY_RESERVATION.

Slot focus: booking_id + canonical temporal.
"""
import logging
import os
from typing import Any, Dict, Optional

import anthropic

from ..base_prompt import (
    booking_id_rules,
    build_tool,
    intent_validation_section,
    temporal_rules,
)
from ...shared.context import format_conversation_context
from ....temporal.stage2_output import (
    empty_temporal_dict,
    materialize_temporal_ownership,
)

logger = logging.getLogger(__name__)

_MODEL = "claude-haiku-4-5-20251001"

_TOOL = build_tool(
    name="extract_modify_slots",
    description="Extract slots for MODIFY_BOOKING or MODIFY_RESERVATION intents.",
    facts_fields=["booking_id"],
    include_temporal=True,
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
    return f"""You are a slot extractor for a booking platform.
Extract modification slots from the user message.

Current date/time (tenant-local): {now}
{ctx_section}
{intent_validation_section(candidate_intent)}

Modification requires booking_id (to identify the booking) plus new date/time.
If the user has not provided a booking_id yet, extract what date/time changes they want.

{booking_id_rules(tenant_context)}

{temporal_rules(now)}"""


class ModifyGroupExtractor:
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
            logger.exception("ModifyGroupExtractor failed for text=%r", text)
            return _empty(candidate_intent)

        for block in response.content:
            if block.type == "tool_use" and block.name == "extract_modify_slots":
                return _merge(block.input, candidate_intent)

        logger.warning("ModifyGroupExtractor: no tool_use block for text=%r", text)
        return _empty(candidate_intent)


def _merge(raw: Dict[str, Any], candidate_intent: str) -> Dict[str, Any]:
    validated = raw.get("validated_intent") or candidate_intent
    confidence = float(raw.get("confidence", 0.8))
    facts = raw.get("facts") or {}
    temporal, temporal_facts, time_constraint = materialize_temporal_ownership(
        raw, confidence=confidence
    )
    return {
        "intent": validated,
        "confidence": confidence,
        "facts": {
            **temporal_facts,
            "service_id": None,
            "booking_id": facts.get("booking_id"),
        },
        "time_constraint": time_constraint,
        "search_query": None,
        "temporal": temporal,
    }


def _empty(candidate_intent: str) -> Dict[str, Any]:
    return {
        "intent": candidate_intent,
        "confidence": 0.0,
        "facts": {
            "dates": [],
            "times": [],
            "date_time_pairs": [],
            "service_id": None,
            "booking_id": None,
        },
        "time_constraint": None,
        "search_query": None,
        "temporal": empty_temporal_dict(0.0),
    }
