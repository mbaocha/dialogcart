"""
Time constraint normalization helpers.

Minimal, pure helpers to normalize extracted time expressions into a canonical
TimeConstraint dict used across the pipeline:
{
  "mode": "exact" | "window" | "fuzzy",
  "start": Optional["HH:MM" or raw text],
  "end": Optional["HH:MM" or raw text],
  "label": Optional[str],
}

Note: This module intentionally avoids any natural language parsing beyond
the already-extracted tokens. It mirrors Phase 1 behavior: exact → same start/end,
range → window, window labels → fuzzy (no hour inference).
"""
from typing import Any, Dict, List, Optional

TimeConstraint = Dict[str, Any]

NAMED_TIMES = {
    "noon": "12:00",
    "midnight": "00:00",
}


def _normalize_named_time(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    v = str(value).strip().lower()
    return NAMED_TIMES.get(v, value)


def resolve_time_constraint(
    times: List[Dict[str, Any]],
    time_windows: List[Dict[str, Any]],
    time_type: str
) -> Optional[TimeConstraint]:
    """
    Normalize extracted time expressions into a canonical TimeConstraint.

    Behavior (preserves Phase 1 semantics):
    - time_type == "exact": first time -> mode=exact, start=end=text
    - time_type == "range": two times -> mode=window(start,end); one time -> window(start,None)
    - time_type == "window": first time_window -> mode=fuzzy, label=window text (no hour inference)
    - otherwise: None
    """
    times = times or []
    time_windows = time_windows or []

    if time_type == "exact":
        if times:
            first_raw = _normalize_named_time(times[0].get("text"))
            first = _to_hhmm(first_raw)
            if first is None:
                return None
            return {"mode": "exact", "start": first, "end": first, "label": None}

    if time_type == "range":
        if len(times) >= 2:
            start = _to_hhmm(_normalize_named_time(times[0].get("text")))
            end = _to_hhmm(_normalize_named_time(times[1].get("text")))
            if start is None or end is None:
                return None
            return {
                "mode": "window",
                "start": start,
                "end": end,
                "label": None,
            }
        elif times:
            # Handle composite single-token ranges like "2 to 5" or "2-5"
            start, end = _parse_range_from_text(times[0].get("text"))
            if start or end:
                return {"mode": "window", "start": start, "end": end, "label": None}
            start = _to_hhmm(_normalize_named_time(times[0].get("text")))
            if start is None:
                return None
            return {"mode": "window", "start": start, "end": None, "label": None}

    # Fallback: any time_window → fuzzy (no hour inference)
    if time_windows:
        label = time_windows[0].get("text")
        return {"mode": "fuzzy", "start": None, "end": None, "label": label}

    return None


def _to_hhmm(value: Optional[str]) -> Optional[str]:
    """
    Normalize a time string to HH:MM (24h) if possible.
    Supports:
    - "3 pm", "3pm", "03:00pm"
    - "15:30"
    - named times already normalized (12:00, 00:00)
    Returns None if not parseable.
    """
    if value is None:
        return None
    text = str(value).strip().lower().replace(".", ":")
    # Named times already normalized (contains ':')
    if ":" in text and any(c.isdigit() for c in text):
        # Handle possible am/pm suffix
        if text.endswith("am") or text.endswith("pm"):
            suffix = text[-2:]
            text = text[:-2].strip()
        else:
            suffix = None
        parts = text.split(":")
        try:
            hour = int(parts[0])
            minute = int(parts[1]) if len(parts) > 1 else 0
        except ValueError:
            return None
        if suffix:
            if suffix == "pm" and hour != 12:
                hour += 12
            if suffix == "am" and hour == 12:
                hour = 0
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return f"{hour:02d}:{minute:02d}"
        return None

    # Pattern: "<hour>am/pm" or "<hour> am/pm"
    import re

    match = re.match(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm)$", text)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2)) if match.group(2) else 0
        suffix = match.group(3)
        if suffix == "pm" and hour != 12:
            hour += 12
        if suffix == "am" and hour == 12:
            hour = 0
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return f"{hour:02d}:{minute:02d}"
        return None

    # Pattern: "<hour>" (unqualified) → assume HH:00 deterministically
    if re.match(r"^\d{1,2}$", text):
        hour = int(text)
        if 0 <= hour <= 23:
            return f"{hour:02d}:00"
        return None

    return None


def _parse_range_from_text(value: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """
    Parse simple hour-only ranges like "2 to 5", "2-5", "2 and 5" into HH:MM window.
    Returns (start, end) in HH:MM or (None, None) if not parseable.
    """
    if not value:
        return None, None
    import re

    text = str(value).lower().strip()
    m = re.match(r"^(\d{1,2})\s*(?:to|-|and)\s*(\d{1,2})$", text)
    if not m:
        return None, None
    h1, h2 = int(m.group(1)), int(m.group(2))
    if 0 <= h1 <= 23 and 0 <= h2 <= 23:
        return f"{h1:02d}:00", f"{h2:02d}:00"
    return None, None
