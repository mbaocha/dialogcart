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


def facts_to_slots(
    facts: Dict[str, Any],
    intent_name: Optional[str] = None,
    source_text: Optional[str] = None,
    time_constraint: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Convert Luma facts format to Core slots format.

    FACT-ONLY CONTRACT: Luma returns facts at top level:
    {
      "intent": { "name": "CREATE_APPOINTMENT" },
      "facts": {
        "service_id": "haircut",
        "times": ["15:00"],
        "dates": ["2024-01-15"]
      }
    }

    This function extracts these facts and promotes them to slots:
    {
      "service_id": "haircut",
      "time": "15:00",
      "date": "2024-01-15"
    }

    Special handling for CREATE_RESERVATION:
    - If dates list has 2+ elements, promote to date_range instead of single date
    - date_range format: {"start": dates[0], "end": dates[1]}

    Rules:
    - Take first element from lists (times, dates, date_time_pairs) for appointments
    - For reservations with 2+ dates, promote to date_range
    - Deterministic: always use first element for single values
    - Do not overwrite existing slots (caller should merge)
    - Fuzzy time windows (morning/evening): facts.times is empty but time_constraint
      carries the label; promote label to slots.time so planning sees it as satisfied.

    Args:
        facts: Luma facts dict (may be empty or partial)
        intent_name: Optional intent name for special handling (e.g., CREATE_RESERVATION)
        source_text: Optional raw user text for fallback raw-value extraction
        time_constraint: Optional top-level time_constraint from NLU response

    Returns:
        Dict of promoted slots (may be empty if no facts present)
    """
    if not isinstance(facts, dict):
        return {}

    slots = {}

    # Direct mappings
    if "service_id" in facts:
        slots["service_id"] = facts["service_id"]

    if "booking_id" in facts:
        slots["booking_id"] = facts["booking_id"]

    if "date_range" in facts:
        slots["date_range"] = facts["date_range"]

    raw_date_range = _extract_raw_date_range(source_text)
    raw_date = _extract_raw_date(source_text)
    raw_time = _extract_raw_time(source_text)

    # Special handling for CREATE_RESERVATION: two dates -> date_range
    if (
        intent_name == "CREATE_RESERVATION"
        and "dates" in facts
        and isinstance(facts["dates"], list)
    ):
        dates_list = facts["dates"]
        if len(dates_list) >= 2:
            # Promote two dates to date_range
            # CRITICAL: Do NOT create a single "date" slot when date_range is created
            slots["date_range"] = (
                raw_date_range
                if raw_date_range
                else {"start": dates_list[0], "end": dates_list[1]}
            )
            logger.info(
                f"Promoted facts.dates (length={len(dates_list)}) to slots.date_range: {slots['date_range']}"
            )
        elif len(dates_list) == 1:
            # Single date for reservation - do NOT promote to date
            logger.debug("Reservation with single date: skipping slots.date promotion")
    else:
        # For appointments or other intents: handle single dates and date ranges
        if "dates" in facts and isinstance(facts["dates"], list):
            if len(facts["dates"]) == 1:
                # CRITICAL: Always use canonical date from facts.dates[0] (Luma-resolved ISO date)
                # This ensures availability execution receives canonical dates (e.g., "2026-01-14")
                # instead of raw natural-language values (e.g., "tomorrow")
                slots["date"] = facts["dates"][0]
                logger.debug(
                    f"Promoted canonical date from facts.dates[0]={facts['dates'][0]} to slots.date (replacing raw_date={raw_date})"
                )
            elif len(facts["dates"]) >= 2:
                # DATE RANGE RECOVERY: For appointments, date ranges (e.g., "next week") satisfy the date slot
                # requirement. Promote to date_range to preserve the range information without forcing
                # a single date selection. The date_range structure is authoritative and preserves the full range.
                # We also set date to the first date for planning layer compatibility (it checks for "date" in
                # collected_slots), but date_range is the source of truth for the actual range.
                # This enables slot recovery for relative date ranges like "next week", "this month", etc.
                slots["date_range"] = (
                    raw_date_range
                    if raw_date_range
                    else {"start": facts["dates"][0], "end": facts["dates"][-1]}
                )
                # CRITICAL: Use canonical date from facts.dates[0] (Luma-resolved ISO date)
                # This ensures availability execution receives canonical dates instead of raw values
                slots["date"] = facts["dates"][0]
                logger.info(
                    f"Promoted facts.dates (length={len(facts['dates'])}) to slots.date_range and slots.date "
                    f"for appointment date range recovery. date_range={slots['date_range']} (authoritative), "
                    f"date={slots['date']} (canonical, for planning compatibility)"
                )

    # List mappings (take first element)
    if (
        "times" in facts
        and isinstance(facts["times"], list)
        and len(facts["times"]) > 0
    ):
        slots["time"] = raw_time or facts["times"][0]
        logger.debug(f"Promoted facts.times[0]={facts['times'][0]} to slots.time")
    elif (
        "time" not in slots
        and isinstance(time_constraint, dict)
        and time_constraint.get("mode") == "fuzzy"
    ):
        label = time_constraint.get("label")
        if label:
            slots["time"] = label
            logger.debug(f"Promoted fuzzy time_constraint.label={label!r} to slots.time")

    # Date-time pairs (take first pair)
    # CRITICAL: For CREATE_RESERVATION, do NOT create "date" slot from date_time_pairs
    # when date_range already exists (reservations use date_range, not single date)
    if (
        "date_time_pairs" in facts
        and isinstance(facts["date_time_pairs"], list)
        and len(facts["date_time_pairs"]) > 0
    ):
        first_pair = facts["date_time_pairs"][0]
        if isinstance(first_pair, dict):
            # For CREATE_RESERVATION, skip "date" promotion if date_range exists
            if intent_name != "CREATE_RESERVATION" or "date_range" not in slots:
                if "date" in first_pair:
                    # CRITICAL: Always use canonical date from date_time_pairs (Luma-resolved ISO date)
                    # This ensures availability execution receives canonical dates instead of raw values
                    slots["date"] = first_pair["date"]
            if "time" in first_pair:
                slots["time"] = raw_time or first_pair["time"]
            logger.debug(
                f"Promoted facts.date_time_pairs[0] to slots.date={slots.get('date')} slots.time={slots.get('time')}"
            )

    if slots:
        logger.info(
            f"Promoted {len(slots)} slots from Luma facts: {list(slots.keys())}"
        )

    return slots
