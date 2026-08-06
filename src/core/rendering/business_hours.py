"""
Deterministic business-hours normalization for conversational rendering.

Commerce owns the raw schedule. This module converts it into an unambiguous
representation for Business Knowledge prompts so the LLM never infers open
days from ambiguous ``isOpen`` / clock objects.
"""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional, Sequence, Tuple

from core.rendering.business_policies import apply_policy_summaries

_DAY_NAMES = (
    "Sunday",
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
)

_DAY_INDEX = {name.lower(): i for i, name in enumerate(_DAY_NAMES)}
_DAY_INDEX.update(
    {
        "sun": 0,
        "mon": 1,
        "tue": 2,
        "tues": 2,
        "wed": 3,
        "thu": 4,
        "thur": 4,
        "thurs": 4,
        "fri": 5,
        "sat": 6,
    }
)

_RAW_HOURS_KEYS = ("hours", "business_hours", "opening_hours")


def _format_clock(raw: Any) -> Optional[str]:
    """Format ``HH:MM`` / ``H:MM`` as ``H:MM AM/PM``."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    parts = text.split(":")
    if len(parts) < 2:
        return text
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError:
        return text
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        return text
    suffix = "AM" if hour < 12 else "PM"
    display_hour = hour % 12
    if display_hour == 0:
        display_hour = 12
    return f"{display_hour}:{minute:02d} {suffix}"


def _day_index(raw: Any) -> Optional[int]:
    if isinstance(raw, int) and 0 <= raw <= 6:
        return raw
    if isinstance(raw, str):
        return _DAY_INDEX.get(raw.strip().lower())
    return None


def _entry_hours_label(entry: Dict[str, Any]) -> Optional[str]:
    start = _format_clock(entry.get("startTime", entry.get("start")))
    end = _format_clock(entry.get("endTime", entry.get("end")))
    if start and end:
        return f"{start}–{end}"
    return start or end


def _is_open(entry: Dict[str, Any]) -> bool:
    if "isOpen" in entry:
        return bool(entry.get("isOpen"))
    if "is_open" in entry:
        return bool(entry.get("is_open"))
    # Presence of start/end alone is not enough — closed days often retain clocks.
    return False


def _parse_day_entries(raw: Any) -> Optional[List[Dict[str, Any]]]:
    """
    Parse Commerce-like schedules into normalized day entries.

    Supports:
    - list[{dayOfWeek|day, isOpen, startTime|start, endTime|end}, ...]
    - dict{monday: {isOpen, start, end}, ...}
    """
    entries: List[Dict[str, Any]] = []

    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                return None
            day = _day_index(item.get("dayOfWeek", item.get("day")))
            if day is None:
                return None
            entries.append(
                {
                    "day": day,
                    "is_open": _is_open(item),
                    "hours": _entry_hours_label(item) if _is_open(item) else None,
                }
            )
    elif isinstance(raw, dict):
        # Reject already-simple string maps like {"mon": "9am-6pm"} — not isOpen objects.
        looked_like_schedule = False
        for key, value in raw.items():
            day = _day_index(key)
            if day is None:
                continue
            if not isinstance(value, dict):
                return None
            if not any(k in value for k in ("isOpen", "is_open", "start", "startTime", "end", "endTime")):
                return None
            looked_like_schedule = True
            open_flag = _is_open(value)
            entries.append(
                {
                    "day": day,
                    "is_open": open_flag,
                    "hours": _entry_hours_label(value) if open_flag else None,
                }
            )
        if not looked_like_schedule:
            return None
    else:
        return None

    if not entries:
        return None

    by_day: Dict[int, Dict[str, Any]] = {}
    for entry in entries:
        by_day[entry["day"]] = entry

    # Fill missing weekdays as unknown/closed only when we have a full-week signal.
    ordered = [by_day[i] for i in range(7) if i in by_day]
    return ordered


def _monday_first_key(day: int) -> int:
    """Sort key: Monday…Saturday, then Sunday (natural business-week order)."""
    return (day - 1) % 7


def _sorted_days(day_indexes: Sequence[int]) -> List[int]:
    return sorted(day_indexes, key=_monday_first_key)


def _group_consecutive(day_indexes: Sequence[int]) -> List[Tuple[int, int]]:
    """
    Group consecutive weekdays for phrasing.

    Uses calendar adjacency (Sun=0…Sat=6). Sunday+Saturday stay separate so
    weekends read as \"Saturday and Sunday\" rather than a wrap-around span.
    """
    if not day_indexes:
        return []
    calendar_ordered = sorted(day_indexes)
    groups: List[Tuple[int, int]] = []
    start = prev = calendar_ordered[0]
    for day in calendar_ordered[1:]:
        if day == prev + 1:
            prev = day
            continue
        groups.append((start, prev))
        start = prev = day
    groups.append((start, prev))
    groups.sort(key=lambda pair: _monday_first_key(pair[0]))
    return groups


def _format_day_span(start: int, end: int) -> str:
    if start == end:
        return _DAY_NAMES[start]
    if end == start + 1:
        return f"{_DAY_NAMES[start]} and {_DAY_NAMES[end]}"
    return f"{_DAY_NAMES[start]} through {_DAY_NAMES[end]}"


def _format_day_list(day_indexes: Sequence[int]) -> str:
    groups = _group_consecutive(day_indexes)
    parts = [_format_day_span(start, end) for start, end in groups]
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return f"{parts[0]} and {parts[1]}"
    return ", ".join(parts[:-1]) + f", and {parts[-1]}"


def _build_opening_summary(
    open_days: Sequence[int],
    closed_days: Sequence[int],
    hour_groups: Sequence[Tuple[Tuple[int, ...], str]],
) -> str:
    if not open_days:
        if closed_days:
            return "Closed every day."
        return "Opening hours are unavailable."

    if len(hour_groups) == 1:
        days, hours = hour_groups[0]
        open_part = f"Open {_format_day_list(days)}, {hours}."
    else:
        segments = [
            f"{_format_day_list(days)} {hours}" for days, hours in hour_groups
        ]
        open_part = "Open " + "; ".join(segments) + "."

    if not closed_days:
        if len(open_days) == 7 and len(hour_groups) == 1:
            _, hours = hour_groups[0]
            return f"Open every day, {hours}."
        return open_part

    closed_part = f"Closed {_format_day_list(closed_days)}."
    return f"{open_part} {closed_part}"


def normalize_business_hours(raw: Any) -> Optional[Dict[str, Any]]:
    """
    Convert a raw Commerce schedule into a deterministic rendering structure.

    Returns None when ``raw`` is not an isOpen-style schedule (so callers leave
    already-readable hours untouched).
    """
    parsed = _parse_day_entries(raw)
    if parsed is None:
        return None

    open_entries = [e for e in parsed if e["is_open"]]
    closed_entries = [e for e in parsed if not e["is_open"]]

    open_indexes = _sorted_days([e["day"] for e in open_entries])
    closed_indexes = _sorted_days([e["day"] for e in closed_entries])
    open_days = [_DAY_NAMES[d] for d in open_indexes]
    closed_days = [_DAY_NAMES[d] for d in closed_indexes]

    # Group open days that share identical hours labels.
    buckets: Dict[str, List[int]] = {}
    for entry in open_entries:
        label = entry["hours"] or "hours unavailable"
        buckets.setdefault(label, []).append(entry["day"])

    hour_groups: List[Tuple[Tuple[int, ...], str]] = [
        (tuple(_sorted_days(days)), label) for label, days in buckets.items()
    ]
    # Stable Monday-first order
    hour_groups.sort(key=lambda item: _monday_first_key(item[0][0]) if item[0] else 99)

    result: Dict[str, Any] = {
        "open_days": open_days,
        "closed_days": closed_days,
        "opening_summary": _build_opening_summary(
            open_indexes, closed_indexes, hour_groups
        ),
    }

    if len(hour_groups) == 1 and open_days:
        result["hours"] = hour_groups[0][1]
    elif hour_groups:
        result["schedule"] = [
            {"days": [_DAY_NAMES[d] for d in days], "hours": label}
            for days, label in hour_groups
        ]

    return result


def _extract_raw_schedule(structured_context: Dict[str, Any]) -> Tuple[Optional[str], Any]:
    """Return (key, value) for the first recognizable raw hours field."""
    for key in _RAW_HOURS_KEYS:
        if key in structured_context:
            return key, structured_context.get(key)
    return None, None


def prepare_structured_context_for_render(
    structured_context: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Build a Business Knowledge view for the LLM prompt.

    Adds deterministic hours and policy summaries when raw Commerce objects are
    present, and removes those ambiguous raw objects from the prompt view.
    The caller's original `structured_context` is left unchanged.
    """
    if not isinstance(structured_context, dict) or not structured_context:
        return structured_context

    prepared = copy.deepcopy(structured_context)
    key, raw = _extract_raw_schedule(prepared)
    if key is not None:
        normalized = normalize_business_hours(raw)
        if normalized is not None:
            # Prefer normalized summary for conversational text; hide ambiguous raw.
            prepared.pop(key, None)
            prepared["opening_hours"] = normalized

    apply_policy_summaries(prepared)
    return prepared
