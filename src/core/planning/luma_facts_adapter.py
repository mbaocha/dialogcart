"""
Luma Facts Adapter

Converts Luma fact-only response format to Core slots format.

Temporal dates/times are owned by the canonical Temporal object
(see ``core.planning.temporal_contract`` / ``temporal_proposal``).
This adapter only promotes non-temporal facts (service_id, booking_id).
"""

import logging
from typing import Any, Dict, Optional

from core.planning.temporal_contract import (
    get_temporal,
    is_flexible_combined_utterance,
    temporal_has_date_material,
)

logger = logging.getLogger(__name__)


def merge_promoted_luma_slots(
    nested_slots: Optional[Dict[str, Any]],
    promoted_slots: Optional[Dict[str, Any]],
    facts: Optional[Dict[str, Any]] = None,
    *,
    prefer_nested_service_id: bool = False,
    temporal: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Merge nested + promoted slots and strip date keys when Fix 4 applies."""
    merged = {
        k: v for k, v in (nested_slots or {}).items() if v is not None
    }
    for key, value in (promoted_slots or {}).items():
        if value is not None:
            merged[key] = value
    nested = dict(nested_slots or {})
    promoted = dict(promoted_slots or {})
    if (
        prefer_nested_service_id
        and "service_id" in nested
        and "service_id" in promoted
    ):
        merged["service_id"] = nested["service_id"]

    t = temporal if isinstance(temporal, dict) else None
    if is_flexible_combined_utterance(t, facts):
        turn_has_date = temporal_has_date_material(t)
        if "date" in promoted or turn_has_date:
            merged.pop("date", None)
        if "date_range" in promoted or turn_has_date:
            merged.pop("date_range", None)
    return merged


def facts_to_slots(
    facts: Dict[str, Any],
    intent_name: Optional[str] = None,
    source_text: Optional[str] = None,
) -> Dict[str, Any]:
    """Convert Luma facts to Core slots (non-temporal only)."""
    del intent_name, source_text
    if not isinstance(facts, dict):
        return {}

    slots = {}
    if facts.get("service_id") is not None:
        slots["service_id"] = facts["service_id"]
    if facts.get("booking_id") is not None:
        slots["booking_id"] = facts["booking_id"]

    if slots:
        logger.info(
            "Promoted %s slots from Luma facts: %s",
            len(slots),
            list(slots.keys()),
        )
    return slots


# Re-export for callers that imported the helper from this module historically.
__all__ = [
    "facts_to_slots",
    "get_temporal",
    "is_flexible_combined_utterance",
    "merge_promoted_luma_slots",
]
