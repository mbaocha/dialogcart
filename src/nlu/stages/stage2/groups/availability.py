"""
Stage 2 group: AVAILABILITY — handles AVAILABILITY (maps to CHECK_AVAILABILITY in policy).

Slot focus: service_term + canonical temporal + browse operation when paginating.
Alias resolution is owned by pipeline catalog.resolve_service (same as CREATE).
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
from ...shared.context import format_conversation_context
from ....temporal.stage2_output import (
    empty_temporal_dict,
    materialize_temporal_ownership,
)

logger = logging.getLogger(__name__)

_MODEL = "claude-haiku-4-5-20251001"

_TOOL = build_tool(
    name="extract_availability_slots",
    description="Extract slots for AVAILABILITY intent (checking free slots).",
    facts_fields=["service_term"],
    include_temporal=True,
    include_validated_intent=True,
    include_service_candidates=True,
    include_operation=True,
)

_VALID_OPERATIONS = frozenset({"browse_next", "browse_previous"})


def _operation_rules() -> str:
    return """── AVAILABILITY OPERATION ───────────────────────────────────────────────────
Set operation when the user is navigating previously presented availability — not
requesting a new search. Otherwise leave operation null.

browse_next — user wants more times from a prior result:
  "show more", "show me more times", "are there other slots?", "anything later?",
  "what else do you have?"
  → operation = "browse_next"
  → temporal must be null (no date/time extraction)

browse_previous — user wants earlier times from a prior result:
  "go back", "previous times", "show earlier"
  → operation = "browse_previous"
  → temporal must be null

New availability queries (dates, services, "what times are free") → operation = null."""


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

Current date/time (tenant-local): {now}
{ctx_section}
{intent_validation_section(candidate_intent)}

The user is asking about available time slots, not booking yet.
Extract which service, date, and time window they want to check.

{_operation_rules()}

{service_rules(aliases)}

{temporal_rules(now)}"""


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


def _normalize_operation(raw: Any) -> Optional[str]:
    if raw is None:
        return None
    operation = str(raw).strip().lower().replace("-", "_")
    if operation in _VALID_OPERATIONS:
        return operation
    return None


def _merge(raw: Dict[str, Any], candidate_intent: str) -> Dict[str, Any]:
    validated = raw.get("validated_intent") or candidate_intent
    confidence = float(raw.get("confidence", 0.8))
    facts = raw.get("facts") or {}
    operation = _normalize_operation(raw.get("operation"))
    temporal, temporal_facts, time_constraint = materialize_temporal_ownership(
        raw, confidence=confidence
    )
    result = {
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
        "service_candidates": raw.get("service_candidates") or [],
        "temporal": temporal,
    }
    if operation is not None:
        result["operation"] = operation
    return result


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
        "service_term": None,
        "time_constraint": None,
        "search_query": None,
        "temporal": empty_temporal_dict(0.0),
    }
