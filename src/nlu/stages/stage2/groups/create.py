"""
Stage 2 group: CREATE — handles CREATE_APPOINTMENT, CREATE_RESERVATION, CORRECTION.

Slot focus: service_term + canonical temporal.
Legacy dates/times/time_constraint are projected from Temporal.
"""
import logging
import os
from typing import Any, Dict, Optional

import anthropic

from ..base_prompt import (
    build_tool,
    intent_validation_section,
    service_rules,
    temporal_rules,
)
from ..in_flow_validation import in_flow_act_validation_rules
from ...shared.context import format_conversation_context
from ...shared.in_flow_act import promote_in_flow_booking_intent
from ....temporal.stage2_output import (
    empty_temporal_dict,
    materialize_temporal_ownership,
)

logger = logging.getLogger(__name__)

_MODEL = "claude-haiku-4-5-20251001"

_TOOL = build_tool(
    name="extract_create_slots",
    description="Extract slots for CREATE_APPOINTMENT, CREATE_RESERVATION, or CORRECTION intents.",
    facts_fields=["service_term"],
    include_temporal=True,
    include_validated_intent=True,
)


def _booking_mode_guidance(booking_mode: str) -> str:
    if booking_mode == "reservation":
        return """BOOKING MODE: reservation (accommodation / multi-night stays)
- "book room", "reserve suite" → CREATE_RESERVATION (never UNKNOWN)
- Required: date range (check-in → check-out), not single date + time
- "book room march 5-10" → temporal.start_date + temporal.end_date (ISO)"""
    return """BOOKING MODE: service (timed appointments)
- "book haircut", "schedule massage" → CREATE_APPOINTMENT
- Appointments need service + date + time
- "book haircut at 10am" → temporal date fields null; start_time="10:00"  (date NOT invented)"""


def _system_prompt(
    now: str,
    tenant_context: Dict[str, Any],
    conversation_context: Optional[Dict[str, Any]],
    candidate_intent: str,
) -> str:
    aliases = tenant_context.get("aliases", {})
    booking_mode = tenant_context.get("booking_mode", "service")
    ctx_block = format_conversation_context(conversation_context or {})
    ctx_section = f"\n{ctx_block}\n" if ctx_block else ""
    return f"""You are a slot extractor for a booking platform.
Extract booking slots from the user message. You are also the final authority on intent.

Current date/time (tenant-local): {now}
{ctx_section}
{intent_validation_section(candidate_intent)}

{in_flow_act_validation_rules(candidate_intent)}

{_booking_mode_guidance(booking_mode)}

{service_rules(aliases)}

{temporal_rules(now)}"""


class CreateGroupExtractor:
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

        logger.debug(
            "[CREATE_PROMPT] system=\n%s\n[CREATE_USER] user=%r",
            system,
            user_content,
        )
        logger.info(
            "[CREATE_STAGE2] text=%r candidate_intent=%r",
            text,
            candidate_intent,
        )
        try:
            response = self._client.messages.create(
                model=_MODEL,
                max_tokens=512,
                system=system,
                tools=[_TOOL],
                tool_choice={"type": "any"},
                messages=[{"role": "user", "content": user_content}],
            )
        except Exception:
            logger.exception("CreateGroupExtractor failed for text=%r", text)
            return _empty(candidate_intent, text, conversation_context)

        for block in response.content:
            if block.type == "tool_use" and block.name == "extract_create_slots":
                return _merge(
                    block.input,
                    candidate_intent,
                    text=text,
                    conversation_context=conversation_context,
                )

        logger.warning("CreateGroupExtractor: no tool_use block for text=%r", text)
        return _empty(candidate_intent, text, conversation_context)


def _merge(
    raw: Dict[str, Any],
    candidate_intent: str,
    *,
    text: str = "",
    conversation_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    validated = raw.get("validated_intent") or candidate_intent
    validated = promote_in_flow_booking_intent(
        validated, text, conversation_context
    )
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
            "booking_id": None,
        },
        "service_term": facts.get("service_term"),
        "time_constraint": time_constraint,
        "search_query": None,
        "service_candidates": [],
        "temporal": temporal,
    }


def _empty(
    candidate_intent: str,
    text: str = "",
    conversation_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    intent = promote_in_flow_booking_intent(
        candidate_intent, text, conversation_context
    )
    return {
        "intent": intent,
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
