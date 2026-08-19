"""
Stage 2 dispatcher — routes a Stage 1 proposal to a group extractor.

Stage 1 supplies a candidate intent (proposal / prior only).
Each group extractor:
- Consumes the shared Intent Validation Contract (base_prompt.intent_validation_section)
- Independently validates the proposal → validated_intent (semantic authority)
- Extracts slots for the validated intent (group-specific extraction only)
- Owns temporal understanding via Temporal; projects legacy fields for compatibility

Routing rules:
- UNKNOWN → create group (first-pass extraction; validation may re-route)
- CONFIRM_ACTION / REJECT_ACTION → no extraction (pipeline handles directly)
- When validated_intent maps to a different Stage 2 group, Stage 3 re-runs
  the correct group extractor and replaces the Stage 2 output entirely.
"""
import logging
from typing import Any, Dict, Optional

from ...registry.intent_groups import get_stage2_group
from ...temporal.stage2_output import empty_temporal_dict
from .entity_schema import CompiledBusinessEntities
from .groups.availability import AvailabilityGroupExtractor
from .groups.cancel import CancelGroupExtractor
from .groups.create import CreateGroupExtractor
from .groups.faq import FAQGroupExtractor
from .groups.modify import ModifyGroupExtractor
from .groups.view import ViewGroupExtractor
from .semantic_validation import (
    canonical_confirmation_pending,
    validate_final_stage2_result,
)

logger = logging.getLogger(__name__)

_GROUP_EXTRACTORS = {
    "create":       CreateGroupExtractor,
    "modify":       ModifyGroupExtractor,
    "cancel":       CancelGroupExtractor,
    "availability": AvailabilityGroupExtractor,
    "view":         ViewGroupExtractor,
    "faq":          FAQGroupExtractor,
}

# Stage 2 output-contract capability. These are the only group extractors that
# accept ``compiled_entities`` and therefore must emit strict typed mention
# evidence for schema-backed entity resolution.
_SCHEMA_ENTITY_GROUPS = frozenset({"create", "availability"})

# Instantiated lazily per group — one instance per worker process
_instances: Dict[str, Any] = {}


def _get_extractor(group: str):
    if group not in _instances:
        cls = _GROUP_EXTRACTORS[group]
        _instances[group] = cls()
    return _instances[group]


def _ensure_temporal(result: Dict[str, Any]) -> Dict[str, Any]:
    """Groups that own Temporal already set it; others get an empty Temporal."""
    if isinstance(result.get("temporal"), dict):
        return result
    return {**result, "temporal": empty_temporal_dict(float(result.get("confidence") or 0.0))}


def _empty_result(intent: str) -> Dict[str, Any]:
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
        "off_topic_query": None,
        "answerable": None,
        "answer": None,
    }


def _resolve_routing_group(intent: str) -> Optional[str]:
    """Map Stage 1 intent to the Stage 2 group for the first extraction pass."""
    if intent == "UNKNOWN":
        return "create"
    return get_stage2_group(intent)


def _pending_confirmation_initial_intent(
    intent: str,
    conversation_context: Optional[Dict[str, Any]],
) -> str:
    """Route legacy dialogue candidates through the pending workflow's Stage 2 group."""
    if intent not in {"CONFIRM_ACTION", "REJECT_ACTION"}:
        return intent
    if not canonical_confirmation_pending(conversation_context):
        return intent

    ctx = conversation_context if isinstance(conversation_context, dict) else {}
    proposals = ctx.get("pending_assistant_proposals")
    if isinstance(proposals, list):
        for proposal in reversed(proposals):
            if not isinstance(proposal, dict) or proposal.get("status") != "PENDING":
                continue
            for key in ("workflow_intent", "underlying_intent", "intent", "operation"):
                candidate = proposal.get(key)
                if isinstance(candidate, str) and get_stage2_group(candidate):
                    return candidate

    # Compatibility fallback for current Core context, which does not yet project
    # the final confirmation as a proposal object with an underlying workflow.
    for key in ("active_booking_intent", "last_intent"):
        candidate = ctx.get(key)
        if isinstance(candidate, str) and get_stage2_group(candidate):
            return candidate
    return intent


def supports_schema_entity_extraction(intent: str) -> bool:
    """Whether the Stage 2 contract selected for ``intent`` extracts schema entities."""
    return _resolve_routing_group(intent) in _SCHEMA_ENTITY_GROUPS


def _run_group_extractor(
    group: str,
    candidate_intent: str,
    text: str,
    now: str,
    tenant_context: Dict[str, Any],
    conversation_context: Optional[Dict[str, Any]],
    compiled_entities: Optional[CompiledBusinessEntities] = None,
) -> Dict[str, Any]:
    extractor = _get_extractor(group)
    kwargs: Dict[str, Any] = {
        "text": text,
        "now": now,
        "tenant_context": tenant_context,
        "candidate_intent": candidate_intent,
        "conversation_context": conversation_context,
    }
    # Schema-aware booking extractors consume the already-compiled contract;
    # the dispatcher does not reinterpret it.
    if group in _SCHEMA_ENTITY_GROUPS and compiled_entities is not None:
        kwargs["compiled_entities"] = compiled_entities
    return extractor.extract(**kwargs)


def extract_slots(
    intent: str,
    text: str,
    now: str,
    tenant_context: Dict[str, Any],
    conversation_context: Optional[Dict[str, Any]] = None,
    compiled_entities: Optional[CompiledBusinessEntities] = None,
) -> Dict[str, Any]:
    """Route intent to Stage 2, optionally Stage 3 when validated intent changes group.

    The returned dict is compatible with the existing normalization pipeline:
    {intent, confidence, facts, time_constraint, search_query, service_term, temporal, ...}
    """
    initial_intent = _pending_confirmation_initial_intent(intent, conversation_context)
    routed_group = _resolve_routing_group(initial_intent)

    if routed_group is None:
        logger.debug("Stage2 dispatcher: no group for intent=%r, skipping slot extraction", intent)
        return _empty_result(intent)

    logger.debug("Stage2 dispatcher: intent=%r → group=%r", intent, routed_group)

    result = _run_group_extractor(
        routed_group,
        initial_intent,
        text,
        now,
        tenant_context,
        conversation_context,
        compiled_entities=compiled_entities,
    )

    initial_result_intent = result.get("intent", initial_intent)
    initial_proposal_response = result.get("proposal_response")
    final_intent = initial_result_intent
    if final_intent != initial_intent:
        logger.info(
            "Stage2 re-route: %r → %r (group=%r text=%r)",
            initial_intent, final_intent, routed_group, text,
        )

    final_group = get_stage2_group(final_intent)
    if final_group and final_group != routed_group:
        logger.info(
            "Stage3 re-dispatch: %r → group=%r (was group=%r text=%r)",
            final_intent, final_group, routed_group, text,
        )
        result = _run_group_extractor(
            final_group,
            final_intent,
            text,
            now,
            tenant_context,
            conversation_context,
            compiled_entities=compiled_entities,
        )

        destination_intent = result.get("intent", final_intent)
        destination_proposal_response = result.get("proposal_response")
        if (
            destination_intent != initial_result_intent
            or destination_proposal_response != initial_proposal_response
        ):
            logger.info(
                "[NLU_STAGE2_REDISPATCH_EVIDENCE] initial_intent=%r "
                "destination_intent=%r initial_proposal_response=%r "
                "destination_proposal_response=%r",
                initial_result_intent,
                destination_intent,
                initial_proposal_response,
                destination_proposal_response,
            )

    result = _ensure_temporal(result)
    return validate_final_stage2_result(result, conversation_context)
