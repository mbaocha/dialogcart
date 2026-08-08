"""
Stage 2 group: CREATE — handles CREATE_APPOINTMENT, CREATE_RESERVATION, CORRECTION.

Slot focus: service_term + canonical temporal (legacy), or schema-driven fields
preserved on facts when compiled_entities is present (compiled once in the pipeline).
Bookable catalog phrase still feeds legacy service_term for Core-compatible resolve.
Legacy dates/times/time_constraint are projected from Temporal.
"""
import logging
import os
import re
from typing import Any, Dict, Optional

import anthropic

from ..base_prompt import (
    build_tool,
    declined_entities_rules,
    intent_candidate_section,
    intent_validation_instructions,
    service_rules,
    temporal_anchor_section,
    temporal_instructions,
)
from ..browse_operation import normalize_operation, operation_rules
from ..entity_schema import (
    CompiledBusinessEntities,
    bookable_item_phrase,
    extract_declared_facts,
)
from ..in_flow_validation import in_flow_act_validation_rules
from ..prompt_cache import cache_eligibility, log_usage, system_blocks
from ...shared.context import format_conversation_context
from ...shared.in_flow_act import promote_in_flow_booking_intent
from ...shared.slot_fill_continuation import slot_fill_continuation_section
from ....catalog import _try_pick_from_candidate_list
from ....temporal.stage2_output import (
    empty_temporal_dict,
    materialize_temporal_ownership,
)

logger = logging.getLogger(__name__)

_MODEL = "claude-haiku-4-5-20251001"

_CREATE_TOOL_NAME = "extract_create_slots"
_CREATE_TOOL_DESCRIPTION = (
    "Extract slots for CREATE_APPOINTMENT, CREATE_RESERVATION, or CORRECTION intents."
)

# Frozen kwargs for the legacy (no entity_schema) tool — must stay bit-identical.
_LEGACY_CREATE_TOOL_KWARGS = {
    "name": _CREATE_TOOL_NAME,
    "description": _CREATE_TOOL_DESCRIPTION,
    "facts_fields": ["service_term"],
    "include_temporal": True,
    "include_validated_intent": True,
    "include_operation": True,
}

_PENDING_TIME_REPLACEMENT_RE = re.compile(
    r"\b(?:make\s+it|change(?:\s+(?:it|the\s+time))?\s+to|"
    r"switch(?:\s+(?:it|the\s+time))?\s+to|move(?:\s+it)?\s+to)\s+"
    r"(?P<hour>[1-9]|1\d|2[0-3])\b",
    re.IGNORECASE,
)


def _repair_pending_confirmation_bare_hour(
    raw: Dict[str, Any],
    *,
    candidate_intent: str,
    text: str,
    conversation_context: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Bind an explicit replacement bare hour only for pending confirmation."""
    ctx = conversation_context if isinstance(conversation_context, dict) else {}
    validated = raw.get("validated_intent") or candidate_intent
    if validated != "CORRECTION" or ctx.get("confirmation_state") != "pending":
        return raw

    temporal = raw.get("temporal") if isinstance(raw.get("temporal"), dict) else {}
    facts = raw.get("facts") if isinstance(raw.get("facts"), dict) else {}
    if temporal.get("start_time") or temporal.get("end_time") or facts.get("times"):
        return raw

    match = _PENDING_TIME_REPLACEMENT_RE.search(text or "")
    if not match:
        return raw

    expression = match.group("hour")
    start_time = f"{int(expression):02d}:00"
    return {
        **raw,
        "temporal": {
            **temporal,
            "expression": expression,
            "start_time_expression": expression,
            "start_time": start_time,
            "mode": temporal.get("mode") or "none",
            "confidence": temporal.get("confidence", raw.get("confidence")),
        },
    }


def build_create_tool(
    compiled: Optional[CompiledBusinessEntities] = None,
) -> dict:
    """Request-scoped CREATE tool; identical to legacy when compiled is None."""
    if compiled is None:
        return build_tool(**_LEGACY_CREATE_TOOL_KWARGS)
    return build_tool(
        name=_CREATE_TOOL_NAME,
        description=_CREATE_TOOL_DESCRIPTION,
        facts_schema=compiled.facts_schema,
        include_temporal=True,
        include_validated_intent=True,
        include_operation=True,
        include_declined_entities=True,
    )


# Legacy alias for tests/callers that still reference create._TOOL (absent-schema shape).
_TOOL = build_create_tool(None)


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


def _service_candidate_pick_guidance(
    conversation_context: Optional[Dict[str, Any]],
) -> str:
    """Prompt block when Core is awaiting a pick from offered service candidates."""
    ctx = conversation_context if isinstance(conversation_context, dict) else {}
    candidates = ctx.get("service_candidates")
    if not isinstance(candidates, list) or not candidates:
        return ""
    missing = ctx.get("missing_slots") or []
    if "service_id" not in list(missing):
        return ""
    listed = ", ".join(f'"{c}"' for c in candidates if c)
    if not listed:
        return ""
    return f"""── SERVICE CANDIDATE PICK ──────────────────────────────────────────────────
The bot offered these service options and is awaiting the user's choice: {listed}
A short reply that names or uniquely identifies one option (e.g. "Premium", "flexi")
IS a service mention — extract it EXACTLY as spoken into the service / service_term field.
Do NOT leave the service field null for such clarification replies."""


def _service_term_from_clarification_reply(
    text: str,
    service_term: Any,
    conversation_context: Optional[Dict[str, Any]],
) -> Any:
    """Fill service_term from a short clarification reply against offered candidates.

    CREATE Stage 2 sometimes leaves the service field null for bare picks like
    \"Premium\". Catalog list-pick already resolves that phrase once service_term
    is set — recover it from the utterance when candidates uniquely match.
    """
    if isinstance(service_term, str) and service_term.strip():
        return service_term
    ctx = conversation_context if isinstance(conversation_context, dict) else {}
    candidates = ctx.get("service_candidates")
    if not isinstance(candidates, list) or not candidates:
        return service_term
    missing = ctx.get("missing_slots") or []
    if "service_id" not in list(missing):
        return service_term
    utterance = (text or "").strip()
    if not utterance:
        return service_term
    keys = [str(c) for c in candidates if c]
    if _try_pick_from_candidate_list(utterance, keys):
        return utterance
    return service_term


def _prompt_blocks(
    now: str,
    tenant_context: Dict[str, Any],
    conversation_context: Optional[Dict[str, Any]],
    candidate_intent: str,
    compiled: Optional[CompiledBusinessEntities] = None,
) -> tuple[str, str]:
    aliases = tenant_context.get("aliases", {})
    # Alias maps are lookup vocabularies, not ranked catalog presentation.
    aliases = {key: aliases[key] for key in sorted(aliases, key=str.casefold)}
    booking_mode = tenant_context.get("booking_mode", "service")
    ctx_block = format_conversation_context(conversation_context or {})
    ctx_section = f"\n{ctx_block}\n" if ctx_block else ""
    if compiled is not None:
        business_block = compiled.prompt_rules
        declined_block = declined_entities_rules()
    else:
        business_block = service_rules(aliases)
        declined_block = ""
    candidate_block = _service_candidate_pick_guidance(conversation_context)
    candidate_section = f"\n{candidate_block}\n" if candidate_block else ""
    declined_section = f"\n{declined_block}\n" if declined_block else ""
    stable = f"""{intent_validation_instructions()}

── EXTRACTION (CREATE) ─────────────────────────────────────────────────────
Extract booking slots for validated_intent only.

{slot_fill_continuation_section()}

{_booking_mode_guidance(booking_mode)}

{operation_rules()}

{business_block}
{declined_section}
{temporal_instructions()}"""
    dynamic = f"""DYNAMIC REQUEST CONTEXT
{intent_candidate_section(candidate_intent)}
Current date/time (tenant-local): {now}
{ctx_section}
{in_flow_act_validation_rules(candidate_intent)}
{candidate_section}
TEMPORAL CONTINUATION:
When conversation context establishes a booking date and the current user supplies
only a time, retain that established date and pair it with the newly supplied time.
{temporal_anchor_section(now)}"""
    return stable, dynamic


def _system_prompt(
    now: str,
    tenant_context: Dict[str, Any],
    conversation_context: Optional[Dict[str, Any]],
    candidate_intent: str,
    compiled: Optional[CompiledBusinessEntities] = None,
) -> str:
    """Compatibility view of the ordered prompt blocks."""
    stable, dynamic = _prompt_blocks(
        now,
        tenant_context,
        conversation_context,
        candidate_intent,
        compiled=compiled,
    )
    return f"{stable}\n{dynamic}"


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
        compiled_entities: Optional[CompiledBusinessEntities] = None,
    ) -> Dict[str, Any]:
        # compiled_entities is validated once in NLUPipeline.run; CREATE does not recompile.
        compiled = compiled_entities

        stable_system, dynamic_system = _prompt_blocks(
            now,
            tenant_context,
            conversation_context,
            candidate_intent,
            compiled=compiled,
        )
        tool = build_create_tool(compiled)
        cache_ok, prefix_tokens, fingerprint = cache_eligibility(
            self._client,
            model=_MODEL,
            tool=tool,
            stable_text=stable_system,
        )
        system = system_blocks(stable_system, dynamic_system, eligible=cache_ok)
        ctx_block = format_conversation_context(conversation_context or {})
        user_content = f"CURRENT USER MESSAGE:\n{text}" if ctx_block else text

        logger.info(
            "[CREATE_STAGE2] candidate_intent=%r entity_schema=%s prefix=%s",
            candidate_intent,
            "present" if compiled is not None else "absent",
            fingerprint,
        )
        try:
            response = self._client.messages.create(
                model=_MODEL,
                max_tokens=512,
                system=system,
                tools=[tool],
                tool_choice={"type": "any"},
                messages=[{"role": "user", "content": user_content}],
            )
            log_usage(
                response,
                model=_MODEL,
                group="create",
                prefix=fingerprint,
                prefix_tokens=prefix_tokens,
                cache_eligible=cache_ok,
                cache_control_applied=cache_ok,
            )
        except Exception:
            logger.exception("CreateGroupExtractor failed")
            return _empty(candidate_intent, text, conversation_context)

        for block in response.content:
            if block.type == "tool_use" and block.name == _CREATE_TOOL_NAME:
                return _merge(
                    block.input,
                    candidate_intent,
                    text=text,
                    conversation_context=conversation_context,
                    compiled=compiled,
                )

        logger.warning("CreateGroupExtractor: no tool_use block")
        return _empty(candidate_intent, text, conversation_context)


def _normalize_declined_entities(
    raw: Any,
    *,
    compiled: Optional[CompiledBusinessEntities] = None,
) -> list:
    """Normalize top-level declined_entities to declared schema field names."""
    if not isinstance(raw, list):
        return []
    allowed = None
    if compiled is not None:
        allowed = {f.name for f in compiled.fields}
    out: list = []
    seen = set()
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            continue
        name = item.strip()
        if allowed is not None and name not in allowed:
            continue
        if name in seen:
            continue
        seen.add(name)
        out.append(name)
    return out


def _merge(
    raw: Dict[str, Any],
    candidate_intent: str,
    *,
    text: str = "",
    conversation_context: Optional[Dict[str, Any]] = None,
    compiled: Optional[CompiledBusinessEntities] = None,
) -> Dict[str, Any]:
    raw = _repair_pending_confirmation_bare_hour(
        raw,
        candidate_intent=candidate_intent,
        text=text,
        conversation_context=conversation_context,
    )
    validated = raw.get("validated_intent") or candidate_intent
    validated = promote_in_flow_booking_intent(
        validated, text, conversation_context
    )
    confidence = float(raw.get("confidence", 0.8))
    facts = raw.get("facts") or {}
    temporal, temporal_facts, time_constraint = materialize_temporal_ownership(
        raw, confidence=confidence, source_text=text
    )
    operation = normalize_operation(raw.get("operation"))
    if not isinstance(facts, dict):
        facts = {}

    entity_facts: Dict[str, Any] = {}
    if compiled is not None:
        entity_facts = extract_declared_facts(facts, compiled)
        # Clarification short-pick fills the bookable phrase when Stage 2 left it null.
        bookable = compiled.bookable_item_field
        if bookable is not None:
            filled = _service_term_from_clarification_reply(
                text, entity_facts.get(bookable.name), conversation_context
            )
            if filled is not None:
                entity_facts[bookable.name] = filled
        service_term = bookable_item_phrase(entity_facts, compiled)
    else:
        service_term = facts.get("service_term")
        service_term = _service_term_from_clarification_reply(
            text, service_term, conversation_context
        )

    declined_entities = _normalize_declined_entities(
        raw.get("declined_entities"), compiled=compiled
    )
    # Declined entities must not carry invented fact values.
    for name in declined_entities:
        if name in entity_facts:
            entity_facts[name] = None

    result = {
        "intent": validated,
        "confidence": confidence,
        "facts": {
            **temporal_facts,
            **entity_facts,
            "service_id": None,
            "booking_id": None,
        },
        "service_term": service_term,
        "time_constraint": time_constraint,
        "search_query": None,
        "service_candidates": [],
        "temporal": temporal,
        "declined_entities": declined_entities,
    }
    if operation is not None:
        result["operation"] = operation
    return result


def _empty(
    candidate_intent: str,
    text: str = "",
    conversation_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    intent = promote_in_flow_booking_intent(
        candidate_intent, text, conversation_context
    )
    service_term = _service_term_from_clarification_reply(
        text, None, conversation_context
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
        "service_term": service_term,
        "time_constraint": None,
        "search_query": None,
        "service_candidates": [],
        "temporal": empty_temporal_dict(0.0),
        "declined_entities": [],
    }
