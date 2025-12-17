"""
Semantic Resolver

Resolves semantic meaning from extracted entities and grouped intents.
Decides what the user means without binding to actual calendar dates.

This layer answers: "What does the user mean?"
NOT: "What actual dates does this correspond to?"
"""
from dataclasses import dataclass
from typing import Dict, Any, Optional
import re
from ..clarification import Clarification, ClarificationReason


@dataclass
class SemanticResolutionResult:
    """
    Semantic resolution result.

    Contains resolved booking semantics without calendar binding.
    """
    resolved_booking: Dict[str, Any]
    needs_clarification: bool = False
    clarification: Optional[Clarification] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary format."""
        result = {
            "resolved_booking": self.resolved_booking,
            "needs_clarification": self.needs_clarification,
        }
        if self.clarification is not None:
            result["clarification"] = self.clarification.to_dict()
        else:
            result["clarification"] = None
        return result


def resolve_semantics(
    intent_result: Dict[str, Any],
    entities: Dict[str, Any]
) -> SemanticResolutionResult:
    """
    Resolve semantic meaning from intent result and entities.

    This function decides what the user means (exact time vs window,
    single date vs range, etc.) without binding to actual calendar dates.

    Args:
        intent_result: Result from appointment_grouper.group_appointment()
                     Contains: intent, booking, structure, status, reason
        entities: Raw extraction output from EntityMatcher
                 Contains: service_families, dates, dates_absolute, times,
                          time_windows, durations

    Returns:
        SemanticResolutionResult with resolved booking semantics

    Resolution Rules (ordered):
    1. Time precedence: exact > range > window > none
    2. Date precedence: absolute > relative
    3. Window + exact time: both preserved (e.g., "tomorrow morning at 9am")
    4. Range semantics: between X and Y → flexible range
    5. Duration: applies to entire booking unless structure says otherwise
    6. Ambiguity: detect conflicts and set needs_clarification
    """
    booking = intent_result.get("booking", {})
    structure = intent_result.get("structure", {})

    # Extract services
    services = booking.get("services", [])

    # Resolve time semantics
    time_resolution = _resolve_time_semantics(entities, structure)

    # Resolve date semantics
    date_resolution = _resolve_date_semantics(entities, structure)

    # Extract duration
    duration = booking.get("duration")

    # Check for conflicts and ambiguity
    clarification = _check_ambiguity(
        entities, structure, time_resolution, date_resolution
    )

    resolved_booking = {
        "services": services,
        "date_mode": date_resolution["mode"],
        "date_refs": date_resolution["refs"],
        "time_mode": time_resolution["mode"],
        "time_refs": time_resolution["refs"],
        "duration": duration
    }

    return SemanticResolutionResult(
        resolved_booking=resolved_booking,
        needs_clarification=clarification is not None,
        clarification=clarification
    )


def _is_fuzzy_hour(time_text: str) -> bool:
    """
    Check if time is a fuzzy hour pattern (6ish, around 6, about 6).

    Examples:
    - "6ish" → True
    - "around 6" → True
    - "about 6" → True
    - "6pm" → False
    """
    time_lower = time_text.lower().strip()
    fuzzy_patterns = ["ish", "around", "about"]
    return any(pattern in time_lower for pattern in fuzzy_patterns)


def _resolve_time_semantics(
    entities: Dict[str, Any],
    structure: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Resolve time semantics following precedence rules.

    Precedence: exact > range > window > none

    Args:
        entities: Raw extraction output
        structure: Structure interpretation result

    Returns:
        Dict with "mode" and "refs" keys
    """
    times = entities.get("times", [])
    time_windows = entities.get("time_windows", [])
    time_type = structure.get("time_type", "none")

    # Rule 1: Window + exact time - exact time wins, window is discarded
    # Example: "tomorrow morning at 9am" → resolves to 9am, not morning window
    # Exact time always overrides time windows for appointment booking
    if time_windows and times:
        # Discard windows, use only exact time
        return {
            "mode": "exact",
            "refs": [times[0].get("text")]  # Only exact time, no windows
        }

    # Rule 1.5: Fuzzy hours (6ish, around 6) → treat as range ONLY if time window exists
    # If no time window, flag as ambiguous (consistent with bare hour policy)
    if times:
        for time_entity in times:
            time_text = time_entity.get("text", "")
            if _is_fuzzy_hour(time_text):
                # Extract hour from fuzzy pattern
                hour_match = re.search(r'(\d+)', time_text)
                if hour_match and time_windows:
                    # Time window provides context, treat as range
                    hour = int(hour_match.group(1))
                    return {
                        "mode": "range",
                        "refs": [f"{hour}:00", f"{hour+1}:00"]
                    }
                # If no time window, will be flagged as ambiguous in _check_ambiguity

    # Rule 2: Range (between X and Y) - check before single exact time
    if time_type == "range" and times:
        return {
            "mode": "range",
            "refs": [t.get("text") for t in times[:2]]
        }

    # Rule 3: Exact time wins (if present, single or multiple)
    if times:
        if len(times) == 1:
            return {
                "mode": "exact",
                "refs": [times[0].get("text")]
            }
        elif len(times) >= 2:
            # Multiple times without range marker → ambiguity
            return {
                "mode": "exact",  # Default to exact, but flag ambiguity later
                "refs": [t.get("text") for t in times]
            }

    # Rule 4: Window (coarse time ranges) - only if no exact time
    if time_windows:
        return {
            "mode": "window",
            "refs": [tw.get("text") for tw in time_windows]
        }

    # Rule 5: None
    return {
        "mode": "none",
        "refs": []
    }


# Date pattern matching helpers
def _normalize_date_text(text: str) -> str:
    """Normalize common misspellings and shorthand."""
    text_lower = text.lower().strip()
    # Misspellings
    replacements = {
        "tomorow": "tomorrow",
        "tomrw": "tomorrow",
        "nxt": "next",
        "mon": "monday",
        "tue": "tuesday",
        "wed": "wednesday",
        "thu": "thursday",
        "fri": "friday",
        "sat": "saturday",
        "sun": "sunday",
    }
    for old, new in replacements.items():
        if old in text_lower:
            text_lower = text_lower.replace(old, new)
    return text_lower


def _is_simple_relative_day(text: str) -> bool:
    """Check if text is a simple relative day: today, tomorrow, day after tomorrow, tonight."""
    text_lower = text.lower()
    simple_days = ["today", "tomorrow", "day after tomorrow", "tonight"]
    return any(day in text_lower for day in simple_days)


def _is_week_based(text: str) -> bool:
    """Check if text is week-based: this week, next week."""
    text_lower = text.lower()
    return "this week" in text_lower or "next week" in text_lower


def _is_weekend_reference(text: str) -> bool:
    """Check if text is a weekend reference: this weekend, next weekend."""
    text_lower = text.lower()
    return "this weekend" in text_lower or "next weekend" in text_lower


def _is_specific_weekday(text: str) -> bool:
    """Check if text is a specific weekday: this Monday, next Monday, coming Friday."""
    text_lower = text.lower()
    weekdays = ["monday", "tuesday", "wednesday",
                "thursday", "friday", "saturday", "sunday"]
    has_weekday = any(day in text_lower for day in weekdays)
    has_modifier = "this" in text_lower or "next" in text_lower or "coming" in text_lower
    return has_weekday and has_modifier and not _is_plural_weekday(text)


def _is_month_relative(text: str) -> bool:
    """Check if text is month-relative: this month, next month."""
    text_lower = text.lower()
    return "this month" in text_lower or "next month" in text_lower


def _is_fine_grained_modifier(text: str) -> bool:
    """Check if text has fine-grained modifiers: early/mid/end of next week/month."""
    text_lower = text.lower()
    modifiers = ["early", "mid", "end"]
    has_modifier = any(mod in text_lower for mod in modifiers)
    has_period = "week" in text_lower or "month" in text_lower
    return has_modifier and has_period


def _is_locale_ambiguous(text: str) -> bool:
    """Check if date format is locale-ambiguous (e.g., 07/12 could be July 12 or Dec 7)."""
    # Pattern: DD/MM or MM/DD (ambiguous)
    pattern = r'^\d{1,2}/\d{1,2}(?:/\d{2,4})?$'
    if re.match(pattern, text):
        parts = text.split('/')
        if len(parts) >= 2:
            first = int(parts[0])
            second = int(parts[1])
            # If both parts could be months (1-12), it's ambiguous
            if 1 <= first <= 12 and 1 <= second <= 12:
                return True
    return False


def _is_plural_weekday(text: str) -> bool:
    """Check if text contains plural weekday (e.g., 'next Mondays', 'Fridays next week')."""
    text_lower = text.lower()
    weekdays_plural = ["mondays", "tuesdays", "wednesdays",
                       "thursdays", "fridays", "saturdays", "sundays"]
    return any(day in text_lower for day in weekdays_plural)


def _is_vague_date_reference(text: str) -> bool:
    """Check if text is a vague date reference requiring clarification."""
    text_lower = text.lower()
    vague_patterns = [
        "sometime soon",
        "later",
        "whenever",
        "when you're free",
        "when available",
        "soon",
        "eventually"
    ]
    return any(pattern in text_lower for pattern in vague_patterns)


def _is_context_dependent(text: str) -> bool:
    """Check if text is context-dependent requiring clarification."""
    text_lower = text.lower()
    context_patterns = [
        "just gone",
        "just past",
        "last week",
        "following",
        "previous"
    ]
    return any(pattern in text_lower for pattern in context_patterns)


def _is_bare_weekday(text: str) -> bool:
    """
    Check if text is a bare weekday without modifier (this/next).

    Examples:
    - "saturday" → True (ambiguous)
    - "this saturday" → False (resolved)
    - "next monday" → False (resolved)
    - "friday morning" → True (ambiguous - bare weekday)
    """
    text_lower = text.lower().strip()
    weekdays = ["monday", "tuesday", "wednesday",
                "thursday", "friday", "saturday", "sunday"]

    # Check if text is exactly a weekday or starts with a weekday
    for weekday in weekdays:
        if text_lower == weekday:
            return True
        # Check if it starts with weekday but doesn't have modifier
        if text_lower.startswith(weekday):
            # Check for modifiers that make it unambiguous
            modifiers = ["this", "next", "last", "coming", "following"]
            # Look for modifier before the weekday
            for modifier in modifiers:
                if modifier in text_lower and text_lower.index(modifier) < text_lower.index(weekday):
                    return False
            # If weekday is at start and no modifier before it, it's bare
            if text_lower.startswith(weekday):
                return True

    return False


def _is_bare_hour(time_text: str) -> bool:
    """
    Check if time is a bare hour without am/pm or time window.

    Examples:
    - "2" → True (ambiguous)
    - "2pm" → False (resolved)
    - "2 am" → False (resolved)
    - "2" with time window → False (resolved by context)
    """
    time_lower = time_text.lower().strip()

    # Remove common separators and whitespace
    time_clean = time_lower.replace(":", "").replace(".", "").replace(" ", "")

    # Check if it's just digits (bare hour)
    if time_clean.isdigit():
        # Check if it has am/pm indicator
        if "am" in time_lower or "pm" in time_lower or "a.m." in time_lower or "p.m." in time_lower:
            return False
        # If it's just digits without am/pm, it's bare
        return True

    # Check for patterns like "2 o'clock" without am/pm
    if "o'clock" in time_lower or "oclock" in time_lower:
        if "am" not in time_lower and "pm" not in time_lower:
            return True

    return False


def _has_fine_grained_modifier(text: str) -> bool:
    """Check if text has fine-grained modifier (early/mid/end)."""
    return _is_fine_grained_modifier(text)


def _resolve_date_semantics(
    entities: Dict[str, Any],
    structure: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Resolve date semantics with hardened, production-safe subset.

    Supports:
    - Relative days: today, tomorrow, day after tomorrow, tonight → single_day
    - Week-based: this week, next week → range
    - Weekends: this weekend, next weekend → range (Sat-Sun)
    - Specific weekdays: this Monday, next Monday, coming Friday → single_day
    - Month-relative: this month, next month → range (full month)
    - Calendar dates: 15th Dec, Dec 15, 12 July, 12/07 → single_day
      (locale ambiguous like 07/12 → flagged for clarification)
    - Simple ranges: between Monday and Wednesday → range
    - Misspellings: normalized first

    Does NOT resolve (requires clarification):
    - Plural weekdays: next Mondays → clarification
    - Vague references: sometime soon → clarification
    - Context-dependent: Thursday just gone → clarification

    Fine-grained modifiers resolve to ranges only:
    - early/mid/end of next week → range
    - early/mid/end of next month → range

    Args:
        entities: Raw extraction output
        structure: Structure interpretation result

    Returns:
        Dict with "mode", "refs", and optional "needs_clarification" flag
    """
    dates = entities.get("dates", [])
    dates_absolute = entities.get("dates_absolute", [])

    # Normalize date texts (handle misspellings)
    normalized_dates = [_normalize_date_text(d.get("text", "")) for d in dates]
    normalized_absolute = [_normalize_date_text(
        da.get("text", "")) for da in dates_absolute]

    # Rule 1: Check for vague/ambiguous patterns that require clarification
    all_date_texts = normalized_dates + normalized_absolute
    for date_text in all_date_texts:
        if _is_vague_date_reference(date_text):
            # Will be flagged in _check_ambiguity
            pass
        if _is_plural_weekday(date_text):
            # Will be flagged in _check_ambiguity
            pass
        if _is_context_dependent(date_text):
            # Will be flagged in _check_ambiguity
            pass

    # Rule 2: Absolute dates take precedence
    if dates_absolute:
        if len(dates_absolute) == 1:
            date_text = normalized_absolute[0]
            # Check for locale ambiguity (e.g., 07/12 could be July 12 or Dec 7)
            if _is_locale_ambiguous(date_text):
                # Will be flagged in _check_ambiguity
                pass
            return {
                "mode": "single_day",
                "refs": [dates_absolute[0].get("text")]
            }
        elif len(dates_absolute) >= 2:
            # Multiple absolute dates → check for range marker
            if structure.get("date_type") == "range" or "between" in str(structure).lower() or "from" in str(structure).lower():
                return {
                    "mode": "range",
                    "refs": [da.get("text") for da in dates_absolute[:2]]
                }
            else:
                # Ambiguous - will be flagged
                return {
                    "mode": "range",  # Default to range, but flag ambiguity
                    "refs": [da.get("text") for da in dates_absolute[:2]]
                }

    # Rule 3: Relative dates
    if dates:
        if len(dates) == 1:
            date_text = normalized_dates[0]

            # Check for fine-grained modifiers (early/mid/end) → always range
            if _has_fine_grained_modifier(date_text):
                return {
                    "mode": "range",
                    "refs": [dates[0].get("text")]
                }

            # Simple relative days → single_day
            if _is_simple_relative_day(date_text):
                return {
                    "mode": "single_day",
                    "refs": [dates[0].get("text")]
                }

            # Week-based → range
            if _is_week_based(date_text):
                return {
                    "mode": "range",
                    "refs": [dates[0].get("text")]
                }

            # Weekend → range
            if _is_weekend_reference(date_text):
                return {
                    "mode": "range",
                    "refs": [dates[0].get("text")]
                }

            # Specific weekday → single_day
            if _is_specific_weekday(date_text):
                return {
                    "mode": "single_day",
                    "refs": [dates[0].get("text")]
                }

            # Month-relative → range (full month)
            if _is_month_relative(date_text):
                return {
                    "mode": "range",
                    "refs": [dates[0].get("text")]
                }

            # Default: single_day
            return {
                "mode": "single_day",
                "refs": [dates[0].get("text")]
            }
        elif len(dates) >= 2:
            # Multiple relative dates → check for range marker
            if structure.get("date_type") == "range" or "between" in str(structure).lower() or "from" in str(structure).lower():
                return {
                    "mode": "range",
                    "refs": [d.get("text") for d in dates[:2]]
                }
            else:
                # Ambiguous - will be flagged
                return {
                    "mode": "range",  # Default to range, but flag ambiguity
                    "refs": [d.get("text") for d in dates[:2]]
                }

    # Rule 4: Mixed absolute and relative
    if dates_absolute and dates:
        # Absolute takes precedence
        return {
            "mode": "single_day",
            "refs": [dates_absolute[0].get("text")]
        }

    # Rule 5: No dates
    return {
        "mode": "flexible",
        "refs": []
    }


def _check_ambiguity(
    entities: Dict[str, Any],
    structure: Dict[str, Any],
    time_resolution: Dict[str, Any],
    date_resolution: Dict[str, Any]
) -> Optional[Clarification]:
    """
    Check for conflicts and ambiguity.

    Returns Clarification object if clarification is needed, None otherwise.

    Args:
        entities: Raw extraction output
        structure: Structure interpretation result
        time_resolution: Resolved time semantics
        date_resolution: Resolved date semantics

    Returns:
        Clarification object or None
    """
    dates = entities.get("dates", [])
    dates_absolute = entities.get("dates_absolute", [])
    all_dates = dates + dates_absolute

    # Check for vague date references
    for date_entity in all_dates:
        date_text = _normalize_date_text(date_entity.get("text", ""))
        if _is_vague_date_reference(date_text):
            return Clarification(
                reason=ClarificationReason.VAGUE_DATE_REFERENCE,
                data={"date_text": date_entity.get("text")}
            )
        if _is_plural_weekday(date_text):
            return Clarification(
                reason=ClarificationReason.AMBIGUOUS_PLURAL_WEEKDAY,
                data={"date_text": date_entity.get("text")}
            )
        if _is_context_dependent(date_text):
            return Clarification(
                reason=ClarificationReason.CONTEXT_DEPENDENT_DATE,
                data={"date_text": date_entity.get("text")}
            )
        # BUG 2 FIX: Check for bare weekdays (without this/next modifier)
        if _is_bare_weekday(date_text):
            return Clarification(
                reason=ClarificationReason.AMBIGUOUS_WEEKDAY_REFERENCE,
                data={"date_text": date_entity.get("text")}
            )

    # Check for locale-ambiguous dates
    for date_entity in dates_absolute:
        date_text = _normalize_date_text(date_entity.get("text", ""))
        if _is_locale_ambiguous(date_text):
            return Clarification(
                reason=ClarificationReason.LOCALE_AMBIGUOUS_DATE,
                data={"date_text": date_entity.get("text")}
            )

    # Check structure-level ambiguity flag
    if structure.get("needs_clarification", False):
        return Clarification(
            reason=ClarificationReason.CONFLICTING_SIGNALS,
            data={"structure": structure}
        )

    # Check for conflicting dates
    if dates_absolute and dates:
        # Absolute takes precedence, not ambiguous
        pass
    elif len(dates_absolute) > 1:
        # Multiple absolute dates
        if date_resolution["mode"] == "range" and not structure.get("needs_clarification"):
            # Valid range, no ambiguity
            pass
        else:
            return Clarification(
                reason=ClarificationReason.AMBIGUOUS_DATE_MULTIPLE,
                data={"date_count": len(dates_absolute), "dates": [
                    d.get("text") for d in dates_absolute]}
            )
    elif len(dates) > 1:
        # Multiple relative dates
        if date_resolution["mode"] == "range" and not structure.get("needs_clarification"):
            # Valid range, no ambiguity
            pass
        else:
            return Clarification(
                reason=ClarificationReason.AMBIGUOUS_DATE_MULTIPLE,
                data={"date_count": len(dates), "dates": [
                    d.get("text") for d in dates]}
            )

    # Check for multiple times without range
    times = entities.get("times", [])
    time_windows = entities.get("time_windows", [])
    services = entities.get("service_families", [])
    total_dates = len(dates) + len(dates_absolute)

    # BUG 3 FIX: Check for bare hours (without am/pm or time window)
    # This check must happen BEFORE the "exact time exists" rule
    # Also check for fuzzy hours without time window - these need clarification too
    for time_entity in times:
        time_text = time_entity.get("text", "")
        # Skip fuzzy hours (they're handled as ranges in time resolution)
        if _is_fuzzy_hour(time_text):
            # If fuzzy hour has no time window, it's ambiguous (can't determine am/pm)
            if not time_windows:
                return Clarification(
                    reason=ClarificationReason.AMBIGUOUS_TIME_NO_WINDOW,
                    data={"time": time_text}
                )
        # Check for bare hours (just digits, no am/pm)
        elif _is_bare_hour(time_text) and not time_windows:
            return Clarification(
                reason=ClarificationReason.AMBIGUOUS_TIME_NO_WINDOW,
                data={"time": time_text}
            )

    # Rule: Do NOT trigger clarification when:
    # - There is exactly one service
    # - Exactly one date
    # - Exactly one exact time (that is NOT a bare hour - already checked above)
    # Even if a time window is also present
    if len(services) == 1 and total_dates == 1 and len(times) == 1:
        # Exact time exists and is not bare (bare hours already handled above), so no clarification needed
        pass
    elif len(times) > 1:
        if time_resolution["mode"] != "range":
            # Use first time for template rendering
            first_time = times[0].get("text", "") if times else ""
            return Clarification(
                reason=ClarificationReason.AMBIGUOUS_TIME_NO_WINDOW,
                data={"time": first_time}
            )

    # No clarification needed
    return None
