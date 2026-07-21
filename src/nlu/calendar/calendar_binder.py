"""
Calendar Binder

Consumes validated Temporal ISO fields and composes calendar artifacts
(timezone localisation, datetime ranges, fuzzy windows, duration).

Natural-language date interpretation lives in Stage2 + TemporalResolver —
not here.
"""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

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


import logging

from ..clarification import Clarification, ClarificationReason
from ..config.core import CREATE_RESERVATION, STATUS_READY
from ..extraction.entity_loading import (
    load_booking_policy,
    load_global_vocabularies,
    load_month_names,
    load_relative_date_offsets,
    load_time_window_bounds,
)

logger = logging.getLogger(__name__)
from ..config.intent_meta import get_intent_registry
from ..config.temporal import (
    ALLOW_BARE_WEEKDAY_BINDING,
    APPOINTMENT_TEMPORAL_TYPE,
    FUZZY_TIME_WINDOWS,
    RESERVATION_TEMPORAL_TYPE,
    DateMode,
    TimeMode,
)


def _get_global_config_path() -> Path:
    """Get path to global normalization config JSON."""
    from ..extraction.entity_loading import get_global_json_path

    # Use standard location based on configured version
    return get_global_json_path()


# Lazy-loaded config (loaded on first use)
_CONFIG_CACHE: Dict[str, Any] = {}


def _load_config() -> Dict[str, Any]:
    """Load and cache configuration from JSON."""
    if not _CONFIG_CACHE:
        config_path = _get_global_config_path()
        _CONFIG_CACHE["relative_date_offsets"] = load_relative_date_offsets(config_path)
        _CONFIG_CACHE["time_window_bounds"] = load_time_window_bounds(config_path)
        _CONFIG_CACHE["month_names"] = load_month_names(config_path)
        _CONFIG_CACHE["booking_policy"] = load_booking_policy(config_path)
        vocabularies = load_global_vocabularies(config_path)
        weekday_map = vocabularies.get("weekdays", {}).get("to_number")
        if weekday_map:
            _CONFIG_CACHE["weekday_to_number"] = weekday_map
    return _CONFIG_CACHE


def _get_relative_date_offsets() -> Dict[str, int]:
    """Get relative date offsets from config."""
    return _load_config()["relative_date_offsets"]


def _get_time_window_bounds() -> Dict[str, Dict[str, str]]:
    """Get time window bounds from config."""
    return _load_config()["time_window_bounds"]


def get_booking_policy() -> Dict[str, bool]:
    """Get booking policy from config."""
    return _load_config()["booking_policy"]


def _get_month_names() -> Dict[str, int]:
    """Get month name to number mapping from entity_types.date.month.to_number."""
    month_names = _load_config()["month_names"]
    logger.debug(
        "[BINDER] _get_month_names: Retrieved month names",
        extra={
            "month_names_keys": list(month_names.keys()) if month_names else [],
            "month_names": month_names,
        },
    )
    return month_names


def _normalize_month_name(month_name: str) -> str:
    """
    Normalize month name/variant to canonical form.

    Uses vocabularies.months to map variants (jan) to canonical (january).
    Falls back to original if not found.
    """
    config_path = _get_global_config_path()
    vocabularies = load_global_vocabularies(config_path)
    months_dict = vocabularies.get("months", {})

    month_lower = month_name.lower()

    # Check if it's already canonical
    if month_lower in months_dict:
        logger.debug(
            "[BINDER] _normalize_month_name: Found as canonical",
            extra={
                "month_name": month_name,
                "month_lower": month_lower,
                "result": month_lower,
            },
        )
        return month_lower

    # Check if it's a variant
    for canonical, variants in months_dict.items():
        if isinstance(variants, list) and month_lower in variants:
            logger.debug(
                "[BINDER] _normalize_month_name: Found as variant",
                extra={
                    "month_name": month_name,
                    "month_lower": month_lower,
                    "canonical": canonical,
                },
            )
            return canonical

    # Not found, return original (will fail lookup later)
    logger.warning(
        "[BINDER] _normalize_month_name: Not found, returning lowercased original",
        extra={
            "month_name": month_name,
            "month_lower": month_lower,
            "months_dict_keys": list(months_dict.keys()) if months_dict else [],
            "months_dict": months_dict,
        },
    )
    return month_lower


def _get_weekday_to_number() -> Dict[str, int]:
    """Get weekday to number mapping from entity_types.date.weekday.to_number."""
    config = _load_config()
    if "weekday_to_number" not in config:
        # Load from entity_types
        from ..extraction.entity_loading import load_global_entity_types

        config_path = _get_global_config_path()
        entity_types = load_global_entity_types(config_path)
        weekday_to_number = (
            entity_types.get("date", {}).get("weekday", {}).get("to_number", {})
        )
        config["weekday_to_number"] = weekday_to_number
    return config["weekday_to_number"]


def _parse_time_token(
    hour_str: str, minute_str: Optional[str], meridiem: Optional[str]
) -> Optional[str]:
    """Convert simple time token to HH:MM 24h."""
    try:
        hour = int(hour_str)
        minute = int(minute_str) if minute_str is not None else 0
    except ValueError:
        return None
    if meridiem:
        mer = meridiem.lower()
        if mer == "pm" and hour != 12:
            hour += 12
        if mer == "am" and hour == 12:
            hour = 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return f"{hour:02d}:{minute:02d}"


def _extract_time_range_from_entities(
    entities: Optional[Dict[str, Any]]
) -> Optional[Dict[str, str]]:
    """
    Deterministic extraction of explicit time window phrasing: "between X and Y".
    """
    if not entities:
        return None
    text = str(entities.get("osentence") or entities.get("psentence") or "").lower()
    if "between" not in text or "and" not in text:
        return None
    match = re.search(
        r"between\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*(?:and|-)\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)?",
        text,
    )
    if not match:
        return None
    start_h, start_m, start_mer, end_h, end_m, end_mer = match.groups()
    start_time = _parse_time_token(start_h, start_m, start_mer)
    end_time = _parse_time_token(end_h, end_m, end_mer)
    if not start_time or not end_time:
        return None
    return {"start_time": start_time, "end_time": end_time}


@dataclass
class CalendarBindingResult:
    """
    Calendar binding result.

    Contains actual calendar dates and times in ISO-8601 format.

    Internal debug fields (not serialized):
    - _binding_success: True if binding succeeded, False if binding failed or was skipped
    - _binding_error: Error reason string if binding failed/skipped, None if successful
    """

    calendar_booking: Dict[str, Any]
    needs_clarification: bool = False
    clarification: Optional[Clarification] = None
    _binding_success: bool = (
        True  # Internal: explicit success/failure flag for debugging
    )
    _binding_error: Optional[str] = (
        None  # Internal: error reason if binding failed/skipped
    )

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
                value = value.replace(tzinfo=get_timezone("UTC"))
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
                    "canonical": self._serialize_value(value.get("canonical")),
                }
            # Regular dict - serialize all values
            return {k: self._serialize_value(v) for k, v in value.items()}

        # Handle primitive types (str, int, float, bool) - already JSON-safe
        if isinstance(value, (str, int, float, bool)):
            return value

        # Fallback: convert to string if unknown type
        return str(value)


# Configuration is now loaded from JSON via _load_config() functions above
# These constants are removed - use _get_relative_date_offsets(), _get_time_window_bounds(), etc.


def get_timezone(timezone_str: str):
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
    if PYTZ_AVAILABLE and hasattr(tz, "localize"):
        return tz.localize(dt)
    else:
        return dt.replace(tzinfo=tz)


# Intents that require calendar binding (policy-driven)
# CREATE_APPOINTMENT and CREATE_RESERVATION are included from IntentRegistry
def _get_binding_intents() -> set:
    """Get intents that require calendar binding from IntentRegistry."""
    registry = get_intent_registry()
    binding_intents = {"AVAILABILITY", "MODIFY_BOOKING", "BOOKING_INQUIRY"}
    # Add intents with temporal_shape (CREATE_APPOINTMENT, CREATE_RESERVATION)
    for intent_name, intent_meta in registry.all_intents().items():
        if intent_meta.temporal_shape:
            binding_intents.add(intent_name)
    return binding_intents


BINDING_INTENTS = _get_binding_intents()


def _check_date_ambiguity(date_refs: list, date_mode: str, now: datetime) -> list[str]:
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
    day_of_week_refs = [
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
    ]
    for ref in date_refs:
        ref_lower = ref.lower()
        if any(day in ref_lower for day in day_of_week_refs):
            # Day of week without explicit date - ambiguous
            reasons.append(f"Day-of-week reference '{ref}' lacks explicit date context")

    # Check for ambiguous relative dates
    if date_mode == DateMode.FLEXIBLE.value and date_refs:
        reasons.append(
            f"Date references '{', '.join(date_refs)}' are ambiguous without date context"
        )

    return reasons


def _check_time_ambiguity(time_refs: list, time_mode: str) -> list[str]:
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
    if time_mode == TimeMode.WINDOW.value and len(time_refs) > 1:
        reasons.append(
            f"Multiple time windows '{', '.join(time_refs)}' without clear date context"
        )

    return reasons


def bind_calendar(
    semantic_result: Any,
    now: datetime,
    timezone: str = "UTC",
    intent: Optional[str] = None,
    entities: Optional[Dict[str, Any]] = None,
    external_intent: Optional[str] = None,
    temporal: Optional[Dict[str, Any]] = None,
) -> Tuple[CalendarBindingResult, Dict[str, Any]]:
    """
    Bind semantic meaning to actual calendar dates and times.

    Prefer canonical ``temporal`` when provided (Stage2 ownership). Legacy
    ``date_refs`` remain as a fallback for callers that have not migrated.

    Args:
        semantic_result: SemanticResolutionResult from semantic resolver
        now: Current datetime (injected, not read from system)
        timezone: Timezone string (e.g., "America/New_York", "UTC")
        intent: User intent (e.g., "CREATE_APPOINTMENT", "CREATE_RESERVATION", "AVAILABILITY")
                Only binds dates/times for: AVAILABILITY, CREATE_APPOINTMENT, CREATE_RESERVATION,
                MODIFY_BOOKING, BOOKING_INQUIRY. Otherwise returns null.
        entities: Optional original extraction entities (for time-window bias rule)
        temporal: Optional canonical Temporal dict from Stage2

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
    # Extract input for trace
    resolved_booking = semantic_result.resolved_booking
    intent_name = external_intent or intent
    temporal_dict = temporal if isinstance(temporal, dict) else None

    # Get temporal shape from IntentRegistry (sole policy source)
    registry = get_intent_registry()
    intent_meta = registry.get(intent_name) if intent_name else None
    temporal_shape = intent_meta.temporal_shape if intent_meta else None

    # Extract date_mode early for week/weekend range detection
    date_mode = resolved_booking.get("date_mode", DateMode.FLEXIBLE.value)
    date_refs = resolved_booking.get("date_refs", [])
    date_modifiers = resolved_booking.get("date_modifiers", []) or []

    binder_input = {
        "intent": intent,
        "external_intent": external_intent,
        "temporal_shape": temporal_shape,
        "date_mode": date_mode,
        "date_refs": date_refs,
        "temporal": temporal_dict,
        "time_mode": resolved_booking.get("time_mode", "none"),
        "time_refs": resolved_booking.get("time_refs", []),
        "timezone": timezone,
    }

    # Intent-guarded binding: only bind for specific intents
    # Exception: Allow UNKNOWN intents with week/weekend range expressions OR absolute single-day dates
    has_week_range_expression = False
    has_absolute_single_day = False
    if intent == "UNKNOWN":
        # Prefer Temporal for UNKNOWN exceptions; fall back to projected date_refs.
        t_start_expr = (
            str((temporal_dict or {}).get("start_date_expression") or "").lower().strip()
        )
        t_end_expr = (
            str((temporal_dict or {}).get("end_date_expression") or "").lower().strip()
        )
        t_phrase = t_start_expr or t_end_expr
        t_has_iso = bool(
            (temporal_dict or {}).get("start_date")
            or (temporal_dict or {}).get("end_date")
        )

        if date_mode == DateMode.FLEXIBLE.value:
            logger.info(
                "[BINDER] Checking for week/weekend range expression (UNKNOWN intent)",
                extra={
                    "intent": intent,
                    "date_mode": date_mode,
                    "date_refs": date_refs,
                    "date_refs_len": len(date_refs),
                    "date_modifiers": date_modifiers,
                    "temporal_phrase": t_phrase or None,
                },
            )
            if t_phrase and (
                ("week" in t_phrase and "weekend" not in t_phrase)
                or "weekend" in t_phrase
            ):
                has_week_range_expression = True
            elif len(date_refs) == 1:
                date_str = str(date_refs[0]).lower()
                if (
                    "week" in date_str and "weekend" not in date_str
                ) or "weekend" in date_str:
                    has_week_range_expression = True
                    logger.info(
                        "[BINDER] Week/weekend range detected in date_refs",
                        extra={"date_str": date_str},
                    )
            # Also check entities for week/weekend phrases (when date_refs is empty but we'll populate it)
            if not has_week_range_expression and entities:
                osentence = (
                    entities.get("osentence", "").lower()
                    if isinstance(entities, dict)
                    else ""
                )
                if osentence:
                    has_week = "week" in osentence and "weekend" not in osentence
                    has_weekend = "weekend" in osentence
                    has_modifier = len(date_modifiers) > 0
                    logger.info(
                        "[BINDER] Checking entities for week/weekend",
                        extra={
                            "osentence": osentence,
                            "has_week": has_week,
                            "has_weekend": has_weekend,
                            "has_modifier": has_modifier,
                        },
                    )
                    if (has_week or has_weekend) and has_modifier:
                        has_week_range_expression = True
                        logger.info(
                            "[BINDER] Week/weekend range detected in entities",
                            extra={"has_week_range_expression": True},
                        )

        # Check for absolute single-day dates (Temporal ISO/expression or legacy date_refs)
        if date_mode == DateMode.SINGLE.value:
            if t_has_iso or t_start_expr or (
                isinstance(date_refs, list) and len(date_refs) == 1
            ):
                has_absolute_single_day = True
                logger.info(
                    "[BINDER] Absolute single-day date detected (UNKNOWN intent)",
                    extra={
                        "intent": intent,
                        "date_mode": date_mode,
                        "date_ref": date_refs[0] if date_refs else None,
                        "date_refs_len": len(date_refs) if isinstance(date_refs, list) else 0,
                        "temporal_start": (temporal_dict or {}).get("start_date"),
                    },
                )

    logger.info(
        "[BINDER] Intent guard check",
        extra={
            "intent": intent,
            "intent_in_binding_intents": intent in BINDING_INTENTS if intent else False,
            "has_week_range_expression": has_week_range_expression,
            "has_absolute_single_day": has_absolute_single_day,
            "will_reject": intent is not None
            and intent not in BINDING_INTENTS
            and not has_week_range_expression
            and not has_absolute_single_day,
        },
    )
    if (
        intent is not None
        and intent not in BINDING_INTENTS
        and not has_week_range_expression
        and not has_absolute_single_day
    ):
        result = CalendarBindingResult(
            calendar_booking={},
            needs_clarification=False,
            clarification=None,
            _binding_success=False,
            _binding_error=f"intent_not_in_binding_intents: {intent}",
        )
        trace = {
            "binder": {
                "called": False,
                "input": binder_input,
                "output": {},
                "decision_reason": f"intent_not_in_binding_intents: {intent}",
            }
        }
        return result, trace

    # Status guard: bind only when upstream marked booking as ready
    status = (
        resolved_booking.get("status") if isinstance(resolved_booking, dict) else None
    )
    if status is not None and status != STATUS_READY:
        result = CalendarBindingResult(
            calendar_booking=resolved_booking,
            needs_clarification=False,
            clarification=None,
            _binding_success=False,
            _binding_error=f"status_not_ready: {status}",
        )
        trace = {
            "binder": {
                "called": False,
                "input": binder_input,
                "output": {},
                "decision_reason": f"status_not_ready: {status}",
            }
        }
        return result, trace

    # Get timezone object
    tz = get_timezone(timezone)

    # Ensure now is timezone-aware
    now = _localize_datetime(now, tz)

    # Calendar binding assumes inputs are already approved by Decision layer
    # No implicit clarification triggers - just bind what's provided
    resolved_booking = semantic_result.resolved_booking
    # ALLOW: Calendar binding to proceed even if services is missing (as long as date/time exists)
    # Guardrail: Do not reject binding if services == []
    # This enables downstream clarification if service is omitted in booking utterances
    services = resolved_booking.get("services", [])
    # date_mode, date_refs, and date_modifiers already extracted above for intent guard check
    # Build a single normalized list for binding: combine modifier + weekday when applicable
    weekdays = {
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
    }
    modifiers = {"next", "this"}
    date_refs_for_binding: list = []
    for ref in date_refs:
        ref_norm = str(ref).strip().lower()
        if ref_norm in weekdays and any(m in modifiers for m in date_modifiers):
            chosen_mod = next((m for m in date_modifiers if m in modifiers), None)
            if chosen_mod:
                date_refs_for_binding.append(f"{chosen_mod} {ref_norm}")
                continue
        date_refs_for_binding.append(ref)
    time_mode = resolved_booking.get("time_mode", TimeMode.NONE.value)
    time_refs = resolved_booking.get("time_refs", [])
    duration = resolved_booking.get("duration")

    # Prefer canonical Temporal; fall back to legacy date_refs.
    if temporal_dict:
        logger.info(
            "[BINDER] Calling _bind_dates_from_temporal",
            extra={
                "start_date": temporal_dict.get("start_date"),
                "end_date": temporal_dict.get("end_date"),
                "start_date_expression": temporal_dict.get("start_date_expression"),
                "end_date_expression": temporal_dict.get("end_date_expression"),
            },
        )
        date_range = _bind_dates_from_temporal(temporal_dict, now, tz)
    else:
        logger.info(
            "[BINDER] Calling _bind_dates",
            extra={
                "date_refs_for_binding": date_refs_for_binding,
                "date_mode": date_mode,
                "date_refs_len": len(date_refs_for_binding) if date_refs_for_binding else 0,
            },
        )
        date_range = _bind_dates(date_refs_for_binding, date_mode, now, tz)
    logger.info(
        "[BINDER] date bind returned",
        extra={
            "date_range": date_range,
            "date_range_is_none": date_range is None,
            "date_range_keys": list(date_range.keys()) if date_range else None,
            "source": "temporal" if temporal_dict else "date_refs",
        },
    )

    # Time binding from Temporal (exact / window / fuzzy expression)
    time_range = None
    fuzzy_label = None
    if temporal_dict:
        t_start = temporal_dict.get("start_time")
        t_end = temporal_dict.get("end_time")
        if t_start and t_end and t_end != t_start:
            if _time_greater(t_start, t_end):
                raise ValueError(
                    "Invalid Temporal: start_time must be before end_time"
                )
            time_range = {"start_time": t_start, "end_time": t_end}
        elif t_start:
            time_range = {"start_time": t_start, "end_time": t_start}
        else:
            label = (
                temporal_dict.get("start_time_expression") or ""
            ).lower().strip()
            if label in FUZZY_TIME_WINDOWS:
                fuzzy_label = label

    # Explicit time windows for services (between X and Y) should bind directly
    if (
        not time_range
        and resolved_booking.get("booking_mode") == "service"
        and resolved_booking.get("time_mode") == TimeMode.WINDOW.value
    ):
        refs = resolved_booking.get("time_refs") or []
        if len(refs) >= 2:
            time_range = {
                "start_time": _convert_time_to_24h(str(refs[0])) or refs[0],
                "end_time": _convert_time_to_24h(str(refs[1])) or refs[1],
            }

    # Fuzzy time handling for services → build datetime_range using FUZZY_TIME_WINDOWS
    datetime_range = None
    if (
        resolved_booking.get("booking_mode") == "service"
        and fuzzy_label
    ):
        label = fuzzy_label
        if not date_range or not date_range.get("start_date"):
            # No date → cannot bind fuzzy time
            result = CalendarBindingResult(
                calendar_booking={},
                needs_clarification=True,
                clarification=Clarification(
                    reason=ClarificationReason.MISSING_DATE, data={"expected": "date"}
                ),
                _binding_success=False,
                _binding_error="fuzzy_time_no_date",
            )
            trace = {
                "binder": {
                    "called": True,
                    "input": binder_input,
                    "output": {
                        "needs_clarification": True,
                        "clarification_reason": "MISSING_DATE",
                    },
                    "decision_reason": "fuzzy_time_no_date",
                }
            }
            return result, trace
        if label not in FUZZY_TIME_WINDOWS:
            result = CalendarBindingResult(
                calendar_booking={},
                needs_clarification=True,
                clarification=Clarification(
                    reason=ClarificationReason.MISSING_TIME, data={"expected": "time"}
                ),
                _binding_success=False,
                _binding_error=f"fuzzy_time_label_not_found: {label}",
            )
            trace = {
                "binder": {
                    "called": True,
                    "input": binder_input,
                    "output": {
                        "needs_clarification": True,
                        "clarification_reason": "MISSING_TIME",
                    },
                    "decision_reason": f"fuzzy_time_label_not_found: {label}",
                }
            }
            return result, trace
        window_start, window_end = FUZZY_TIME_WINDOWS[label]
        start_date = date_range["start_date"]
        datetime_range = {
            "start": f"{start_date}T{window_start}Z",
            "end": f"{start_date}T{window_end}Z",
        }
        time_range = {"start_time": window_start, "end_time": window_end}
    else:
        # If still no time_range and explicit window pattern exists, extract it
        if not time_range:
            tr = _extract_time_range_from_entities(entities)
            if tr:
                time_range = tr

    # Combine date + time into datetime range
    # NOTE: If date_range is None, datetime_range will also be None (no fallback to today)
    # For reservations, ignore time_range and use full-day logic
    datetime_range = combine_datetime_range(
        date_range, time_range, now, tz, external_intent=external_intent
    )

    # Apply duration if present
    if duration and datetime_range:
        datetime_range = _apply_duration(datetime_range, duration, tz)

    # Build calendar_booking dict - ensure all fields are included if present
    # This is the single authoritative carrier of binder output for all intents
    calendar_booking = {}
    # Always include services if present (even if empty list)
    if services is not None:
        calendar_booking["services"] = services
    # Include date_range if present (for reservations)
    if date_range:
        logger.info(
            "[BINDER] Adding date_range to calendar_booking",
            extra={
                "date_range": date_range,
                "reservation_temporal_type": RESERVATION_TEMPORAL_TYPE,
            },
        )
        calendar_booking[RESERVATION_TEMPORAL_TYPE] = date_range
    else:
        logger.warning(
            "[BINDER] date_range is None, not adding to calendar_booking",
            extra={
                "date_refs_for_binding": date_refs_for_binding,
                "date_mode": date_mode,
            },
        )
    # Include time_range if present
    if time_range:
        calendar_booking["time_range"] = time_range
    # Include datetime_range if present (for appointments)
    if datetime_range:
        calendar_booking[APPOINTMENT_TEMPORAL_TYPE] = datetime_range
    # Include duration if present
    if duration:
        calendar_booking["duration"] = duration
    # Surface time info when extracted from explicit window so downstream slot checks pass
    if time_range:
        calendar_booking["time_refs"] = [
            time_range.get("start_time"),
            time_range.get("end_time"),
        ]

    # Reservation responses must expose start_date/end_date explicitly
    # For CREATE_RESERVATION and MODIFY_BOOKING reservations, expose date_range and start_date/end_date
    if external_intent in ("CREATE_RESERVATION", "MODIFY_BOOKING") and date_range:
        # For MODIFY_BOOKING, also ensure date_range is stored directly (for delta slots)
        if external_intent == "MODIFY_BOOKING":
            # Store date_range directly for MODIFY_BOOKING (already stored in RESERVATION_TEMPORAL_TYPE above)
            # No additional action needed since RESERVATION_TEMPORAL_TYPE == "date_range"
            pass
        # Expose start_date/end_date for both CREATE_RESERVATION and MODIFY_BOOKING
        calendar_booking["start_date"] = date_range.get("start_date")
        calendar_booking["end_date"] = date_range.get("end_date")

    # Success: binding completed successfully
    result = CalendarBindingResult(
        calendar_booking=calendar_booking,
        needs_clarification=False,
        clarification=None,
        _binding_success=True,
        _binding_error=None,
    )

    trace = {
        "binder": {
            "called": True,
            "input": binder_input,
            "output": calendar_booking,
            "decision_reason": "success",
        }
    }

    return result, trace


def _bind_dates_from_temporal(
    temporal: Dict[str, Any], now: datetime, tz: Any
) -> Optional[Dict[str, str]]:
    """
    Build date_range from validated Temporal ISO fields only.

    Natural-language interpretation is forbidden here. Closed relatives must
    already be resolved by ``TemporalResolver`` before bind_calendar runs.
    """
    start_iso = temporal.get("start_date")
    end_iso = temporal.get("end_date")

    start_dt = _bind_single_date(str(start_iso).strip(), now, tz) if start_iso else None
    end_dt = _bind_single_date(str(end_iso).strip(), now, tz) if end_iso else None

    if start_dt and end_dt:
        if start_dt > end_dt:
            end_dt = _localize_datetime(
                datetime(start_dt.year, end_dt.month, end_dt.day), tz
            )
            if start_dt > end_dt:
                start_dt = _localize_datetime(
                    datetime(end_dt.year, start_dt.month, start_dt.day), tz
                )
        return {
            "start_date": start_dt.strftime("%Y-%m-%d"),
            "end_date": end_dt.strftime("%Y-%m-%d"),
        }
    if start_dt:
        day = start_dt.strftime("%Y-%m-%d")
        return {"start_date": day, "end_date": day}
    return None


def _bind_dates(
    date_refs: list, date_mode: str, now: datetime, tz: Any
) -> Optional[Dict[str, str]]:
    """
    Legacy path when Temporal is absent: ISO date_refs only.

    Relative/weekday/month natural language is not interpreted here.
    """
    if not date_refs:
        return None

    iso_refs = []
    for ref in date_refs:
        bound = _bind_single_date(str(ref).strip(), now, tz)
        if bound is not None:
            iso_refs.append(bound)

    if not iso_refs:
        return None

    if date_mode == DateMode.RANGE.value or len(iso_refs) >= 2:
        start_date = iso_refs[0]
        end_date = iso_refs[-1]
        if start_date > end_date:
            end_date = _localize_datetime(
                datetime(start_date.year, end_date.month, end_date.day), tz
            )
            if start_date > end_date:
                start_date = _localize_datetime(
                    datetime(end_date.year, start_date.month, start_date.day), tz
                )
        return {
            "start_date": start_date.strftime("%Y-%m-%d"),
            "end_date": end_date.strftime("%Y-%m-%d"),
        }

    day = iso_refs[0].strftime("%Y-%m-%d")
    return {"start_date": day, "end_date": day}


def _bind_single_date(date_str: str, now: Any, tz: Any) -> Optional[datetime]:
    """Accept ISO YYYY-MM-DD only. No natural-language interpretation."""
    del now
    if not date_str:
        return None
    date_str_stripped = str(date_str).strip()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date_str_stripped):
        return None
    try:
        parsed_date = datetime.strptime(date_str_stripped, "%Y-%m-%d")
        return _localize_datetime(parsed_date, tz)
    except ValueError:
        return None


def _parse_absolute_date(date_str: str, now: datetime, tz: Any) -> Optional[datetime]:
    """Removed: Stage2 owns named-month/absolute NL. Kept as no-op for import safety."""
    del date_str, now, tz
    return None


def _resolve_year_month_day(
    year: Optional[int], month: int, day: int, now: datetime, tz: Any
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
            candidate = _localize_datetime(datetime(current_year, month, day), tz)
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
    time_window_bounds = _get_time_window_bounds()
    if window_name.lower() not in time_window_bounds:
        return False

    window = time_window_bounds[window_name.lower()]
    window_start = window["start"]  # "HH:MM"
    window_end = window["end"]  # "HH:MM"

    # Convert to minutes for comparison
    def to_minutes(hhmm: str) -> int:
        h, m = map(int, hhmm.split(":"))
        return h * 60 + m

    time_minutes = to_minutes(time_hhmm)
    start_minutes = to_minutes(window_start)
    end_minutes = to_minutes(window_end)

    return start_minutes <= time_minutes <= end_minutes


def bind_times(
    time_refs: list,
    time_mode: str,
    now: datetime,
    tz: Any,
    time_windows: Optional[list] = None,
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

    if time_mode == TimeMode.EXACT.value:
        # Extract first exact time (ignore windows if present)
        time_window_bounds = _get_time_window_bounds()
        exact_times = [t for t in time_refs if t not in time_window_bounds]
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
                            time_window_bounds = _get_time_window_bounds()
                            if window_name in time_window_bounds:
                                # SPECIAL CASE: If user said only "hour" (like "9") and there's a window, bias to window start
                                if (
                                    ":" not in time_str
                                    and "." not in time_str
                                    and len(time_str.strip()) <= 2
                                    and time_str.strip().isdigit()
                                ):
                                    window_start = time_window_bounds[window_name][
                                        "start"
                                    ]
                                    time_hhmm = window_start
                                    break
                                # (DEFAULT): Only apply bias if current time is NOT in window
                                if not _time_in_window(time_hhmm, window_name):
                                    # Try shifting by 12 hours (AM ↔ PM)
                                    hour, minute = map(int, time_hhmm.split(":"))
                                    shifted_hour = (hour + 12) % 24
                                    shifted_time_hhmm = (
                                        f"{shifted_hour:02d}:{minute:02d}"
                                    )
                                    if _time_in_window(shifted_time_hhmm, window_name):
                                        time_hhmm = shifted_time_hhmm
                                        break
                    else:
                        # No window, no meridiem: hour-only → must mark as ambiguous
                        if (
                            ":" not in time_str
                            and "." not in time_str
                            and len(time_str.strip()) <= 2
                            and time_str.strip().isdigit()
                        ):
                            # Return with clarification needed (handled in ambiguity checks, maybe needs rewrite in caller)
                            return None

                return {"start_time": time_hhmm, "end_time": time_hhmm}

    elif time_mode == TimeMode.WINDOW.value:
        window_name = time_refs[0].lower()
        time_window_bounds = _get_time_window_bounds()
        if window_name in time_window_bounds:
            bounds = time_window_bounds[window_name]
            return {"start_time": bounds["start"], "end_time": bounds["end"]}

    elif time_mode == TimeMode.RANGE.value:
        if len(time_refs) >= 2:
            # Extract exact times (ignore windows)
            time_window_bounds = _get_time_window_bounds()
            exact_times = [t for t in time_refs if t not in time_window_bounds]
            if len(exact_times) >= 2:
                start_time, _ = _parse_time(exact_times[0])
                end_time, _ = _parse_time(exact_times[1])
                if start_time and end_time:
                    return {
                        "start_time": start_time.strftime("%H:%M"),
                        "end_time": end_time.strftime("%H:%M"),
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
    normalized = re.sub(r"\s*([.:])\s*", r"\1", normalized)

    # Handle "X pm" or "X am" where X is just hour
    # "4 pm" → "4:00 pm"
    hour_only_pattern = r"^(\d{1,2})\s+(am|pm)$"
    match = re.match(hour_only_pattern, normalized)
    if match:
        hour = match.group(1)
        meridiem = match.group(2)
        return f"{hour}:00 {meridiem}"

    # Handle "X.XX pm" or "X.XX am" (dot separator with meridiem)
    # "5.30 pm" → "5:30 pm"
    dot_with_meridiem_pattern = r"^(\d{1,2})\.(\d{2})\s+(am|pm)$"
    match = re.match(dot_with_meridiem_pattern, normalized)
    if match:
        hour = match.group(1)
        minute = match.group(2)
        meridiem = match.group(3)
        return f"{hour}:{minute} {meridiem}"

    # Handle "X.XX" (dot separator, no meridiem)
    # "5.30" → "5:30"
    dot_no_meridiem_pattern = r"^(\d{1,2})\.(\d{2})$"
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
    time_str_for_pattern = re.sub(r"\s+", "", time_str_normalized)

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


def combine_datetime_range(
    date_range: Optional[Dict[str, str]],
    time_range: Optional[Dict[str, str]],
    now: datetime,
    tz: Any,
    external_intent: Optional[str] = None,
) -> Optional[Dict[str, str]]:
    """
    Combine date range and time range into datetime range.

    Args:
        date_range: Dict with "start_date" and "end_date" (YYYY-MM-DD)
        time_range: Dict with "start_time" and "end_time" (HH:MM)
        now: Current datetime
        tz: Timezone object
        external_intent: External intent (CREATE_RESERVATION or CREATE_APPOINTMENT)

    Returns:
        Dict with "start" and "end" (ISO-8601) or None
    """
    if not date_range or not time_range:
        return None

    start_date = datetime.strptime(date_range["start_date"], "%Y-%m-%d")
    end_date = datetime.strptime(date_range["end_date"], "%Y-%m-%d")

    if not time_range.get("start_time") and not time_range.get("end_time"):
        return None

    def _parse_time(hhmm: Optional[str]) -> Optional[tuple[int, int]]:
        if not hhmm:
            return None
        parts = hhmm.split(":")
        return int(parts[0]), int(parts[1])

    start_parts = _parse_time(time_range.get("start_time"))
    end_parts = _parse_time(time_range.get("end_time")) or start_parts

    if start_parts:
        start_dt = _localize_datetime(
            start_date.replace(hour=start_parts[0], minute=start_parts[1]), tz
        )
    else:
        return None

    if end_parts:
        end_dt = _localize_datetime(
            end_date.replace(hour=end_parts[0], minute=end_parts[1]), tz
        )
    else:
        return None

    return {"start": start_dt.isoformat(), "end": end_dt.isoformat()}


def _apply_duration(
    datetime_range: Dict[str, str], duration: Dict[str, Any], tz: Any
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
            return {"start": datetime_range["start"], "end": end_dt.isoformat()}

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
        (
            r"(\d+)\s*(?:hour|hr|h)\s*(?:and\s+)?(\d+)\s*(?:minute|min|m)",
            lambda m: int(m.group(1)) * 60 + int(m.group(2)),
        ),
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
    duration: Optional[Dict[str, Any]] = None,
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
        # Get reason from clarification object if available
        reason_text = "Semantic ambiguity detected"
        if semantic_result.clarification:
            reason_text = semantic_result.clarification.reason.value
        reasons.append(reason_text)

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
                reasons.append("End datetime must be after or equal to start datetime")
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
                    reasons.append("End date must be after or equal to start date")
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
                        "Time range may span midnight - clarification needed"
                    )
            except (ValueError, IndexError):
                reasons.append("Invalid time range format")

    if reasons:
        return True, "; ".join(reasons)

    return False, None
