"""
Build Temporal from legacy Stage2 fields (fallback / round-trip).

Primary Stage2 path is LLM → Temporal → project_legacy.
This module supports transitional tool payloads and tests.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from .models import Temporal

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_HHMM_RE = re.compile(r"^\d{1,2}:\d{2}$")


def _as_str_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if item is not None and str(item).strip()]


def _is_iso_date(value: str) -> bool:
    return bool(_ISO_DATE_RE.match(value.split("T")[0].split(" ")[0]))


def _iso_date_only(value: str) -> str:
    return value.split("T")[0].split(" ")[0]


def _is_hhmm(value: str) -> bool:
    return bool(_HHMM_RE.match(value))


def _assign_date(
    value: Optional[str],
) -> tuple[Optional[str], Optional[str]]:
    """Return (canonical_iso, expression) for a Stage2 date string."""
    if not value:
        return None, None
    raw = str(value).strip()
    if not raw:
        return None, None
    if _is_iso_date(raw):
        return _iso_date_only(raw), None
    return None, raw


def _assign_time(
    value: Optional[str],
) -> tuple[Optional[str], Optional[str]]:
    """Return (canonical_hhmm, expression) for a Stage2 time string."""
    if not value:
        return None, None
    raw = str(value).strip()
    if not raw:
        return None, None
    if _is_hhmm(raw):
        hour, minute = raw.split(":")
        return f"{int(hour):02d}:{minute}", None
    return None, raw


def _build_expression(
    start_date_expression: Optional[str],
    start_date: Optional[str],
    start_time_expression: Optional[str],
    start_time: Optional[str],
    end_date_expression: Optional[str],
    end_date: Optional[str],
    end_time_expression: Optional[str],
    end_time: Optional[str],
) -> Optional[str]:
    start_d = start_date_expression or start_date
    start_t = start_time_expression or start_time
    end_d = end_date_expression or end_date
    end_t = end_time_expression or end_time

    parts: List[str] = []
    if start_d and start_t:
        parts.append(f"{start_d} at {start_t}")
    elif start_d:
        parts.append(start_d)
    elif start_t:
        parts.append(start_t)

    if end_d or end_t:
        if end_d and end_t:
            parts.append(f"until {end_d} at {end_t}")
        elif end_d:
            parts.append(f"until {end_d}")
        else:
            parts.append(f"until {end_t}")

    if not parts:
        return None
    return " ".join(parts)


def build_temporal_from_stage2(
    facts: Optional[Dict[str, Any]] = None,
    time_constraint: Optional[Dict[str, Any]] = None,
    confidence: Optional[float] = None,
) -> Temporal:
    """Project Stage2 facts + time_constraint into a Temporal object."""
    facts = facts if isinstance(facts, dict) else {}
    dates = _as_str_list(facts.get("dates"))
    times = _as_str_list(facts.get("times"))
    pairs = facts.get("date_time_pairs")
    if not isinstance(pairs, list):
        pairs = []

    start_date = start_date_expression = None
    end_date = end_date_expression = None
    start_time = start_time_expression = None
    end_time = end_time_expression = None

    if pairs:
        first = pairs[0] if isinstance(pairs[0], dict) else {}
        start_date, start_date_expression = _assign_date(first.get("date"))
        start_time, start_time_expression = _assign_time(first.get("time"))

    if start_date is None and start_date_expression is None and dates:
        start_date, start_date_expression = _assign_date(dates[0])
    if len(dates) >= 2 and end_date is None and end_date_expression is None:
        end_date, end_date_expression = _assign_date(dates[1])

    if start_time is None and start_time_expression is None and times:
        start_time, start_time_expression = _assign_time(times[0])
    if len(times) >= 2 and end_time is None and end_time_expression is None:
        end_time, end_time_expression = _assign_time(times[1])

    if isinstance(time_constraint, dict):
        mode = time_constraint.get("mode")
        if mode == "exact":
            if start_time is None and start_time_expression is None:
                start_time, start_time_expression = _assign_time(
                    time_constraint.get("start")
                )
            if end_time is None and end_time_expression is None:
                end_raw = time_constraint.get("end")
                # Exact point-in-time often repeats start as end — only keep distinct end.
                if end_raw and str(end_raw).strip() != str(
                    time_constraint.get("start") or ""
                ).strip():
                    end_time, end_time_expression = _assign_time(end_raw)
        elif mode == "fuzzy":
            label = time_constraint.get("label")
            if label and start_time_expression is None and start_time is None:
                start_time_expression = str(label).strip() or None

    expression = _build_expression(
        start_date_expression,
        start_date,
        start_time_expression,
        start_time,
        end_date_expression,
        end_date,
        end_time_expression,
        end_time,
    )

    conf: Optional[float] = None
    if confidence is not None:
        try:
            conf = float(confidence)
        except (TypeError, ValueError):
            conf = None

    mode = None
    if end_date or end_date_expression:
        phrase = (start_date_expression or end_date_expression or "").lower()
        if ("week" in phrase and "weekend" not in phrase) or "weekend" in phrase:
            mode = "flexible"
        else:
            mode = "range"
    elif start_date or start_date_expression:
        phrase = (start_date_expression or "").lower()
        if ("week" in phrase and "weekend" not in phrase) or "weekend" in phrase:
            mode = "flexible"
        else:
            mode = "single_day"
    else:
        mode = "none"

    return Temporal(
        expression=expression,
        start_date_expression=start_date_expression,
        start_time_expression=start_time_expression,
        end_date_expression=end_date_expression,
        end_time_expression=end_time_expression,
        start_date=start_date,
        start_time=start_time,
        end_date=end_date,
        end_time=end_time,
        mode=mode,
        confidence=conf,
    )
