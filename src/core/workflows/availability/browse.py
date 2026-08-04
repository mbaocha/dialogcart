"""Transient availability browse signals from structured Luma operations.

Browse is pure page movement inside a criteria-shaped presentation result set.
Date changes belong to SEARCH_AVAILABILITY — never Browse.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from core.workflows.availability.contracts import BrowseIntent

logger = logging.getLogger(__name__)

_BROWSE_OPERATIONS = {
    "browse_next": ("next", "any"),
    "browse_previous": ("previous", "any"),
    # Legacy synonyms still map to page movement only (no date axis).
    "browse_next_times": ("next", "any"),
    "browse_previous_times": ("previous", "any"),
}

# Deliberately small alias set. Date phrases are NOT browse.
_ANY_NEXT_PHRASES = (
    "show more",
    "more",
    "next",
)
_ANY_PREV_PHRASES = (
    "show previous",
    "previous",
    "back",
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
    if axis not in ("any", "time", None):
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


def _exact_phrase_match(lowered: str, phrase: str) -> bool:
    """True when *phrase* is the whole utterance or a clear token sequence."""
    text = " ".join(lowered.split())
    target = " ".join(phrase.split())
    if text == target:
        return True
    # Allow light punctuation wrappers: "next." / "show more!"
    stripped = text.strip(".,!? ")
    return stripped == target


def infer_browse_direction_from_text(text: str) -> Optional[BrowseIntent]:
    """Infer BrowseIntent from a small alias set when NLU omits structured operation.

    Date navigation phrases (next day, previous day, later date, …) are never browse.
    """
    lowered = (text or "").lower().strip()
    if not lowered:
        return None

    # Reject date-axis language explicitly — these are SEARCH semantics.
    date_axis_markers = (
        "next day",
        "previous day",
        "prev day",
        "following day",
        "another day",
        "later date",
        "earlier date",
        "next available day",
        "earlier day",
    )
    if any(marker in lowered for marker in date_axis_markers):
        return None

    for phrase in _ANY_PREV_PHRASES:
        if _exact_phrase_match(lowered, phrase):
            return _intent("previous", "any")
    for phrase in _ANY_NEXT_PHRASES:
        if _exact_phrase_match(lowered, phrase):
            return _intent("next", "any")
    return None


def resolve_browse_intent(
    merged: Optional[Dict[str, Any]],
    session_state: Optional[Dict[str, Any]] = None,
) -> Optional[BrowseIntent]:
    """Resolve BrowseIntent from NLU contract fields or safe text fallback."""
    if not isinstance(merged, dict):
        return None

    # Absolute date / changed temporal criterion → SEARCH ownership, not browse.
    if merged.get("_current_turn_has_date") and merged.get("_current_turn_date"):
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


# Backward-compatible alias.
resolve_availability_browse = resolve_browse_intent


def cache_satisfiable_browse_request(
    merged: Optional[Dict[str, Any]],
    session_state: Optional[Dict[str, Any]] = None,
) -> Optional[BrowseIntent]:
    """Return BrowseIntent when page movement can use the trusted presentation set.

    Planner uses this to suppress ``SEARCH_AVAILABILITY`` only for structured
    ``browse_next`` / ``browse_previous`` (or the small alias set) when a trusted
    cache exists. Absolute dates and temporal criterion changes are never
    cache-satisfiable browse — they require SEARCH.
    """
    if not _has_cached_availability(session_state):
        return None
    if isinstance(merged, dict):
        try:
            from core.planning.temporal_proposal import build_selection_user_facts
            from core.workflows.availability.selection import search_criteria_changed

            entity_schema = (
                merged.get("_entity_schema")
                if isinstance(merged.get("_entity_schema"), dict)
                else None
            )
            if search_criteria_changed(
                user_facts=build_selection_user_facts(merged),
                session_state=session_state,
                entity_schema=entity_schema,
            ):
                return None
        except ImportError:
            pass

        # Explicit absolute date this turn → SEARCH, not browse suppress.
        if merged.get("_current_turn_has_date") and merged.get("_current_turn_date"):
            return None

    browse = resolve_browse_intent(merged, session_state)
    if not browse:
        return None
    return browse


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
            "[AVAILABILITY_BROWSE] detected direction=%s axis=%s",
            browse.get("direction"),
            browse.get("axis_hint"),
        )
    else:
        merged.pop("availability_browse", None)
