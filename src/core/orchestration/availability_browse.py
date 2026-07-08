"""Transient availability browse signals from structured Luma operations."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_BROWSE_OPERATIONS = {
    "browse_next": "next",
    "browse_previous": "previous",
}

_BROWSE_NEXT_PHRASES = (
    "show more",
    "show me additional",
    "additional time",
    "additional times",
    "more time",
    "more times",
    "other time",
    "other times",
    "next time",
    "next page",
    "later time",
    "later times",
    "see more",
    "any more",
    "what else",
)

_BROWSE_PREVIOUS_PHRASES = (
    "earlier time",
    "earlier times",
    "previous page",
    "previous time",
    "go back",
    "before that",
)


def normalize_availability_operation(raw: Any) -> Optional[Dict[str, str]]:
    """Normalize Luma ``operation`` to ``{"direction": "next"|"previous"}``."""
    if raw is None:
        return None
    operation = str(raw).strip().lower().replace("-", "_")
    if not operation:
        return None
    direction = _BROWSE_OPERATIONS.get(operation)
    if not direction:
        return None
    return {"direction": direction}


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
) -> Optional[Dict[str, str]]:
    """Read explicit ``availability_browse`` from NLU or merge."""
    browse_field = luma_response.get("availability_browse")
    if not isinstance(browse_field, dict):
        return None
    direction = browse_field.get("direction")
    if direction in ("next", "previous"):
        return {"direction": str(direction)}
    return None


def extract_availability_browse(
    luma_response: Optional[Dict[str, Any]],
) -> Optional[Dict[str, str]]:
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
    if not isinstance(session_state, dict):
        return False
    last_result = session_state.get("last_execution_result")
    return (
        isinstance(last_result, dict)
        and last_result.get("type") == "availability"
        and last_result.get("status") == "success"
        and bool(last_result.get("slots"))
    )


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


def infer_browse_direction_from_text(text: str) -> Optional[Dict[str, str]]:
    """Infer browse direction from user text when NLU omits structured operation."""
    lowered = (text or "").lower().strip()
    if not lowered:
        return None
    for phrase in _BROWSE_PREVIOUS_PHRASES:
        if phrase in lowered:
            return {"direction": "previous"}
    for phrase in _BROWSE_NEXT_PHRASES:
        if phrase in lowered:
            return {"direction": "next"}
    if "additional" in lowered and any(
        token in lowered for token in ("time", "times", "slot", "show")
    ):
        return {"direction": "next"}
    return None


def resolve_availability_browse(
    merged: Optional[Dict[str, Any]],
    session_state: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, str]]:
    """Resolve browse direction from NLU contract fields or safe text fallback."""
    if not isinstance(merged, dict):
        return None

    browse = extract_availability_browse(merged)
    if browse:
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
            "[AVAILABILITY_BROWSE] inferred direction=%s from cached availability "
            "and user text (luma_intent=%s session_intent=%s)",
            inferred.get("direction"),
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
        merged["availability_browse"] = browse
        logger.info(
            "[AVAILABILITY_BROWSE] detected direction=%s",
            browse.get("direction"),
        )
    else:
        merged.pop("availability_browse", None)
