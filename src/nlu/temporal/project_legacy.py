"""
Project legacy Stage2 temporal fields from the canonical Temporal model.

Used so Pipeline / CalendarBinder / Core keep consuming dates, times,
date_time_pairs, and time_constraint unchanged.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..config.temporal import FUZZY_TIME_WINDOWS
from .models import Temporal

_FUZZY_LABELS = frozenset(FUZZY_TIME_WINDOWS.keys())


def _date_token(
    canonical: Optional[str], expression: Optional[str]
) -> Optional[str]:
    """Legacy dates[] prefer canonical ISO (business); expression only if unresolved."""
    if canonical:
        return canonical
    if expression:
        return expression
    return None


def project_legacy_from_temporal(temporal: Temporal) -> Dict[str, Any]:
    """
    Build compatibility fields from Temporal.

    Returns:
        {
          "dates": [...],
          "times": [...],
          "date_time_pairs": [...],
          "time_constraint": {...} | None,
        }
    """
    start_tok = _date_token(temporal.start_date, temporal.start_date_expression)
    end_tok = _date_token(temporal.end_date, temporal.end_date_expression)

    dates: List[str] = []
    if start_tok:
        dates.append(start_tok)
    if end_tok:
        dates.append(end_tok)

    times: List[str] = []
    if temporal.start_time:
        times.append(temporal.start_time)

    date_time_pairs: List[Dict[str, str]] = []
    # Match prior Stage2 rule: pair only when date + exact clock time co-occur.
    if start_tok and temporal.start_time:
        date_time_pairs.append({"date": start_tok, "time": temporal.start_time})

    time_constraint = _project_time_constraint(temporal)

    return {
        "dates": dates,
        "times": times,
        "date_time_pairs": date_time_pairs,
        "time_constraint": time_constraint,
    }


def _project_time_constraint(temporal: Temporal) -> Optional[Dict[str, Any]]:
    fuzzy_label = (temporal.start_time_expression or "").strip().lower()
    if fuzzy_label in _FUZZY_LABELS:
        start, end = FUZZY_TIME_WINDOWS[fuzzy_label]
        return {
            "mode": "fuzzy",
            "start": start,
            "end": end,
            "label": fuzzy_label,
        }

    if temporal.start_time:
        end = temporal.end_time or temporal.start_time
        return {
            "mode": "exact",
            "start": temporal.start_time,
            "end": end,
            "label": None,
        }

    return None
