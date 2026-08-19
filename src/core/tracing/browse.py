"""Browse and pagination decision trace emitters (observational only)."""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

from core.tracing.decision_trace import Candidate, TurnTrace, decide, emit_evidence, emit_mutation
from core.tracing.reason_codes import (
    BROWSE_EXHAUSTED,
    BROWSE_NEXT,
    BROWSE_NOT_DETECTED,
    BROWSE_OPERATION_DETECTED,
    BROWSE_PREVIOUS,
    INPUT_IGNORED_NOT_APPLICABLE,
    PAGINATION_HANDLED,
    PAGINATION_SKIPPED,
)

BROWSE_EVIDENCE_SIGNAL_ID = "evidence.browse.signal"
PAGINATION_EVIDENCE_CACHE_ID = "evidence.session.cache"
PAGINATION_EVIDENCE_PAGES_ID = "evidence.presentation.pages"

BROWSE_RESOLVE_ID = "decision.browse.resolve_direction"
PAGINATION_HANDLE_ID = "decision.pagination.handle_turn"
PAGINATION_PAGE_TARGET_ID = "decision.pagination.page_target"

PAGINATION_NODE_IDS = (
    BROWSE_RESOLVE_ID,
    PAGINATION_HANDLE_ID,
    PAGINATION_PAGE_TARGET_ID,
)

PRESENTATION_CATEGORY = "presentation"
ROUTING_CATEGORY = "routing"


def pagination_dependencies() -> List[str]:
    trace = TurnTrace.current()
    if trace is None:
        return []
    return [node_id for node_id in PAGINATION_NODE_IDS if trace.has_record(node_id)]


def emit_browse_resolve_trace(
    *,
    merged: Optional[Mapping[str, Any]],
    browse: Optional[Mapping[str, Any]],
    source: str = "structured",
) -> Optional[str]:
    trace = TurnTrace.current()
    if trace is None:
        return None
    if trace.has_record(BROWSE_RESOLVE_ID):
        return BROWSE_RESOLVE_ID

    operation = None
    if isinstance(merged, dict):
        operation = merged.get("operation")
        facts = merged.get("facts")
        if isinstance(facts, dict) and facts.get("operation"):
            operation = facts.get("operation")

    if trace.has_record(BROWSE_EVIDENCE_SIGNAL_ID):
        signal_id = BROWSE_EVIDENCE_SIGNAL_ID
    else:
        signal_id = emit_evidence(
            "BROWSE_SIGNAL",
            subsystem="orchestration",
            facts={
                "operation": operation,
                "browse": dict(browse) if isinstance(browse, dict) else None,
                "source": source,
            },
            node_id=BROWSE_EVIDENCE_SIGNAL_ID,
            source="luma_response",
            observed_at_stage="pagination",
        )

    if browse and browse.get("direction") in ("next", "previous"):
        direction = browse["direction"]
        return decide(
            "BROWSE_RESOLVE",
            subsystem="orchestration",
            winner=direction,
            reason_code=BROWSE_OPERATION_DETECTED,
            reason_text=f"Browse direction {direction!r} from structured operation",
            node_id=BROWSE_RESOLVE_ID,
            depends_on=[signal_id] if signal_id else [],
            category=ROUTING_CATEGORY,
            candidates=[
                Candidate(
                    id="structured",
                    matched=source == "structured",
                    reason_code=BROWSE_OPERATION_DETECTED,
                    reason_text="Structured NLU operation field",
                ),
            ],
        )

    return decide(
        "BROWSE_RESOLVE",
        subsystem="orchestration",
        winner=None,
        reason_code=BROWSE_NOT_DETECTED,
        reason_text="No browse direction resolved",
        node_id=BROWSE_RESOLVE_ID,
        depends_on=[signal_id] if signal_id else [],
        category=ROUTING_CATEGORY,
        skipped=True,
    )


def emit_pagination_handle_trace(
    *,
    handled: bool,
    skip_reason: Optional[str] = None,
    direction: Optional[str] = None,
    session_state: Optional[Mapping[str, Any]] = None,
    browse_resolve_id: Optional[str] = None,
) -> Optional[str]:
    if TurnTrace.current() is None:
        return None

    session_state = session_state if isinstance(session_state, dict) else {}
    from core.workflows.availability.presentation import availability_cache_from_session

    cache = availability_cache_from_session(session_state)
    from core.workflows.availability.presentation import (
        availability_pagination_from_session,
    )

    presentation = availability_pagination_from_session(session_state) or {}

    cache_id = emit_evidence(
        "SESSION_CACHE",
        subsystem="session",
        facts={
            "last_execution_type": cache.get("type") if isinstance(cache, dict) else None,
            "last_execution_status": cache.get("status") if isinstance(cache, dict) else None,
            "cached_slot_count": len(cache.get("slots") or []) if isinstance(cache, dict) else 0,
            "page_index": presentation.get("page_index"),
        },
        node_id=PAGINATION_EVIDENCE_CACHE_ID,
        source="session_state",
        observed_at_stage="pagination",
    )

    deps = [dep for dep in (browse_resolve_id, cache_id) if dep]

    if handled:
        return decide(
            "PAGINATION_HANDLE",
            subsystem="orchestration",
            winner="handled",
            reason_code=PAGINATION_HANDLED,
            reason_text=f"Pagination handled browse direction {direction!r}",
            node_id=PAGINATION_HANDLE_ID,
            depends_on=deps,
            category=PRESENTATION_CATEGORY,
            inputs_evaluated={"direction": direction},
        )

    return decide(
        "PAGINATION_HANDLE",
        subsystem="orchestration",
        winner="skipped",
        reason_code=PAGINATION_SKIPPED,
        reason_text=f"Pagination not handled: {skip_reason or 'unknown'}",
        node_id=PAGINATION_HANDLE_ID,
        depends_on=deps,
        category=PRESENTATION_CATEGORY,
        skipped=True,
        inputs_evaluated={"skip_reason": skip_reason},
    )


def emit_pagination_page_target_trace(
    *,
    current_index: int,
    target_index: int,
    direction: str,
    exhausted: bool,
    page_size: int,
    total_slots: int,
    pagination_handle_id: Optional[str] = None,
) -> None:
    if TurnTrace.current() is None:
        return None

    pages_id = emit_evidence(
        "PRESENTATION_PAGES",
        subsystem="orchestration",
        facts={
            "current_index": current_index,
            "target_index": target_index,
            "direction": direction,
            "exhausted": exhausted,
            "page_size": page_size,
            "total_unique_slots": total_slots,
        },
        node_id=PAGINATION_EVIDENCE_PAGES_ID,
        source="compute_target_page_index",
        observed_at_stage="pagination",
    )

    deps = [dep for dep in (pagination_handle_id, pages_id) if dep]
    reason_code = BROWSE_EXHAUSTED if exhausted else (
        BROWSE_NEXT if direction == "next" else BROWSE_PREVIOUS
    )

    decision_id = decide(
        "PAGE_TARGET",
        subsystem="orchestration",
        winner={"page_index": target_index, "exhausted": exhausted},
        reason_code=reason_code,
        reason_text=(
            f"Browse {direction} exhausted at page {current_index}"
            if exhausted
            else f"Browse {direction} to page {target_index}"
        ),
        node_id=PAGINATION_PAGE_TARGET_ID,
        depends_on=deps,
        category=PRESENTATION_CATEGORY,
        inputs_evaluated={
            "current_index": current_index,
            "target_index": target_index,
            "direction": direction,
            "exhausted": exhausted,
        },
    )

    if decision_id and not exhausted and target_index != current_index:
        emit_mutation(
            decision_id,
            subsystem="orchestration",
            field="availability_presentation.page_index",
            previous=current_index,
            new=target_index,
            reason_code=reason_code,
            reason_text=f"Updated page index via browse {direction}",
            presentation_only=True,
        )
