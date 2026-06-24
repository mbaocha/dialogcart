"""
Luma Facts Adapter

Converts Luma fact-only response format to Core slots format.

Luma returns facts in a structured format:
- facts.service_id
- facts.booking_id
- facts.times (list)
- facts.dates (list)
- facts.date_range
- facts.date_time_pairs (list of {date, time})

This adapter promotes these into Core slots format:
- service_id -> slots["service_id"]
- booking_id -> slots["booking_id"]
- times[0] -> slots["time"]
- dates[0] -> slots["date"]
- date_range -> slots["date_range"]
- date_time_pairs[0] -> slots["date"] and slots["time"]
"""

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)



def is_flexible_combined_utterance(
    date_constraint: Optional[Dict[str, Any]],
    facts: Optional[Dict[str, Any]],
) -> bool:
    """True when vague date + service appear in the same NLU turn (Fix 4).

    Requires facts.dates so a follow-up like \"book a haircut\" after \"tomorrow\"
    (service only, date already in session) does not strip the carried date.
    """
    facts = facts or {}
    return (
        isinstance(date_constraint, dict)
        and date_constraint.get("mode") == "flexible"
        and facts.get("service_id") is not None
        and bool(facts.get("dates"))
    )


def merge_promoted_luma_slots(
    nested_slots: Optional[Dict[str, Any]],
    promoted_slots: Optional[Dict[str, Any]],
    date_constraint: Optional[Dict[str, Any]] = None,
    facts: Optional[Dict[str, Any]] = None,
    *,
    prefer_nested_service_id: bool = False,
) -> Dict[str, Any]:
    """Merge nested + promoted slots and strip date keys when Fix 4 applies."""
    nested = dict(nested_slots or {})
    promoted = dict(promoted_slots or {})
    merged = {**nested, **promoted}
    if (
        prefer_nested_service_id
        and "service_id" in nested
        and "service_id" in promoted
    ):
        merged["service_id"] = nested["service_id"]
    if is_flexible_combined_utterance(date_constraint, facts):
        # Only strip dates promoted from this turn's NLU, not session carry-over.
        if "date" in promoted or (facts and facts.get("dates")):
            merged.pop("date", None)
        if "date_range" in promoted or (facts and facts.get("dates")):
            merged.pop("date_range", None)
    return merged


def facts_to_slots(
    facts: Dict[str, Any],
    intent_name: Optional[str] = None,
    source_text: Optional[str] = None,
) -> Dict[str, Any]:
    """Convert Luma facts to Core slots.

    Phase 2: only service_id and booking_id are promoted here.
    Dates/times live in date_proposal/time_proposal (temporal_proposal.py) and are
    confirmed into slots.date/time only after availability search succeeds.
    """
    if not isinstance(facts, dict):
        return {}

    slots = {}

    # Direct mappings
    if "service_id" in facts:
        slots["service_id"] = facts["service_id"]

    if "booking_id" in facts:
        slots["booking_id"] = facts["booking_id"]

    # Phase 2: dates/times live in date_proposal/time_proposal (see temporal_proposal.py).
    # Confirmed slots.date/time are set after availability search or explicit commit.

    if slots:
        logger.info(
            f"Promoted {len(slots)} slots from Luma facts: {list(slots.keys())}"
        )

    return slots
