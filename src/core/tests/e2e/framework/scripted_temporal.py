"""Canonical Temporal builders for scripted E2E NLU payloads.

Matches the Stage2 / ``nlu.temporal.models.Temporal`` contract so Core
admission (``get_temporal`` / ``extract_nlu_proposals``) receives the same
shape as live NLU — without legacy ``time_constraint`` / ``date_constraint``.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


def scripted_temporal(
    *,
    start_date: Optional[str] = None,
    start_time: Optional[str] = None,
    end_date: Optional[str] = None,
    end_time: Optional[str] = None,
    start_date_expression: Optional[str] = None,
    start_time_expression: Optional[str] = None,
    end_date_expression: Optional[str] = None,
    end_time_expression: Optional[str] = None,
    expression: Optional[str] = None,
    mode: Optional[str] = None,
    confidence: float = 1.0,
) -> Dict[str, Any]:
    """Build a full Temporal dict (all Stage2 keys present, nulls preserved)."""
    if mode is None:
        if end_date or end_date_expression:
            mode = "range"
        elif start_date or start_date_expression:
            mode = "single_day"
        else:
            mode = "none"

    if expression is None:
        parts = []
        start_d = start_date_expression or start_date
        start_t = start_time_expression or start_time
        end_d = end_date_expression or end_date
        end_t = end_time_expression or end_time
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
        expression = " ".join(parts) if parts else None

    return {
        "expression": expression,
        "start_date_expression": start_date_expression,
        "start_time_expression": start_time_expression,
        "end_date_expression": end_date_expression,
        "end_time_expression": end_time_expression,
        "start_date": start_date,
        "start_time": start_time,
        "end_date": end_date,
        "end_time": end_time,
        "mode": mode,
        "confidence": confidence,
    }


def exact_time_temporal(time_value: str, *, confidence: float = 1.0) -> Dict[str, Any]:
    """Time-only utterance (e.g. ``10am``) — Stage2 mode ``none`` + start_time."""
    return scripted_temporal(
        start_time=time_value,
        end_time=None,
        mode="none",
        confidence=confidence,
    )


def single_day_temporal(
    date_value: str,
    *,
    start_time: Optional[str] = None,
    confidence: float = 1.0,
) -> Dict[str, Any]:
    """Resolved single calendar day, optionally with an exact clock time."""
    return scripted_temporal(
        start_date=date_value,
        start_time=start_time,
        end_time=start_time,
        mode="single_day",
        confidence=confidence,
    )
