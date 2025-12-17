"""
Calendar Binder

Converts semantic meaning into actual calendar dates and times.
Produces ISO-8601 values for machine consumption.

This layer answers: "What real dates/times does this correspond to?"
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
import re

# Try zoneinfo first (Python 3.9+), fallback to pytz
try:
    from zoneinfo import ZoneInfo
    try:
        # Test if tzdata is available
        _ = ZoneInfo("UTC")
        ZONEINFO_AVAILABLE = True
    except Exception:
        ZONEINFO_AVAILABLE = False
except ImportError:
    ZONEINFO_AVAILABLE = False

try:
    import pytz
    PYTZ_AVAILABLE = True
except ImportError:
    PYTZ_AVAILABLE = False


from ..clarification import Clarification, ClarificationReason


@dataclass
class CalendarBindingResult:
    """
    Calendar binding result.

    Contains actual calendar dates and times in ISO-8601 format.
    """
    calendar_booking: Dict[str, Any]
    needs_clarification: bool = False
    clarification: Optional[Clarification] = None

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert to JSON-serializable dictionary format.

        Ensures all values are JSON-safe:
        - Datetime objects → ISO-8601 strings
        - Services normalized to minimal external shape
        - None values preserved as null
        - No Python-specific objects leak through

        Returns:
            Dictionary containing only JSON-serializable types
        """
        # Serialize calendar_booking recursively
        serialized_booking = self._serialize_value(self.calendar_booking)

        result = {
            "calendar_booking": serialized_booking,
            "needs_clarification": self.needs_clarification,
        }

        # Include clarification object, serializable
        if self.clarification is not None:
            result["clarification"] = self.clarification.to_dict()
        else:
            result["clarification"] = None

        return result

    def _serialize_value(self, value: Any) -> Any:
        """
        Recursively serialize a value to JSON-safe types.

        Args:
            value: Value to serialize (can be dict, list, datetime, etc.)

        Returns:
            JSON-serializable value
        """
        # Handle None
        if value is None:
            return None

        # Handle datetime objects
        if isinstance(value, datetime):
            # Convert to ISO-8601 string, ensure timezone-aware
            if value.tzinfo is None:
                # Naive datetime - assume UTC
                value = value.replace(tzinfo=_get_timezone("UTC"))
            return value.isoformat()

        # Handle lists first (to normalize services in lists)
        if isinstance(value, list):
            return [self._serialize_value(item) for item in value]

        # Handle dictionaries
        if isinstance(value, dict):
            # Check if this is a service dict (normalize to minimal shape)
            if "text" in value and "canonical" in value:
                # Normalize service to minimal external shape (text + canonical only)
                return {
                    "text": self._serialize_value(value.get("text")),
                    "canonical": self._serialize_value(value.get("canonical"))
                }
            # Regular dict - serialize all values
            return {k: self._serialize_value(v) for k, v in value.items()}

        # Handle primitive types (str, int, float, bool) - already JSON-safe
        if isinstance(value, (str, int, float, bool)):
            return value

        # Fallback: convert to string if unknown type
        return str(value)


# Relative date offsets (days from now)
RELATIVE_DATE_OFFSETS = {
    "today": 0,
    "tonight": 0,
    "tomorrow": 1,
    "next week": 7,
    "next month": 30,
    "this weekend": 0,  # Approximate - would need day-of-week logic
    "next weekend": 7,  # Approximate
}

# Time window bounds (HH:MM format)
TIME_WINDOW_BOUNDS = {
    "morning": {"start": "08:00", "end": "11:59"},
    "afternoon": {"start": "12:00", "end": "16:59"},
    "evening": {"start": "17:00", "end": "20:59"},
    "night": {"start": "21:00", "end": "23:59"},
}

# Month name to number mapping
MONTH_NAMES = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}


def _get_timezone(timezone_str: str):
    """Get timezone object, supporting zoneinfo and pytz."""
    if ZONEINFO_AVAILABLE:
        try:
            return ZoneInfo(timezone_str)
        except Exception:
            from datetime import timezone
            return timezone.utc
    elif PYTZ_AVAILABLE:
        try:
            return pytz.timezone(timezone_str)
        except Exception:
            return pytz.UTC
    else:
        from datetime import timezone
        return timezone.utc


def _localize_datetime(dt: datetime, tz: Any) -> datetime:
    """Localize a naive datetime to timezone-aware."""
    if dt.tzinfo is not None:
        return dt.astimezone(tz)

    # Handle pytz vs zoneinfo
    if PYTZ_AVAILABLE and hasattr(tz, 'localize'):
        return tz.localize(dt)
    else:
        return dt.replace(tzinfo=tz)


# Intents that require calendar binding
BINDING_INTENTS = {
    "AVAILABILITY",
    "CREATE_BOOKING",
    "MODIFY_BOOKING",
    "BOOKING_INQUIRY"
}


def _check_date_ambiguity(
    date_refs: list,
    date_mode: str,
    now: datetime
) -> list[str]:
    """
    Check for ambiguous date references.

    Examples:
    - "Friday morning" with no date context
    - Multiple relative dates without clear ordering

    Returns:
        List of ambiguity reason strings (empty if no ambiguity)
    """
    reasons = []

    if not date_refs:
        return reasons

    # Check for day-of-week references without date context
    day_of_week_refs = ["monday", "tuesday", "wednesday",
                        "thursday", "friday", "saturday", "sunday"]
    for ref in date_refs:
        ref_lower = ref.lower()
        if any(day in ref_lower for day in day_of_week_refs):
            # Day of week without explicit date - ambiguous
            reasons.append(
                f"Day-of-week reference '{ref}' lacks explicit date context")

    # Check for ambiguous relative dates
    if date_mode == "flexible" and date_refs:
        reasons.append(
            f"Date references '{', '.join(date_refs)}' are ambiguous without date context")

    return reasons


def _check_time_ambiguity(
    time_refs: list,
    time_mode: str
) -> list[str]:
    """
    Check for ambiguous time references.

    Examples:
    - "morning" without date context
    - Multiple time windows without clear scope

    Returns:
        List of ambiguity reason strings (empty if no ambiguity)
    """
    reasons = []

    if not time_refs:
        return reasons

    # Check for time windows without date context
    if time_mode == "window" and len(time_refs) > 1:
        reasons.append(
            f"Multiple time windows '{', '.join(time_refs)}' without clear date context")

    return reasons


def bind_calendar(
    semantic_result: Any,
    now: datetime,
    timezone: str = "UTC",
    intent: Optional[str] = None,
    entities: Optional[Dict[str, Any]] = None
) -> CalendarBindingResult:
    """
    Bind semantic meaning to actual calendar dates and times.

    Converts semantic refs (e.g., "tomorrow", "9am", "morning") into
    ISO-8601 dates and times based on the provided "now" timestamp.

    Args:
        semantic_result: SemanticResolutionResult from semantic resolver
        now: Current datetime (injected, not read from system)
        timezone: Timezone string (e.g., "America/New_York", "UTC")
        intent: User intent (e.g., "CREATE_BOOKING", "AVAILABILITY")
                Only binds dates/times for: AVAILABILITY, CREATE_BOOKING,
                MODIFY_BOOKING, BOOKING_INQUIRY. Otherwise returns null.
        entities: Optional original extraction entities (for time-window bias rule)

    Returns:
        CalendarBindingResult with ISO-8601 dates and times

    Binding Rules:
    1. Intent-guarded: Only bind for specific intents
    2. Relative dates → offset from now
    3. Absolute dates without year → prefer future, if passed use next year
    4. Time windows → expand to window bounds
    5. Date + Time → combine into datetime range
    6. Duration → compute end = start + duration
    7. Validation → end must be >= start, reject duration + multi-day ranges
    8. Ambiguity → detect and flag, don't guess
    """
    # Intent-guarded binding: only bind for specific intents
    if intent is not None and intent not in BINDING_INTENTS:
        return CalendarBindingResult(
            calendar_booking={
                "services": semantic_result.resolved_booking.get("services", []),
                "date_range": None,
                "time_range": None,
                "datetime_range": None,
                "duration": None
            },
            needs_clarification=False,
            clarification=None
        )

    # Get timezone object
    tz = _get_timezone(timezone)

    # Ensure now is timezone-aware
    now = _localize_datetime(now, tz)

    # SHORT-CIRCUIT: If semantic resolution requires clarification, trust it
    # Calendar binding must NOT re-evaluate ambiguity
    if semantic_result.needs_clarification:
        return CalendarBindingResult(
            calendar_booking={
                "services": semantic_result.resolved_booking.get("services", []),
                "date_range": None,
                "time_range": None,
                "datetime_range": None,
                "duration": semantic_result.resolved_booking.get("duration")
            },
            needs_clarification=True,
            clarification=semantic_result.clarification
        )

    resolved_booking = semantic_result.resolved_booking
    # ALLOW: Calendar binding to proceed even if services is missing (as long as date/time exists)
    # Guardrail: Do not reject binding if services == []
    # This enables downstream clarification if service is omitted in booking utterances
    services = resolved_booking.get("services", [])
    date_mode = resolved_booking.get("date_mode", "flexible")
    date_refs = resolved_booking.get("date_refs", [])
    time_mode = resolved_booking.get("time_mode", "none")
    time_refs = resolved_booking.get("time_refs", [])
    duration = resolved_booking.get("duration")

    # Bind dates
    date_range = _bind_dates(date_refs, date_mode, now, tz)

    # Extract time windows from entities for bias rule (if available)
    time_windows = None
    if entities:
        time_windows = entities.get("time_windows", [])

    # Bind times (with optional window info for bias rule)
    time_range = _bind_times(time_refs, time_mode, now,
                             tz, time_windows=time_windows)

    # Combine date + time into datetime range
    datetime_range = _combine_datetime_range(date_range, time_range, now, tz)

    # Apply duration if present
    if duration and datetime_range:
        datetime_range = _apply_duration(datetime_range, duration, tz)

    # Validate ranges (includes conflict detection)
    # NOTE: Only validation errors are checked here - ambiguity is decided by semantic resolution
    validation_needs_clarification, validation_reason = _validate_ranges(
        date_range, time_range, datetime_range, semantic_result, duration
    )

    needs_clarification = validation_needs_clarification

    calendar_booking = {
        "services": services,
        "date_range": date_range,
        "time_range": time_range,
        "datetime_range": datetime_range,
        "duration": duration
    }

    clarification_obj = None
    if needs_clarification:
        # Map validation errors to structured clarifications
        # NOTE: Ambiguity reasons come from semantic resolution, not calendar binding
        reason_enum = ClarificationReason.CONFLICTING_SIGNALS  # default fallback
        data = {}

        # Only handle validation errors (range conflicts, duration issues, etc.)
        if validation_reason:
            validation_lower = validation_reason.lower()
            if "end date" in validation_lower or "end datetime" in validation_lower or "after" in validation_lower:
                reason_enum = ClarificationReason.CONFLICTING_SIGNALS
                data = {"validation_error": validation_reason,
                        "date_refs": date_refs}
            elif "duration" in validation_lower and "multi-day" in validation_lower:
                reason_enum = ClarificationReason.CONFLICTING_SIGNALS
                data = {"validation_error": validation_reason,
                        "duration": duration, "date_refs": date_refs}
            else:
                reason_enum = ClarificationReason.CONFLICTING_SIGNALS
                data = {"validation_error": validation_reason}

        clarification_obj = Clarification(
            reason=reason_enum, data=data)
    return CalendarBindingResult(
        calendar_booking=calendar_booking,
        needs_clarification=needs_clarification,
        clarification=clarification_obj
    )


def _bind_dates(
    date_refs: list,
    date_mode: str,
    now: datetime,
    tz: Any
) -> Optional[Dict[str, str]]:
    """
    Bind date references to actual calendar dates.

    Args:
        date_refs: List of date reference strings
        date_mode: "single_day", "range", or "flexible"
        now: Current datetime
        tz: Timezone object

    Returns:
        Dict with "start_date" and "end_date" (YYYY-MM-DD) or None
    """
    if not date_refs:
        return None

    if date_mode == "single_day":
        date_str = date_refs[0]
        bound_date = _bind_single_date(date_str, now, tz)
        if bound_date:
            return {
                "start_date": bound_date.strftime("%Y-%m-%d"),
                "end_date": bound_date.strftime("%Y-%m-%d")
            }

    elif date_mode == "range":
        if len(date_refs) >= 2:
            start_date = _bind_single_date(date_refs[0], now, tz)
            end_date = _bind_single_date(date_refs[1], now, tz)
            if start_date and end_date:
                return {
                    "start_date": start_date.strftime("%Y-%m-%d"),
                    "end_date": end_date.strftime("%Y-%m-%d")
                }

    # flexible mode or invalid refs
    return None


def _bind_single_date(date_str: str, now: datetime, tz: Any) -> Optional[datetime]:
    """
    Bind a single date reference to a datetime.

    Handles:
    - Relative dates (tomorrow, next week)
    - Absolute dates (15th dec, 15/12/2025)
    - Weekday expressions: "this friday", "next monday" (deterministically)

    Args:
        date_str: Date reference string
        now: Current datetime
        tz: Timezone object

    Returns:
        Bound datetime or None
    """
    date_str_lower = date_str.lower().strip()

    # Handle "this <weekday>" and "next <weekday>"
    weekday_map = {"monday": 0, "tuesday": 1, "wednesday": 2,
                   "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6}
    match = re.match(
        r"\b(this|next)\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", date_str_lower)
    if match:
        kind, weekday_str = match.group(1), match.group(2)
        today_weekday = now.weekday()
        target_weekday = weekday_map[weekday_str]
        if kind == "this":
            days_ahead = (target_weekday - today_weekday) % 7
        else:  # "next"
            days_ahead = (target_weekday - today_weekday) % 7 + 7
        target_date = now + timedelta(days=days_ahead)
        return target_date.replace(hour=0, minute=0, second=0, microsecond=0)

    # Check relative dates first
    if date_str_lower in RELATIVE_DATE_OFFSETS:
        offset_days = RELATIVE_DATE_OFFSETS[date_str_lower]
        bound_date = now + timedelta(days=offset_days)
        return bound_date.replace(hour=0, minute=0, second=0, microsecond=0)

    # Parse absolute dates
    # Format: "15th dec" or "15 dec" or "dec 15" or "15/12" or "15/12/2025"
    bound_date = _parse_absolute_date(date_str_lower, now, tz)
    return bound_date


def _parse_absolute_date(date_str: str, now: datetime, tz: Any) -> Optional[datetime]:
    """
    Parse absolute date string.

    Handles formats:
    - "15th dec" / "15 dec"
    - "dec 15" / "december 15"
    - "15/12" / "15/12/2025"
    - "15-12" / "15-12-2025"

    Prefers future dates. If date has passed this year, use next year.
    """
    # Pattern 1: "15th dec" or "15 dec"
    pattern1 = r"(\d{1,2})(?:st|nd|rd|th)?\s+(jan|january|feb|february|mar|march|apr|april|may|jun|june|jul|july|aug|august|sep|september|oct|october|nov|november|dec|december)(?:\s+(\d{4}))?"
    match = re.search(pattern1, date_str)
    if match:
        day = int(match.group(1))
        month_name = match.group(2).lower()
        year_str = match.group(3)
        month = MONTH_NAMES.get(month_name)
        if month:
            year = int(year_str) if year_str else None
            return _resolve_year_month_day(year, month, day, now, tz)

    # Pattern 2: "dec 15" or "december 15"
    pattern2 = r"(jan|january|feb|february|mar|march|apr|april|may|jun|june|jul|july|aug|august|sep|september|oct|october|nov|november|dec|december)\s+(\d{1,2})(?:st|nd|rd|th)?(?:\s+(\d{4}))?"
    match = re.search(pattern2, date_str)
    if match:
        month_name = match.group(1).lower()
        day = int(match.group(2))
        year_str = match.group(3)
        month = MONTH_NAMES.get(month_name)
        if month:
            year = int(year_str) if year_str else None
            return _resolve_year_month_day(year, month, day, now, tz)

    # Pattern 3: "15/12" or "15/12/2025" or "15-12" or "15-12-2025"
    pattern3 = r"(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?"
    match = re.search(pattern3, date_str)
    if match:
        day = int(match.group(1))
        month = int(match.group(2))
        year_str = match.group(3)
        year = int(year_str) if year_str else None
        if year and year < 100:
            # Two-digit year: assume 20xx
            year = 2000 + year
        return _resolve_year_month_day(year, month, day, now, tz)

    return None


def _resolve_year_month_day(
    year: Optional[int],
    month: int,
    day: int,
    now: datetime,
    tz: Any
) -> Optional[datetime]:
    """
    Resolve year, month, day to a datetime.

    If year is None, prefer future date.
    If date has passed this year, use next year.
    """
    current_year = now.year

    if year is None:
        # Try this year first
        try:
            candidate = _localize_datetime(
                datetime(current_year, month, day), tz)
            if candidate >= now.replace(hour=0, minute=0, second=0, microsecond=0):
                return candidate
        except ValueError:
            pass

        # If passed, use next year
        try:
            return _localize_datetime(datetime(current_year + 1, month, day), tz)
        except ValueError:
            return None
    else:
        try:
            return _localize_datetime(datetime(year, month, day), tz)
        except ValueError:
            return None


def _time_in_window(time_hhmm: str, window_name: str) -> bool:
    """
    Check if a time (HH:MM) falls within a time window.

    Args:
        time_hhmm: Time string in HH:MM format
        window_name: Window name (e.g., "morning", "night")

    Returns:
        True if time falls within window bounds
    """
    if window_name.lower() not in TIME_WINDOW_BOUNDS:
        return False

    window = TIME_WINDOW_BOUNDS[window_name.lower()]
    window_start = window["start"]  # "HH:MM"
    window_end = window["end"]      # "HH:MM"

    # Convert to minutes for comparison
    def to_minutes(hhmm: str) -> int:
        h, m = map(int, hhmm.split(":"))
        return h * 60 + m

    time_minutes = to_minutes(time_hhmm)
    start_minutes = to_minutes(window_start)
    end_minutes = to_minutes(window_end)

    return start_minutes <= time_minutes <= end_minutes


def _bind_times(
    time_refs: list,
    time_mode: str,
    now: datetime,
    tz: Any,
    time_windows: Optional[list] = None
) -> Optional[Dict[str, str]]:
    """
    Bind time references to actual times.

    Applies time-window bias rule: if exact time lacks AM/PM and a time window
    exists, prefer the interpretation that falls inside the window.

    Args:
        time_refs: List of time reference strings
        time_mode: "exact", "window", "range", or "none"
        now: Current datetime
        tz: Timezone object
        time_windows: Optional list of time window entities (for bias rule)

    Returns:
        Dict with "start_time" and "end_time" (HH:MM) or None
    """
    if not time_refs:
        return None

    if time_mode == "exact":
        # Extract first exact time (ignore windows if present)
        exact_times = [t for t in time_refs if t not in TIME_WINDOW_BOUNDS]
        if exact_times:
            time_str = exact_times[0]
            bound_time, has_explicit_meridiem = _parse_time(time_str)
            if bound_time:
                time_hhmm = bound_time.strftime("%H:%M")

                # TIME-WINDOW BIAS RULE:
                # When an exact time lacks explicit AM/PM (ambiguous) and a time window exists,
                # prefer the interpretation that falls inside the window.
                #
                # Rule: If exact_time ∉ window AND (exact_time + 12h) ∈ window,
                #       then shift exact_time by +12 hours.
                #
                # Examples:
                # - "night at 10.30" → 10:30 AM ∉ night, 22:30 PM ∈ night → bind to 22:30
                # - "morning at 9.30" → 09:30 AM ∈ morning → keep 09:30 (no shift)
                # - "at 10.30" (no window) → keep default 10:30 (no bias)
                # - "night at 10pm" (explicit PM) → keep 22:00 (no bias, explicit takes precedence)
                #
                # This rule is deterministic, window-consistent, and backward-compatible.
                if not has_explicit_meridiem:
                    if time_windows:
                        # Window bias for ambiguous hour-only time string
                        for window_entity in time_windows:
                            window_name = window_entity.get("text", "").lower()
                            if window_name in TIME_WINDOW_BOUNDS:
                                # SPECIAL CASE: If user said only "hour" (like "9") and there's a window, bias to window start
                                if (':' not in time_str and '.' not in time_str and len(time_str.strip()) <= 2 and time_str.strip().isdigit()):
                                    window_start = TIME_WINDOW_BOUNDS[window_name]['start']
                                    time_hhmm = window_start
                                    break
                                # (DEFAULT): Only apply bias if current time is NOT in window
                                if not _time_in_window(time_hhmm, window_name):
                                    # Try shifting by 12 hours (AM ↔ PM)
                                    hour, minute = map(
                                        int, time_hhmm.split(":"))
                                    shifted_hour = (hour + 12) % 24
                                    shifted_time_hhmm = f"{shifted_hour:02d}:{minute:02d}"
                                    if _time_in_window(shifted_time_hhmm, window_name):
                                        time_hhmm = shifted_time_hhmm
                                        break
                    else:
                        # No window, no meridiem: hour-only → must mark as ambiguous
                        if (':' not in time_str and '.' not in time_str and len(time_str.strip()) <= 2 and time_str.strip().isdigit()):
                            # Return with clarification needed (handled in ambiguity checks, maybe needs rewrite in caller)
                            return None

                return {
                    "start_time": time_hhmm,
                    "end_time": time_hhmm
                }

    elif time_mode == "window":
        window_name = time_refs[0].lower()
        if window_name in TIME_WINDOW_BOUNDS:
            bounds = TIME_WINDOW_BOUNDS[window_name]
            return {
                "start_time": bounds["start"],
                "end_time": bounds["end"]
            }

    elif time_mode == "range":
        if len(time_refs) >= 2:
            # Extract exact times (ignore windows)
            exact_times = [t for t in time_refs if t not in TIME_WINDOW_BOUNDS]
            if len(exact_times) >= 2:
                start_time, _ = _parse_time(exact_times[0])
                end_time, _ = _parse_time(exact_times[1])
                if start_time and end_time:
                    return {
                        "start_time": start_time.strftime("%H:%M"),
                        "end_time": end_time.strftime("%H:%M")
                    }

    return None


def _normalize_time_string(time_str: str) -> str:
    """
    Normalize time string to canonical form before parsing.

    Handles space-delimited tokens like "5 . 30 pm" → "5:30 pm"

    Converts:
    - "5 . 30 pm" → "5:30 pm"
    - "10 . 30" → "10:30"
    - "4 pm" → "4:00 pm"
    - "5:30 pm" → "5:30 pm" (already canonical)

    Returns:
        Normalized time string in canonical form
    """
    # Convert to lowercase and strip
    normalized = time_str.lower().strip()

    # Remove spaces around dots and colons
    # "5 . 30" → "5.30"
    normalized = re.sub(r'\s*([.:])\s*', r'\1', normalized)

    # Handle "X pm" or "X am" where X is just hour
    # "4 pm" → "4:00 pm"
    hour_only_pattern = r'^(\d{1,2})\s+(am|pm)$'
    match = re.match(hour_only_pattern, normalized)
    if match:
        hour = match.group(1)
        meridiem = match.group(2)
        return f"{hour}:00 {meridiem}"

    # Handle "X.XX pm" or "X.XX am" (dot separator with meridiem)
    # "5.30 pm" → "5:30 pm"
    dot_with_meridiem_pattern = r'^(\d{1,2})\.(\d{2})\s+(am|pm)$'
    match = re.match(dot_with_meridiem_pattern, normalized)
    if match:
        hour = match.group(1)
        minute = match.group(2)
        meridiem = match.group(3)
        return f"{hour}:{minute} {meridiem}"

    # Handle "X.XX" (dot separator, no meridiem)
    # "5.30" → "5:30"
    dot_no_meridiem_pattern = r'^(\d{1,2})\.(\d{2})$'
    match = re.match(dot_no_meridiem_pattern, normalized)
    if match:
        hour = match.group(1)
        minute = match.group(2)
        return f"{hour}:{minute}"

    # Return as-is if already in canonical form
    return normalized


def _parse_time(time_str: str) -> tuple[Optional[datetime], bool]:
    """
    Parse time string to datetime (time only, date ignored).

    Returns:
        Tuple of (datetime, has_explicit_meridiem)
        - datetime: Parsed time or None if invalid
        - has_explicit_meridiem: True if AM/PM was explicitly specified

    Handles formats:
    - "9am" / "9 am" / "9:00am" (explicit meridiem)
    - "9pm" / "9 pm" / "9:00pm" (explicit meridiem)
    - "09:30" / "9:30" (no meridiem, ambiguous)
    - "15:00" (24-hour, unambiguous)
    - "10.30" / "10 . 30" (dot separator, no meridiem, ambiguous)
    - "5.30pm" / "5 . 30 pm" (dot separator with meridiem)
    """
    # Normalize time string first (handles space-delimited tokens)
    time_str_normalized = _normalize_time_string(time_str)

    # Remove remaining spaces for pattern matching (keep meridiem separate)
    # "5:30 pm" → "5:30pm" for pattern matching
    time_str_for_pattern = re.sub(r'\s+', '', time_str_normalized)

    # Pattern 1: 12-hour format "9am" or "9:30am" or "5.30pm" (explicit AM/PM)
    # Handles both colon and dot separators
    pattern1 = r"(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm)"
    match = re.search(pattern1, time_str_for_pattern)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2)) if match.group(2) else 0
        period = match.group(3)

        if period == "pm" and hour != 12:
            hour += 12
        elif period == "am" and hour == 12:
            hour = 0

        try:
            # Explicit meridiem
            return datetime(2000, 1, 1, hour, minute), True
        except ValueError:
            return None, False

    # Pattern 2: 24-hour format "09:30" or "15:00" (colon separator)
    pattern2 = r"(\d{1,2}):(\d{2})"
    match = re.search(pattern2, time_str_for_pattern)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2))

        # If hour >= 13, it's unambiguous 24-hour format
        has_explicit_meridiem = hour >= 13

        try:
            return datetime(2000, 1, 1, hour, minute), has_explicit_meridiem
        except ValueError:
            return None, False

    # Pattern 3: 24-hour format "10.30" or "15.00" (dot separator, no meridiem)
    # Handles cases like "at 10.30" where dot is used instead of colon
    # Note: Dot separator with meridiem is handled by Pattern 1
    pattern3 = r"(\d{1,2})\.(\d{2})"
    match = re.search(pattern3, time_str_for_pattern)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2))

        # If hour >= 13, it's unambiguous 24-hour format
        has_explicit_meridiem = hour >= 13

        try:
            return datetime(2000, 1, 1, hour, minute), has_explicit_meridiem
        except ValueError:
            return None, False

    return None, False


def _combine_datetime_range(
    date_range: Optional[Dict[str, str]],
    time_range: Optional[Dict[str, str]],
    now: datetime,
    tz: Any
) -> Optional[Dict[str, str]]:
    """
    Combine date range and time range into datetime range.

    Args:
        date_range: Dict with "start_date" and "end_date" (YYYY-MM-DD)
        time_range: Dict with "start_time" and "end_time" (HH:MM)
        now: Current datetime
        tz: Timezone object

    Returns:
        Dict with "start" and "end" (ISO-8601) or None
    """
    if not date_range and not time_range:
        return None

    # If only date exists → full-day range
    if date_range and not time_range:
        start_date = datetime.strptime(date_range["start_date"], "%Y-%m-%d")
        end_date = datetime.strptime(date_range["end_date"], "%Y-%m-%d")
        start_dt = _localize_datetime(start_date.replace(hour=0, minute=0), tz)
        end_dt = _localize_datetime(end_date.replace(hour=23, minute=59), tz)
        return {
            "start": start_dt.isoformat(),
            "end": end_dt.isoformat()
        }

    # If only time exists → same-day range (use today)
    if time_range and not date_range:
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        start_time_parts = time_range["start_time"].split(":")
        end_time_parts = time_range["end_time"].split(":")

        start_dt = _localize_datetime(today.replace(
            hour=int(start_time_parts[0]),
            minute=int(start_time_parts[1])
        ), tz)
        end_dt = _localize_datetime(today.replace(
            hour=int(end_time_parts[0]),
            minute=int(end_time_parts[1])
        ), tz)

        return {
            "start": start_dt.isoformat(),
            "end": end_dt.isoformat()
        }

    # Both date and time exist → combine
    if date_range and time_range:
        start_date = datetime.strptime(date_range["start_date"], "%Y-%m-%d")
        end_date = datetime.strptime(date_range["end_date"], "%Y-%m-%d")

        start_time_parts = time_range["start_time"].split(":")
        end_time_parts = time_range["end_time"].split(":")

        start_dt = _localize_datetime(start_date.replace(
            hour=int(start_time_parts[0]),
            minute=int(start_time_parts[1])
        ), tz)
        end_dt = _localize_datetime(end_date.replace(
            hour=int(end_time_parts[0]),
            minute=int(end_time_parts[1])
        ), tz)

        return {
            "start": start_dt.isoformat(),
            "end": end_dt.isoformat()
        }

    return None


def _apply_duration(
    datetime_range: Dict[str, str],
    duration: Dict[str, Any],
    tz: Any
) -> Dict[str, str]:
    """
    Apply duration to compute end time.

    If only start is known, compute end = start + duration.

    Args:
        datetime_range: Dict with "start" and "end" (ISO-8601)
        duration: Duration dict with "text" field
        tz: Timezone object

    Returns:
        Updated datetime_range with computed end
    """
    duration_text = duration.get("text", "").lower()

    # Parse duration (e.g., "one hour", "30 mins", "2 hours")
    duration_minutes = _parse_duration(duration_text)

    if duration_minutes:
        start_str = datetime_range["start"]
        start_dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))

        # If end is same as start (point in time), compute end
        if datetime_range["start"] == datetime_range.get("end"):
            end_dt = start_dt + timedelta(minutes=duration_minutes)
            return {
                "start": datetime_range["start"],
                "end": end_dt.isoformat()
            }

    return datetime_range


def _parse_duration(duration_text: str) -> Optional[int]:
    """
    Parse duration text to minutes.

    Handles: "one hour", "30 mins", "2 hours", "half hour", etc.
    """
    duration_text = duration_text.lower().strip()

    # Pattern: number + unit
    patterns = [
        (r"(\d+)\s*(?:hour|hr|h)", lambda m: int(m.group(1)) * 60),
        (r"(\d+)\s*(?:minute|min|m)", lambda m: int(m.group(1))),
        (r"one\s+hour", lambda m: 60),
        (r"half\s+hour", lambda m: 30),
        (r"(\d+)\s*(?:hour|hr|h)\s*(?:and\s+)?(\d+)\s*(?:minute|min|m)",
         lambda m: int(m.group(1)) * 60 + int(m.group(2))),
    ]

    for pattern, converter in patterns:
        match = re.search(pattern, duration_text)
        if match:
            return converter(match)

    return None


def _validate_ranges(
    date_range: Optional[Dict[str, str]],
    time_range: Optional[Dict[str, str]],
    datetime_range: Optional[Dict[str, str]],
    semantic_result: Any,
    duration: Optional[Dict[str, Any]] = None
) -> tuple[bool, Optional[str]]:
    """
    Validate that ranges are valid (end >= start) and check for conflicts.

    Strengthened validation rules:
    - Explicitly reject end < start
    - Reject duration + multi-day date ranges (prefer explicit date ranges)

    Args:
        date_range: Date range dict
        time_range: Time range dict
        datetime_range: Datetime range dict
        semantic_result: Original semantic result
        duration: Duration dict (if present)

    Returns:
        Tuple of (needs_clarification, reason)
    """
    reasons = []

    # Check semantic-level ambiguity flag
    if semantic_result.needs_clarification:
        reasons.append(semantic_result.reason or "Semantic ambiguity detected")

    # Check duration + multi-day date range conflict
    if duration and date_range:
        start_date = date_range.get("start_date")
        end_date = date_range.get("end_date")

        if start_date and end_date and start_date != end_date:
            # Multi-day date range with duration - ambiguous
            reasons.append(
                "Duration specified with multi-day date range - "
                "clarify whether duration applies to each day or entire range"
            )

    # Validate datetime range
    if datetime_range:
        start_str = datetime_range["start"]
        end_str = datetime_range["end"]

        try:
            start_dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
            end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))

            # Allow start == end (point in time), but not end < start
            if end_dt < start_dt:
                reasons.append(
                    "End datetime must be after or equal to start datetime")
        except (ValueError, AttributeError):
            reasons.append("Invalid datetime range format")

    # Validate date range
    if date_range:
        start_date = date_range.get("start_date")
        end_date = date_range.get("end_date")

        if start_date and end_date:
            try:
                start_dt = datetime.strptime(start_date, "%Y-%m-%d")
                end_dt = datetime.strptime(end_date, "%Y-%m-%d")

                if end_dt < start_dt:
                    reasons.append(
                        "End date must be after or equal to start date")
            except ValueError:
                reasons.append("Invalid date range format")

    # Validate time range
    if time_range:
        start_time = time_range.get("start_time")
        end_time = time_range.get("end_time")

        if start_time and end_time:
            try:
                start_parts = start_time.split(":")
                end_parts = end_time.split(":")
                start_minutes = int(start_parts[0]) * 60 + int(start_parts[1])
                end_minutes = int(end_parts[0]) * 60 + int(end_parts[1])

                if end_minutes < start_minutes:
                    # This is OK if it spans midnight, but we'll flag it
                    reasons.append(
                        "Time range may span midnight - clarification needed")
            except (ValueError, IndexError):
                reasons.append("Invalid time range format")

    if reasons:
        return True, "; ".join(reasons)

    return False, None
