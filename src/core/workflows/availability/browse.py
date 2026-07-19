"""Transient availability browse signals from structured Luma operations."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from core.workflows.availability.contracts import BrowseIntent

logger = logging.getLogger(__name__)

_BROWSE_OPERATIONS = {
    "browse_next": ("next", "any"),
    "browse_previous": ("previous", "any"),
    "browse_next_day": ("next", "date"),
    "browse_previous_day": ("previous", "date"),
    "browse_next_times": ("next", "time"),
    "browse_previous_times": ("previous", "time"),
}

# Most specific phrases first.
_DATE_NEXT_PHRASES = (
    "next day",
    "next available day",
    "following day",
    "another day",
)
_DATE_PREV_PHRASES = (
    "previous day",
    "prev day",
    "earlier day",
)
_TIME_NEXT_PHRASES = (
    "more times",
    "more time",
    "additional times",
    "additional time",
    "later times",
    "later time",
)
_TIME_PREV_PHRASES = (
    "earlier times",
    "earlier time",
    "previous time",
    "previous page",
)
_ANY_NEXT_PHRASES = (
    "show more",
    "show me additional",
    "other time",
    "other times",
    "next time",
    "next page",
    "see more",
    "any more",
    "what else",
)
_ANY_PREV_PHRASES = (
    "go back",
    "before that",
)


def _intent(direction: str, axis_hint: Optional[str] = "any") -> BrowseIntent:
    return {"direction": direction, "axis_hint": axis_hint}  # type: ignore[typeddict-item]


def normalize_availability_operation(raw: Any) -> Optional[BrowseIntent]:
    """Normalize Luma ``operation`` to a BrowseIntent."""
    if raw is None:
        return None
    operation = str(raw).strip().lower().replace("-", "_")
    if not operation:
        return None
    mapped = _BROWSE_OPERATIONS.get(operation)
    if not mapped:
        # Legacy bare directions
        if operation in ("next", "previous"):
            return _intent(operation, "any")
        return None
    direction, axis = mapped
    return _intent(direction, axis)


def _operation_from_luma(luma_response: Dict[str, Any]) -> Any:
    raw = luma_response.get("operation")
    if raw is not None:
        return raw
    facts = luma_response.get("facts")
    if isinstance(facts, dict) and facts.get("operation") is not None:
        return facts.get("operation")
    return None


def _browse_from_availability_browse_field(
    luma_response: Dict[str, Any],
) -> Optional[BrowseIntent]:
    """Read explicit ``availability_browse`` from NLU or merge."""
    browse_field = luma_response.get("availability_browse")
    if not isinstance(browse_field, dict):
        return None
    direction = browse_field.get("direction")
    if direction not in ("next", "previous"):
        return None
    axis = browse_field.get("axis_hint") or browse_field.get("axis")
    if axis not in ("any", "time", "date", None):
        axis = "any"
    return _intent(str(direction), axis or "any")


def extract_availability_browse(
    luma_response: Optional[Dict[str, Any]],
) -> Optional[BrowseIntent]:
    """Read a browse signal from structured NLU fields on a Luma/merged response."""
    if not isinstance(luma_response, dict):
        return None
    explicit = _browse_from_availability_browse_field(luma_response)
    if explicit:
        return explicit
    return normalize_availability_operation(_operation_from_luma(luma_response))


def _luma_intent_name(luma_response: Dict[str, Any]) -> str:
    """Original/effective Luma intent name (before durable session override)."""
    raw = luma_response.get("_raw_luma_response")
    if isinstance(raw, dict):
        intent = raw.get("intent", {})
        if isinstance(intent, dict) and intent.get("name"):
            return str(intent["name"]).upper()
    intent = luma_response.get("intent", {})
    if isinstance(intent, dict) and intent.get("name"):
        return str(intent["name"]).upper()
    return ""


def _has_cached_availability(session_state: Optional[Dict[str, Any]]) -> bool:
    from core.workflows.availability.presentation import has_trusted_availability_cache

    return has_trusted_availability_cache(session_state)


def _session_intent_name(session_state: Dict[str, Any]) -> str:
    session_intent = session_state.get("intent_name")
    if session_intent is None:
        session_intent = session_state.get("intent")
    if isinstance(session_intent, dict):
        return str(session_intent.get("name") or "").strip().upper()
    if isinstance(session_intent, str):
        return session_intent.strip().upper()
    return ""


def _session_has_durable_booking_intent(
    session_state: Optional[Dict[str, Any]],
) -> bool:
    """True when session carries an active durable CREATE_APPOINTMENT booking flow."""
    if not isinstance(session_state, dict):
        return False
    session_intent = _session_intent_name(session_state)
    if session_intent != "CREATE_APPOINTMENT":
        return False
    try:
        from core.policy.intent_policy import get_intent_durable

        return bool(get_intent_durable(session_intent))
    except (ImportError, Exception):
        return True


def _allows_browse_text_fallback(
    merged: Dict[str, Any],
    session_state: Optional[Dict[str, Any]],
) -> bool:
    """Allow text inference for AVAILABILITY turns or active booking continuation."""
    if _luma_intent_name(merged) == "AVAILABILITY":
        return True
    return _session_has_durable_booking_intent(session_state)


def infer_browse_direction_from_text(text: str) -> Optional[BrowseIntent]:
    """Infer BrowseIntent from user text when NLU omits structured operation."""
    lowered = (text or "").lower().strip()
    if not lowered:
        return None

    for phrase in _DATE_PREV_PHRASES:
        if phrase in lowered:
            return _intent("previous", "date")
    for phrase in _DATE_NEXT_PHRASES:
        if phrase in lowered:
            return _intent("next", "date")
    for phrase in _TIME_PREV_PHRASES:
        if phrase in lowered:
            return _intent("previous", "time")
    for phrase in _TIME_NEXT_PHRASES:
        if phrase in lowered:
            return _intent("next", "time")
    for phrase in _ANY_PREV_PHRASES:
        if phrase in lowered:
            return _intent("previous", "any")
    for phrase in _ANY_NEXT_PHRASES:
        if phrase in lowered:
            return _intent("next", "any")
    if "additional" in lowered and any(
        token in lowered for token in ("time", "times", "slot", "show")
    ):
        return _intent("next", "time")
    return None


def resolve_browse_intent(
    merged: Optional[Dict[str, Any]],
    session_state: Optional[Dict[str, Any]] = None,
) -> Optional[BrowseIntent]:
    """Resolve BrowseIntent from NLU contract fields or safe text fallback."""
    if not isinstance(merged, dict):
        return None

    browse = extract_availability_browse(merged)
    if browse:
        # Structured NLU often only supplies direction; refine axis from text when present.
        if (browse.get("axis_hint") or "any") == "any":
            source_text = merged.get("_source_text") or ""
            inferred = infer_browse_direction_from_text(str(source_text))
            if inferred and inferred.get("axis_hint") in ("time", "date"):
                browse = {
                    "direction": browse.get("direction") or inferred.get("direction"),
                    "axis_hint": inferred.get("axis_hint"),
                }
        try:
            from core.tracing.browse import emit_browse_resolve_trace

            emit_browse_resolve_trace(
                merged=merged,
                browse=browse,
                source="structured",
            )
        except ImportError:
            pass
        return browse

    if not _has_cached_availability(session_state):
        return None

    if not _allows_browse_text_fallback(merged, session_state):
        return None

    source_text = merged.get("_source_text") or ""
    inferred = infer_browse_direction_from_text(str(source_text))
    if inferred:
        logger.info(
            "[AVAILABILITY_BROWSE] inferred direction=%s axis=%s from cached availability "
            "and user text (luma_intent=%s session_intent=%s)",
            inferred.get("direction"),
            inferred.get("axis_hint"),
            _luma_intent_name(merged) or None,
            _session_intent_name(session_state) if isinstance(session_state, dict) else None,
        )
        try:
            from core.tracing.browse import emit_browse_resolve_trace

            emit_browse_resolve_trace(
                merged=merged,
                browse=inferred,
                source="text_fallback",
                text_inferred=True,
            )
        except ImportError:
            pass
        return inferred

    return None


# Backward-compatible alias.
resolve_availability_browse = resolve_browse_intent


def apply_availability_browse_signal(
    merged: Dict[str, Any],
    luma_response: Dict[str, Any],
) -> None:
    """Attach a transient per-turn browse signal to the merged response.

    Browse is detected from NLU ``operation``, ``facts.operation``, or
    ``availability_browse`` on the current-turn Luma response only — never
    carried forward from session state.
    """
    browse = extract_availability_browse(luma_response)
    if browse:
        # Prefer text-refined axis when structured signal is direction-only.
        source_text = merged.get("_source_text") or luma_response.get("_source_text") or ""
        inferred = infer_browse_direction_from_text(str(source_text))
        if inferred and inferred.get("axis_hint") in ("time", "date"):
            browse = {
                "direction": browse.get("direction") or inferred.get("direction"),
                "axis_hint": inferred.get("axis_hint"),
            }
        merged["availability_browse"] = browse
        logger.info(
            "[AVAILABILITY_BROWSE] detected direction=%s axis=%s",
            browse.get("direction"),
            browse.get("axis_hint"),
        )
    else:
        merged.pop("availability_browse", None)
