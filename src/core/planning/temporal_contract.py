"""
Canonical Temporal contract for Core.

NLU emits ``temporal``; Core planning/session/execution consume it only.
No inbound synthesis from legacy bags; no outbound constraint projections.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

_FUZZY_TIME_WINDOWS = {
    "morning": ("00:00", "11:59"),
    "afternoon": ("12:00", "16:59"),
    "evening": ("17:00", "21:59"),
    "night": ("21:00", "23:59"),
}

_TEMPORAL_KEYS = (
    "expression",
    "start_date_expression",
    "start_time_expression",
    "end_date_expression",
    "end_time_expression",
    "start_date",
    "start_time",
    "end_date",
    "end_time",
    "mode",
    "confidence",
    "resolution",
)


def empty_temporal(confidence: Optional[float] = None) -> Dict[str, Any]:
    return {
        "expression": None,
        "start_date_expression": None,
        "start_time_expression": None,
        "end_date_expression": None,
        "end_time_expression": None,
        "start_date": None,
        "start_time": None,
        "end_date": None,
        "end_time": None,
        "mode": "none",
        "confidence": confidence,
        "resolution": None,
    }


def _opt_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def normalize_temporal(raw: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """Normalise a Temporal dict (null-safe, known keys only)."""
    base = empty_temporal()
    if not isinstance(raw, dict):
        return base
    for key in _TEMPORAL_KEYS:
        if key not in raw:
            continue
        if key == "confidence":
            try:
                base[key] = float(raw[key]) if raw[key] is not None else None
            except (TypeError, ValueError):
                base[key] = None
        elif key == "mode":
            mode = _opt_str(raw.get("mode"))
            base["mode"] = (
                mode
                if mode in ("none", "single_day", "range", "flexible")
                else "none"
            )
        elif key == "resolution":
            resolution = raw.get("resolution")
            base["resolution"] = dict(resolution) if isinstance(resolution, dict) else None
        else:
            base[key] = _opt_str(raw.get(key))
    if base["mode"] in (None, "none"):
        base["mode"] = infer_mode(base)
    return base


def infer_mode(temporal: Mapping[str, Any]) -> str:
    mode = temporal.get("mode")
    if mode in ("single_day", "range", "flexible"):
        return mode
    if temporal.get("end_date") or temporal.get("end_date_expression"):
        return "range"
    if temporal.get("start_date") or temporal.get("start_date_expression"):
        return "single_day"
    return "none"


def temporal_has_date_material(temporal: Optional[Mapping[str, Any]]) -> bool:
    if not isinstance(temporal, dict):
        return False
    return bool(
        temporal.get("start_date")
        or temporal.get("start_date_expression")
        or temporal.get("end_date")
        or temporal.get("end_date_expression")
    )


def temporal_has_time_material(temporal: Optional[Mapping[str, Any]]) -> bool:
    if not isinstance(temporal, dict):
        return False
    return bool(
        temporal.get("start_time")
        or temporal.get("start_time_expression")
        or temporal.get("end_time")
        or temporal.get("end_time_expression")
        # Resolution is authoritative current-turn time evidence even when it
        # intentionally carries no copied clock value.
        or isinstance(temporal.get("resolution"), dict)
    )


def get_temporal(source: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """Load Temporal from a luma/session dict. Empty when absent (no legacy synthesize)."""
    if not isinstance(source, dict):
        return empty_temporal()

    raw = source.get("temporal")
    if isinstance(raw, dict):
        return normalize_temporal(raw)

    planning = source.get("planning")
    if isinstance(planning, dict) and isinstance(planning.get("temporal"), dict):
        return normalize_temporal(planning["temporal"])

    return empty_temporal()


def ensure_temporal(luma_response: Dict[str, Any]) -> Dict[str, Any]:
    """Attach normalised ``temporal`` on a response payload (no legacy projections)."""
    out = dict(luma_response)
    out["temporal"] = get_temporal(out)
    # Strip legacy temporal bags from internal Core payloads when present.
    out.pop("date_constraint", None)
    out.pop("time_constraint", None)
    return out


# Back-compat alias used by Stage 02 / requests during Phase 2.
attach_temporal = ensure_temporal


def date_proposal_from_temporal(
    temporal: Optional[Mapping[str, Any]],
) -> Optional[Dict[str, Any]]:
    if not isinstance(temporal, dict):
        return None
    start = temporal.get("start_date")
    end = temporal.get("end_date")
    mode = temporal.get("mode") or infer_mode(temporal)
    if not start and not end:
        return None
    if mode == "none":
        mode = "range" if end else "single_day"
    proposal: Dict[str, Any] = {"mode": mode}
    if start:
        proposal["start"] = start
    if end:
        proposal["end"] = end
    if not proposal.get("start"):
        return None
    return proposal


def time_proposal_from_temporal(
    temporal: Optional[Mapping[str, Any]],
) -> Optional[Dict[str, Any]]:
    if not isinstance(temporal, dict):
        return None
    resolution = temporal.get("resolution")
    kind = resolution.get("kind") if isinstance(resolution, dict) else None
    if kind in ("ambiguous_meridiem", "presented_option", "invalid_option_reference"):
        return None
    if temporal.get("start_time"):
        return {"mode": "exact", "value": temporal["start_time"]}
    label = (_opt_str(temporal.get("start_time_expression")) or "").lower()
    if label in _FUZZY_TIME_WINDOWS:
        start, end = _FUZZY_TIME_WINDOWS[label]
        return {"mode": "fuzzy", "label": label, "start": start, "end": end}
    return None


def exact_time_from_temporal(
    temporal: Optional[Mapping[str, Any]],
) -> Optional[str]:
    """Exact clock time (HH:MM) from Temporal, if present."""
    if not isinstance(temporal, dict):
        return None
    if temporal.get("start_time"):
        return str(temporal["start_time"])
    return None


def is_flexible_combined_utterance(
    temporal: Optional[Mapping[str, Any]] = None,
    facts: Optional[Mapping[str, Any]] = None,
    **_ignored: Any,
) -> bool:
    """True when vague date + service appear in the same NLU turn (Fix 4)."""
    facts = facts or {}
    if not isinstance(temporal, dict) or temporal.get("mode") != "flexible":
        return False
    if facts.get("service_id") is None:
        return False
    return temporal_has_date_material(temporal)


_DATE_FIELD_KEYS = (
    "start_date",
    "end_date",
    "start_date_expression",
    "end_date_expression",
)
_TIME_FIELD_KEYS = (
    "start_time",
    "end_time",
    "start_time_expression",
    "end_time_expression",
)


def merge_temporals(
    session_temporal: Optional[Mapping[str, Any]],
    current_temporal: Optional[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Fieldwise merge: current turn updates only the fields it owns.

    - Time-only current preserves session date fields.
    - Date-only current preserves session time fields.
    - New date/time material replaces the corresponding prior fields as a set
      (ISO + expression stay paired for that axis).
    - ``mode`` is re-inferred from the merged material.
    """
    session = (
        normalize_temporal(session_temporal)
        if isinstance(session_temporal, dict)
        else empty_temporal()
    )
    current = (
        normalize_temporal(current_temporal) if current_temporal else empty_temporal()
    )

    has_date = temporal_has_date_material(current)
    has_time = temporal_has_time_material(current)
    if not has_date and not has_time:
        return session if isinstance(session_temporal, dict) else current

    merged = dict(session)
    if has_date:
        for key in _DATE_FIELD_KEYS:
            merged[key] = current.get(key)
    if has_time:
        for key in _TIME_FIELD_KEYS:
            merged[key] = current.get(key)
        merged["resolution"] = current.get("resolution")

    # Prefer current overall expression when either axis updated; else keep session.
    if current.get("expression"):
        merged["expression"] = current.get("expression")
    elif has_date or has_time:
        # Rebuild a compact expression from merged axes when current omitted one.
        parts = []
        start_d = merged.get("start_date_expression") or merged.get("start_date")
        start_t = merged.get("start_time_expression") or merged.get("start_time")
        if start_d and start_t:
            parts.append(f"{start_d} at {start_t}")
        elif start_d:
            parts.append(str(start_d))
        elif start_t:
            parts.append(str(start_t))
        merged["expression"] = " ".join(parts) if parts else merged.get("expression")

    if current.get("confidence") is not None:
        merged["confidence"] = current.get("confidence")

    merged["mode"] = infer_mode(merged)
    return normalize_temporal(merged)
