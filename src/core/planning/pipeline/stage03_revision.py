"""Stage 03 — revision policy."""

from __future__ import annotations

from typing import Any, Dict, Optional

from core.planning.booking_revision import detect_booking_revision
from core.planning.pipeline.types import RevisionResult, WorkingTurn
from core.planning.planning_mutations import apply_booking_revision_mutations


def apply_revision_policy(
    working_turn: WorkingTurn,
    session_state: Optional[Dict[str, Any]],
) -> RevisionResult:
    payload = working_turn.payload
    entity_schema = (
        payload.get("_entity_schema")
        if isinstance(payload.get("_entity_schema"), dict)
        else None
    )
    revision = detect_booking_revision(
        payload, session_state, entity_schema=entity_schema
    )
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
        # Search-criteria changes must invalidate availability here.
        if revision.invalidates_availability:
            apply_booking_revision_mutations(
                working_turn,
                revision,
                reason="planning_revision",
            )
    return RevisionResult(revision=revision, revision_summary=revision_summary)
