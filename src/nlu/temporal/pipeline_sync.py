"""
Pipeline helpers: Temporal as primary internal representation.

Legacy bags may still be projected at the NLU HTTP API boundary only.
"""

from __future__ import annotations

from typing import Any, Dict

from .models import Temporal
from .project_legacy import project_legacy_from_temporal
from .stage2_output import empty_temporal_dict, parse_temporal_dict


def get_temporal(slm: Dict[str, Any]) -> Temporal:
    """Load Temporal from slm. Empty when absent (no legacy synthesize)."""
    raw = slm.get("temporal")
    confidence = slm.get("confidence")
    if isinstance(confidence, dict):
        confidence = None
    if isinstance(raw, dict):
        return parse_temporal_dict(raw, confidence=confidence)
    return parse_temporal_dict(
        empty_temporal_dict(
            float(confidence) if isinstance(confidence, (int, float)) else 0.0
        ),
        confidence=confidence if isinstance(confidence, (int, float)) else None,
    )


def apply_temporal(slm: Dict[str, Any], temporal: Temporal) -> Dict[str, Any]:
    """Write Temporal; project legacy fields for API/compat consumers only."""
    legacy = project_legacy_from_temporal(temporal)
    facts = dict(slm.get("facts") or {})
    facts["dates"] = legacy["dates"]
    facts["times"] = legacy["times"]
    facts["date_time_pairs"] = legacy["date_time_pairs"]
    return {
        **slm,
        "temporal": temporal.to_dict(),
        "facts": facts,
        "time_constraint": legacy["time_constraint"],
    }


def temporal_has_date_material(temporal: Temporal) -> bool:
    return bool(
        temporal.start_date
        or temporal.start_date_expression
        or temporal.end_date
        or temporal.end_date_expression
    )


def temporal_has_time_material(temporal: Temporal) -> bool:
    return bool(
        temporal.start_time
        or temporal.start_time_expression
        or temporal.end_time
        or temporal.end_time_expression
    )


def clear_temporal_dates(temporal: Temporal) -> Temporal:
    return Temporal(
        expression=None,
        start_date_expression=None,
        start_time_expression=temporal.start_time_expression,
        end_date_expression=None,
        end_time_expression=temporal.end_time_expression,
        start_date=None,
        start_time=temporal.start_time,
        end_date=None,
        end_time=temporal.end_time,
        mode="none",
        confidence=temporal.confidence,
    )


def infer_date_mode_from_temporal(temporal: Temporal) -> str:
    """Prefer explicit Temporal.mode; else infer for API date_constraint projection."""
    if temporal.mode in ("single_day", "range", "flexible"):
        return temporal.mode
    if temporal.end_date or temporal.end_date_expression:
        return "range"
    if temporal.start_date or temporal.start_date_expression:
        return "single_day"
    return "none"
