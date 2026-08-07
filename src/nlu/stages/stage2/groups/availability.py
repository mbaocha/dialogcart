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
    intent_candidate_section,
    intent_validation_instructions,
    service_rules,
    temporal_anchor_section,
    temporal_instructions,
)
from ..browse_operation import normalize_operation, operation_rules
from ..prompt_cache import cache_eligibility, log_usage, system_blocks
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


def _prompt_blocks(
    now: str,
    tenant_context: Dict[str, Any],
    conversation_context: Optional[Dict[str, Any]],
    candidate_intent: str,
) -> tuple[str, str]:
    aliases = tenant_context.get("aliases", {})
    ctx_block = format_conversation_context(conversation_context or {})
    ctx_section = f"\n{ctx_block}\n" if ctx_block else ""
    stable = f"""{intent_validation_instructions()}

── EXTRACTION (AVAILABILITY) ───────────────────────────────────────────────
Extract availability query slots for validated_intent only.

The user is asking about available time slots, not booking yet.
Extract which service, date, and time window they want to check.

{operation_rules()}

{service_rules(aliases)}

{temporal_instructions()}"""
    dynamic = f"""DYNAMIC REQUEST CONTEXT
{intent_candidate_section(candidate_intent)}
Current date/time (tenant-local): {now}
{temporal_anchor_section(now)}
{ctx_section}"""
    return stable, dynamic


def _system_prompt(
    now: str,
    tenant_context: Dict[str, Any],
    conversation_context: Optional[Dict[str, Any]],
    candidate_intent: str,
) -> str:
    """Compatibility view of the ordered prompt blocks."""
    stable, dynamic = _prompt_blocks(
        now, tenant_context, conversation_context, candidate_intent
    )
    return f"{stable}\n{dynamic}"


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
        stable_system, dynamic_system = _prompt_blocks(
            now, tenant_context, conversation_context, candidate_intent
        )
        cache_ok, prefix_tokens, fingerprint = cache_eligibility(
            self._client,
            model=_MODEL,
            tool=_TOOL,
            stable_text=stable_system,
        )
        system = system_blocks(stable_system, dynamic_system, eligible=cache_ok)
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
            log_usage(
                response,
                model=_MODEL,
                group="availability",
                prefix=fingerprint,
                prefix_tokens=prefix_tokens,
                cache_eligible=cache_ok,
                cache_control_applied=cache_ok,
            )
        except Exception:
            logger.exception("AvailabilityGroupExtractor failed")
            return _empty(candidate_intent)

        for block in response.content:
            if block.type == "tool_use" and block.name == "extract_availability_slots":
                return _merge(block.input, candidate_intent, text=text)

        logger.warning("AvailabilityGroupExtractor: no tool_use block")
        return _empty(candidate_intent)


def _merge(
    raw: Dict[str, Any],
    candidate_intent: str,
    *,
    text: str = "",
) -> Dict[str, Any]:
    validated = raw.get("validated_intent") or candidate_intent
    confidence = float(raw.get("confidence", 0.8))
    facts = raw.get("facts") or {}
    operation = normalize_operation(raw.get("operation"))
    temporal, temporal_facts, time_constraint = materialize_temporal_ownership(
        raw, confidence=confidence, source_text=text
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
