"""
Stage 2 dispatcher — routes a Stage 1 intent to the correct group extractor.

Each group extractor:
- Receives the candidate intent from Stage 1
- Extracts slots using a focused, group-specific prompt
- Is the final authority on intent (can re-route via validated_intent)
- Owns temporal understanding via Temporal; projects legacy fields for compatibility

Routing rules:
- UNKNOWN → create group (create validates intent from full conversation context)
- CONFIRM_ACTION / REJECT_ACTION → no extraction (pipeline handles directly)
- When validated_intent maps to a different Stage 2 group, Stage 3 re-runs
  the correct group extractor and replaces the Stage 2 output entirely.
"""
import logging
from typing import Any, Dict, Optional

from ...registry.intent_groups import get_stage2_group
from ...temporal.stage2_output import empty_temporal_dict
from .groups.availability import AvailabilityGroupExtractor
from .groups.cancel import CancelGroupExtractor
from .groups.create import CreateGroupExtractor
from .groups.faq import FAQGroupExtractor
from .groups.modify import ModifyGroupExtractor
from .groups.view import ViewGroupExtractor

logger = logging.getLogger(__name__)

_GROUP_EXTRACTORS = {
    "create":       CreateGroupExtractor,
    "modify":       ModifyGroupExtractor,
    "cancel":       CancelGroupExtractor,
    "availability": AvailabilityGroupExtractor,
    "view":         ViewGroupExtractor,
    "faq":          FAQGroupExtractor,
}

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
    }


def _resolve_routing_group(intent: str) -> Optional[str]:
    """Map Stage 1 intent to the Stage 2 group for the first extraction pass."""
    if intent == "UNKNOWN":
        return "create"
    return get_stage2_group(intent)


def _run_group_extractor(
    group: str,
    candidate_intent: str,
    text: str,
    now: str,
    tenant_context: Dict[str, Any],
    conversation_context: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    extractor = _get_extractor(group)
    return extractor.extract(
        text=text,
        now=now,
        tenant_context=tenant_context,
        candidate_intent=candidate_intent,
        conversation_context=conversation_context,
    )


def extract_slots(
    intent: str,
    text: str,
    now: str,
    tenant_context: Dict[str, Any],
    conversation_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Route intent to Stage 2, optionally Stage 3 when validated intent changes group.

    The returned dict is compatible with the existing normalization pipeline:
    {intent, confidence, facts, time_constraint, search_query, service_term, temporal, ...}
    """
    routed_group = _resolve_routing_group(intent)

    if routed_group is None:
        logger.debug("Stage2 dispatcher: no group for intent=%r, skipping slot extraction", intent)
        return _empty_result(intent)

    logger.debug("Stage2 dispatcher: intent=%r → group=%r", intent, routed_group)

    result = _run_group_extractor(
        routed_group, intent, text, now, tenant_context, conversation_context,
    )

    final_intent = result.get("intent", intent)
    if final_intent != intent:
        logger.info(
            "Stage2 re-route: %r → %r (group=%r text=%r)",
            intent, final_intent, routed_group, text,
        )

    final_group = get_stage2_group(final_intent)
    if final_group and final_group != routed_group:
        logger.info(
            "Stage3 re-dispatch: %r → group=%r (was group=%r text=%r)",
            final_intent, final_group, routed_group, text,
        )
        result = _run_group_extractor(
            final_group, final_intent, text, now, tenant_context, conversation_context,
        )

    return _ensure_temporal(result)
