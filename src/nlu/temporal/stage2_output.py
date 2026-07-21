"""
Stage2 temporal ownership helpers.

Primary path: LLM → Temporal → legacy projections.
Fallback: legacy LLM fields → Temporal → legacy (behaviour-preserving).
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from .models import Temporal
from .project_legacy import project_legacy_from_temporal


def _opt_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _opt_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_temporal_dict(
    raw: Optional[Dict[str, Any]],
    confidence: Optional[float] = None,
) -> Temporal:
    """Parse a Temporal tool payload into a Temporal dataclass."""
    raw = raw if isinstance(raw, dict) else {}
    conf = _opt_float(raw.get("confidence"))
    if conf is None:
        conf = _opt_float(confidence)

    expression = _opt_str(raw.get("expression"))
    start_date_expression = _opt_str(raw.get("start_date_expression"))
    start_time_expression = _opt_str(raw.get("start_time_expression"))
    end_date_expression = _opt_str(raw.get("end_date_expression"))
    end_time_expression = _opt_str(raw.get("end_time_expression"))
    start_date = _opt_str(raw.get("start_date"))
    start_time = _opt_str(raw.get("start_time"))
    end_date = _opt_str(raw.get("end_date"))
    end_time = _opt_str(raw.get("end_time"))
    mode = _opt_str(raw.get("mode"))
    if mode and mode not in ("none", "single_day", "range", "flexible"):
        mode = None

    if expression is None:
        from .from_stage2 import _build_expression

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


def resolve_temporal_from_tool_input(
    raw: Dict[str, Any],
    confidence: Optional[float] = None,
) -> Temporal:
    """Prefer canonical temporal from the tool payload; else empty Temporal."""
    conf = _opt_float(confidence)
    if conf is None:
        conf = _opt_float(raw.get("confidence"))

    temporal_raw = raw.get("temporal") if isinstance(raw.get("temporal"), dict) else {}
    return parse_temporal_dict(temporal_raw, conf)


def materialize_temporal_ownership(
    raw: Dict[str, Any],
    confidence: Optional[float] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any], Optional[Dict[str, Any]]]:
    """
    Own temporal understanding in Temporal; project legacy compatibility fields.

    Returns:
        (temporal_dict, legacy_facts_fragment, time_constraint)
        legacy_facts_fragment has dates/times/date_time_pairs only.
    """
    temporal = resolve_temporal_from_tool_input(raw, confidence=confidence)
    legacy = project_legacy_from_temporal(temporal)
    facts_fragment = {
        "dates": legacy["dates"],
        "times": legacy["times"],
        "date_time_pairs": legacy["date_time_pairs"],
    }
    return temporal.to_dict(), facts_fragment, legacy["time_constraint"]


def empty_temporal_dict(confidence: float = 0.0) -> Dict[str, Any]:
    return Temporal(confidence=float(confidence)).to_dict()
