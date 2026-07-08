"""Session merge decision trace emitters (observational only)."""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Set

from core.tracing.decision_trace import Candidate, IgnoredInput, TurnTrace, decide, emit_evidence, emit_mutation
from core.tracing.reason_codes import (
    INPUT_IGNORED_NOT_APPLICABLE,
    MERGE_ELIGIBLE,
    MERGE_SKIPPED,
    SLOT_ADDITIVE_MERGE,
    SLOT_PRESERVED_DURABLE,
)

MERGE_EVIDENCE_DURABLE_FLOW_ID = "evidence.session.durable_flow"
MERGE_EVIDENCE_SLOT_DIFF_ID = "evidence.merge.slot_diff"
MERGE_ELIGIBILITY_ID = "decision.merge.eligibility"
MERGE_SLOT_ADDITIVE_ID = "decision.merge.slot_additive"

MERGE_NODE_IDS = (
    MERGE_ELIGIBILITY_ID,
    MERGE_SLOT_ADDITIVE_ID,
)

ROUTING_CATEGORY = "routing"


def merge_dependencies() -> List[str]:
    trace = TurnTrace.current()
    if trace is None:
        return []
    return [node_id for node_id in MERGE_NODE_IDS if trace.has_record(node_id)]


def _slot_diff(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
) -> Dict[str, List[str]]:
    before_keys = {k for k, v in before.items() if v is not None}
    after_keys = {k for k, v in after.items() if v is not None}
    added = sorted(after_keys - before_keys)
    preserved = sorted(before_keys & after_keys)
    updated: List[str] = []
    for key in before_keys & after_keys:
        if before.get(key) != after.get(key):
            updated.append(key)
    dropped = sorted(before_keys - after_keys)
    return {
        "added": added,
        "preserved": preserved,
        "updated": updated,
        "dropped": dropped,
    }


def emit_merge_eligibility_trace(
    *,
    eligible: bool,
    session_reset_occurred: bool,
    intent_name: str = "",
    has_session_slots: bool = False,
) -> Optional[str]:
    trace = TurnTrace.current()
    if trace is None:
        return None
    if trace.has_record(MERGE_ELIGIBILITY_ID):
        return MERGE_ELIGIBILITY_ID

    durable_id = emit_evidence(
        "DURABLE_FLOW",
        subsystem="session",
        facts={
            "intent_name": intent_name,
            "session_reset_occurred": session_reset_occurred,
            "has_session_slots": has_session_slots,
        },
        node_id=MERGE_EVIDENCE_DURABLE_FLOW_ID,
        source="should_merge_session_context",
        observed_at_stage="merge",
    )
    deps = [durable_id] if durable_id else []
    return decide(
        "MERGE_ELIGIBILITY",
        subsystem="session",
        winner="merge" if eligible else "skip",
        reason_code=MERGE_ELIGIBLE if eligible else MERGE_SKIPPED,
        reason_text=(
            "Durable session flow eligible for merge"
            if eligible
            else "Session merge skipped (no durable flow or reset occurred)"
        ),
        node_id=MERGE_ELIGIBILITY_ID,
        depends_on=deps,
        category=ROUTING_CATEGORY,
        inputs_evaluated={
            "intent_name": intent_name,
            "session_reset_occurred": session_reset_occurred,
            "has_session_slots": has_session_slots,
        },
    )


def emit_merge_slot_trace(
    *,
    session_slots: Mapping[str, Any],
    merged_slots: Mapping[str, Any],
    merge_eligibility_id: Optional[str] = None,
    ignored_luma_keys: Optional[Set[str]] = None,
) -> None:
    """Emit slot merge diff evidence, decision, and per-slot mutations."""
    trace = TurnTrace.current()
    if trace is None:
        return
    if trace.has_record(MERGE_SLOT_ADDITIVE_ID):
        return

    diff = _slot_diff(session_slots, merged_slots)
    diff_id = emit_evidence(
        "SLOT_DIFF",
        subsystem="session",
        facts=diff,
        node_id=MERGE_EVIDENCE_SLOT_DIFF_ID,
        source="merge_luma_with_session",
        observed_at_stage="merge",
    )

    deps = [dep for dep in (merge_eligibility_id, diff_id) if dep]
    from core.tracing.binding import bind_dependencies

    deps.extend(bind_dependencies())

    durable_keys = set(session_slots.keys()) & set(merged_slots.keys())
    candidates = [
        Candidate(
            id="preserve_durable",
            matched=bool(durable_keys),
            reason_code=SLOT_PRESERVED_DURABLE,
            reason_text="Existing session slot values preserved unless explicitly updated",
        ),
        Candidate(
            id="additive_luma",
            matched=bool(diff["added"]),
            reason_code=SLOT_ADDITIVE_MERGE,
            reason_text="New Luma entities merged additively",
        ),
    ]

    ignored: Dict[str, IgnoredInput] = {}
    for key in ignored_luma_keys or set():
        ignored[key] = IgnoredInput(
            reason_code=INPUT_IGNORED_NOT_APPLICABLE,
            reason_text="Luma field not merged into durable slots",
        )

    decision_id = decide(
        "SLOT_ADDITIVE_MERGE",
        subsystem="session",
        winner={
            "added": diff["added"],
            "updated": diff["updated"],
            "preserved_count": len(diff["preserved"]),
        },
        reason_code=SLOT_ADDITIVE_MERGE,
        reason_text="Merged session and Luma slots with additive precedence",
        node_id=MERGE_SLOT_ADDITIVE_ID,
        depends_on=deps,
        candidates=candidates,
        category=ROUTING_CATEGORY,
        inputs_evaluated={
            "session_slot_keys": sorted(session_slots.keys()),
            "merged_slot_keys": sorted(merged_slots.keys()),
        },
        inputs_ignored=ignored,
    )

    if not decision_id:
        return

    for key in diff["added"] + diff["updated"]:
        emit_mutation(
            decision_id,
            subsystem="session",
            field=f"slots.{key}",
            previous=session_slots.get(key),
            new=merged_slots.get(key),
            reason_code=SLOT_ADDITIVE_MERGE,
            reason_text=f"Merged slot {key!r}",
        )

    for key in diff["dropped"]:
        emit_mutation(
            decision_id,
            subsystem="session",
            field=f"slots.{key}",
            previous=session_slots.get(key),
            new=None,
            reason_code=SLOT_ADDITIVE_MERGE,
            reason_text=f"Dropped slot {key!r} during merge",
        )
