"""Stage 03 — revision policy."""

from __future__ import annotations

from typing import Any, Dict, Optional

from core.planning.booking_revision import detect_booking_revision
from core.planning.pipeline.types import RevisionResult, WorkingTurn
from core.session.invalidation import InvalidationTrigger, apply_invalidation


def _normalize_date_value(value: Any) -> Optional[str]:
    if not value or not isinstance(value, str):
        return None
    return value.split("T")[0].split(" ")[0]


def _apply_field_aware_invalidation(
    working_turn: WorkingTurn,
    revision,
) -> None:
    """Clear stale booking fields after a detected service/date/time revision.

    Planning owns field-aware invalidation. Confirmation gate only consumes
    authorization; it must not branch on which booking field changed.
    """
    payload = working_turn.payload
    current_slots = dict(
        payload.get("_effective_collected_slots")
        or working_turn.effective_collected_slots
        or payload.get("slots")
        or {}
    )
    apply_invalidation(
        payload,
        InvalidationTrigger.BOOKING_REVISION,
        revision=revision,
        reason="planning_revision",
    )
    invalidated_slots = dict(payload.get("slots") or {})

    if revision.service:
        for key in ("service_id", "_canonical_service_id"):
            if current_slots.get(key) is not None:
                invalidated_slots[key] = current_slots[key]

    if revision.date:
        # New date often arrives only as date_proposal. Restore durable date
        # keys only when they already carry the revised value — never the
        # pre-revision session date that additive merge preserved.
        new_date = None
        for change in revision.changes:
            if change.field == "date":
                new_date = _normalize_date_value(change.to_value)
                break
        for key in ("date", "date_range", "start_date", "end_date"):
            value = current_slots.get(key)
            if value is None:
                continue
            if new_date and _normalize_date_value(str(value)) == new_date:
                invalidated_slots[key] = value

    payload["slots"] = invalidated_slots
    payload["_effective_collected_slots"] = invalidated_slots
    working_turn.effective_collected_slots = invalidated_slots

    if revision.service or revision.date:
        # Genuine mid-flow replacements only (first acquisition is not a
        # revision). Stale time proposals would rebind against the old presented
        # offers and undo availability invalidation.
        if not payload.get("_current_turn_has_time"):
            payload.pop("time_constraint", None)
            payload.pop("date_constraint", None)
            payload.pop("time_proposal", None)
            temporal = payload.get("temporal")
            if isinstance(temporal, dict):
                temporal = dict(temporal)
                temporal["start_time"] = None
                temporal["end_time"] = None
                temporal["start_time_expression"] = None
                temporal["end_time_expression"] = None
                payload["temporal"] = temporal
        # Service-only revision must keep the active search date_proposal so the
        # new service is searched on the same day. Date revisions arrive as a
        # current-turn proposal and replace the prior value without popping here.
        payload["_revision_invalidated_availability"] = True
        payload.pop("resolved_datetime_range", None)


def apply_revision_policy(
    working_turn: WorkingTurn,
    session_state: Optional[Dict[str, Any]],
) -> RevisionResult:
    revision = detect_booking_revision(working_turn.payload, session_state)
    revision_summary = None
    if revision.any:
        parts = []
        for change in revision.changes:
            parts.append(f"{change.field}: {change.from_value} → {change.to_value}")
        if parts:
            revision_summary = "; ".join(parts)
            working_turn.payload["_revision_summary"] = revision_summary
        # Time-only revisions are owned by merge bind/rebind. Re-clearing time
        # here would undo a successful offered-time bind on the same turn.
        # Service/date criteria changes must invalidate availability here.
        if revision.service or revision.date:
            _apply_field_aware_invalidation(working_turn, revision)
    return RevisionResult(revision=revision, revision_summary=revision_summary)
