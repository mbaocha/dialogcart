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
}


def _intent(direction: str, axis_hint: Optional[str] = "any") -> BrowseIntent:
    return {"direction": direction, "axis_hint": axis_hint}  # type: ignore[typeddict-item]


def normalize_availability_operation(raw: Any) -> Optional[BrowseIntent]:
    """Map an authoritative NLU browse operation to a BrowseIntent."""
    if not isinstance(raw, str):
        return None
    mapped = _BROWSE_OPERATIONS.get(raw)
    if not mapped:
        return None
    direction, axis = mapped
    return _intent(direction, axis)


def _operation_from_luma(luma_response: Dict[str, Any]) -> Any:
    return luma_response.get("operation")


def extract_availability_browse(
    luma_response: Optional[Dict[str, Any]],
) -> Optional[BrowseIntent]:
    """Read a browse signal from structured NLU fields on a Luma/merged response."""
    if not isinstance(luma_response, dict):
        return None
    return normalize_availability_operation(_operation_from_luma(luma_response))


def _has_cached_availability(session_state: Optional[Dict[str, Any]]) -> bool:
    from core.workflows.availability.presentation import has_trusted_availability_cache

    return has_trusted_availability_cache(session_state)


def resolve_browse_intent(
    merged: Optional[Dict[str, Any]],
    session_state: Optional[Dict[str, Any]] = None,
) -> Optional[BrowseIntent]:
    """Resolve BrowseIntent exclusively from the authoritative NLU operation."""
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

    return None


# Backward-compatible alias.
resolve_availability_browse = resolve_browse_intent


def cache_satisfiable_browse_request(
    merged: Optional[Dict[str, Any]],
    session_state: Optional[Dict[str, Any]] = None,
) -> Optional[BrowseIntent]:
    """Return BrowseIntent when page movement can use the trusted presentation set.

    Planner uses this to suppress ``SEARCH_AVAILABILITY`` only for structured
    ``browse_next`` / ``browse_previous`` when a trusted
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

    Browse is detected from the authoritative NLU ``operation`` on the
    current-turn response only — never carried forward from session state.
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
