"""
Luma Facts Adapter

Converts Luma fact-only response format to Core slots format.

Temporal dates/times are owned by the canonical Temporal object
(see ``core.planning.temporal_contract`` / ``temporal_proposal``).
This adapter promotes platform facts (service_id, booking_id) and
entity_schema-allowlisted business facts only.
"""

import logging
from typing import Any, Dict, Mapping, Optional

from core.adapters.nlu.entity_schema_builder import (
    promotable_slot_keys_from_entity_schema,
)
from core.planning.temporal_contract import (
    get_temporal,
    is_flexible_combined_utterance,
    temporal_has_date_material,
)

logger = logging.getLogger(__name__)

# Always eligible for promotion when present and non-null.
_PLATFORM_FACT_KEYS = frozenset({"service_id", "booking_id"})


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
    *,
    entity_schema: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Convert Luma facts to Core slots (non-temporal only).

    Promotes:
    - platform keys ``service_id`` / ``booking_id``
    - keys allowlisted by the active ``entity_schema`` (declared names +
      resolved catalog id keys)

    Null / missing values are omitted so merge will not erase durable slots.
    Undeclared arbitrary fact keys are ignored.
    """
    del intent_name, source_text
    if not isinstance(facts, dict):
        return {}

    allow = set(_PLATFORM_FACT_KEYS)
    allow |= set(promotable_slot_keys_from_entity_schema(entity_schema))

    slots: Dict[str, Any] = {}
    for key in allow:
        if key not in facts:
            continue
        value = facts.get(key)
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        slots[key] = value

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
