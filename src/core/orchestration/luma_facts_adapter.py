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
import re
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


_MONTH_PATTERN = (
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|"
    r"jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|"
    r"nov(?:ember)?|dec(?:ember)?)"
)
_DAY_PATTERN = r"\d{1,2}(?:st|nd|rd|th)?"
_DATE_RANGE_RE = re.compile(
    rf"\b(?:from\s+)?{_MONTH_PATTERN}\s+{_DAY_PATTERN}\s+(?:to|-)\s+{_DAY_PATTERN}\b",
    re.IGNORECASE,
)
_DATE_SINGLE_RE = re.compile(
    rf"\b{_MONTH_PATTERN}\s+{_DAY_PATTERN}\b",
    re.IGNORECASE,
)
_TIME_RE = re.compile(
    r"\b\d{1,2}(?::\d{2})?\s?(?:am|pm)\b",
    re.IGNORECASE,
)
_RELATIVE_DATE_PHRASES = (
    "today",
    "tomorrow",
    "next week",
    "this friday",
    "next friday",
    "this monday",
    "next monday",
    "this tuesday",
    "next tuesday",
    "this wednesday",
    "next wednesday",
    "this thursday",
    "next thursday",
    "this saturday",
    "next saturday",
    "this sunday",
    "next sunday",
)
_TIME_WORDS = ("morning", "afternoon", "evening", "noon", "night", "tonight")


def _extract_raw_date_range(source_text: Optional[str]) -> Optional[str]:
    if not source_text:
        return None
    match = _DATE_RANGE_RE.search(source_text)
    if match:
        return match.group(0).strip().lower()
    return None


def _extract_raw_date(source_text: Optional[str]) -> Optional[str]:
    if not source_text:
        return None
    lowered = source_text.lower()
    for phrase in _RELATIVE_DATE_PHRASES:
        if phrase in lowered:
            return phrase
    match = _DATE_SINGLE_RE.search(lowered)
    if match:
        return match.group(0).strip().lower()
    return None


def _extract_raw_time(source_text: Optional[str]) -> Optional[str]:
    if not source_text:
        return None
    lowered = source_text.lower()
    match = _TIME_RE.search(lowered)
    if match:
        return match.group(0).strip().lower()
    for word in _TIME_WORDS:
        if word in lowered:
            return word
    return None


def is_flexible_combined_utterance(
    date_constraint: Optional[Dict[str, Any]],
    facts: Optional[Dict[str, Any]],
) -> bool:
    """True when vague date + service appear in the same NLU turn (Fix 4).

    Only facts.service_id counts — session-carried service_id must not trigger this
    on follow-up turns like \"next week\" after \"book facial\".
    """
    facts = facts or {}
    return (
        isinstance(date_constraint, dict)
        and date_constraint.get("mode") == "flexible"
        and facts.get("service_id") is not None
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
        merged.pop("date", None)
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
