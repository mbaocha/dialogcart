"""Slot binding decision trace emitters (observational only)."""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

from core.tracing.decision_trace import Candidate, TurnTrace, decide, emit_evidence, emit_mutation
from core.tracing.reason_codes import (
    BIND_EXACT_TIME_MATCH,
    BIND_NO_EXACT_TIME,
    BIND_NO_PRESENTED_OFFERS,
    BIND_NO_USER_TIME,
    BIND_TIME_MISMATCH,
    BIND_DATE_MISMATCH,
    BIND_PARSE_FAILED,
)

BIND_EVIDENCE_TIME_PROPOSAL_ID = "evidence.time_proposal"
BIND_EVIDENCE_PRESENTED_OFFERS_ID = "evidence.presented.offers"
BIND_TIME_DECISION_ID = "decision.merge.bind_time"

BIND_NODE_IDS = (BIND_TIME_DECISION_ID,)

ROUTING_CATEGORY = "routing"


def bind_dependencies() -> List[str]:
    trace = TurnTrace.current()
    if trace is None:
        return []
    return [node_id for node_id in BIND_NODE_IDS if trace.has_record(node_id)]


def _offer_summaries(offers: List[Any], *, limit: int = 8) -> List[str]:
    summaries: List[str] = []
    for offer in offers[:limit]:
        if isinstance(offer, dict):
            start = offer.get("starts_at") or offer.get("start")
            summaries.append(str(start))
    return summaries


def emit_bind_time_trace(
    *,
    slots_before: Mapping[str, Any],
    bind_result: Optional[Mapping[str, Any]],
    skip_reason: Optional[str] = None,
    time_proposal: Optional[Mapping[str, Any]] = None,
    temporal: Optional[Mapping[str, Any]] = None,
    offers: Optional[List[Any]] = None,
    user_time_raw: Optional[str] = None,
    user_time_norm: Optional[str] = None,
    expected_date: Optional[str] = None,
    matched_offer_start: Optional[str] = None,
) -> None:
    """Emit bind-time evidence, decision, and slot mutations."""
    if TurnTrace.current() is None:
        return

    offers = offers or []
    _ = temporal
    time_evidence = emit_evidence(
        "TIME_PROPOSAL",
        subsystem="orchestration",
        facts={
            "time_proposal": dict(time_proposal) if isinstance(time_proposal, dict) else None,
            "temporal": dict(temporal) if isinstance(temporal, dict) else None,
            "user_time_raw": user_time_raw,
            "user_time_norm": user_time_norm,
            "expected_date": expected_date,
        },
        node_id=BIND_EVIDENCE_TIME_PROPOSAL_ID,
        source="luma_response",
        observed_at_stage="merge",
    )

    offers_evidence = emit_evidence(
        "PRESENTED_OFFERS",
        subsystem="session",
        facts={
            "offer_count": len(offers),
            "offer_starts": _offer_summaries(offers),
            "search_date": expected_date,
        },
        node_id=BIND_EVIDENCE_PRESENTED_OFFERS_ID,
        source="presented_availability",
        observed_at_stage="merge",
    )

    deps = [dep for dep in (time_evidence, offers_evidence) if dep]

    candidates: List[Candidate] = []
    if not offers:
        candidates.append(
            Candidate(
                id="no_offers",
                matched=False,
                reason_code=BIND_NO_PRESENTED_OFFERS,
                reason_text="No presented availability offers",
            )
        )
    if not user_time_raw:
        candidates.append(
            Candidate(
                id="no_user_time",
                matched=False,
                reason_code=BIND_NO_USER_TIME,
                reason_text="No exact user time from proposal or constraint",
            )
        )

    if bind_result:
        winner = "bound"
        reason_code = BIND_EXACT_TIME_MATCH
        reason_text = f"Bound offered time {matched_offer_start!r}"
        bound_slots = bind_result.get("slots") if isinstance(bind_result.get("slots"), dict) else {}
        decision_id = decide(
            "BIND_TIME",
            subsystem="orchestration",
            winner=winner,
            reason_code=reason_code,
            reason_text=reason_text,
            node_id=BIND_TIME_DECISION_ID,
            depends_on=deps,
            candidates=candidates,
            category=ROUTING_CATEGORY,
            inputs_evaluated={
                "user_time_norm": user_time_norm,
                "expected_date": expected_date,
                "matched_offer_start": matched_offer_start,
            },
        )
        if decision_id:
            prev_date = slots_before.get("date")
            prev_time = slots_before.get("time")
            new_date = bound_slots.get("date")
            new_time = bound_slots.get("time")
            if new_date != prev_date:
                emit_mutation(
                    decision_id,
                    subsystem="orchestration",
                    field="slots.date",
                    previous=prev_date,
                    new=new_date,
                    reason_code=BIND_EXACT_TIME_MATCH,
                    reason_text="Bound date from presented offer",
                )
            if new_time != prev_time:
                emit_mutation(
                    decision_id,
                    subsystem="orchestration",
                    field="slots.time",
                    previous=prev_time,
                    new=new_time,
                    reason_code=BIND_EXACT_TIME_MATCH,
                    reason_text="Bound time from presented offer",
                )
        return

    reason_map = {
        "no_offers": (BIND_NO_PRESENTED_OFFERS, "No presented offers to bind against"),
        "no_user_time": (BIND_NO_USER_TIME, "No exact user time to bind"),
        "normalize_failed": ("BIND_NORMALIZE_FAILED", "User time failed normalization"),
        "time_mismatch": (BIND_TIME_MISMATCH, "No offer matched user time"),
        "date_mismatch": (BIND_DATE_MISMATCH, "Offer date did not match presentation date"),
        "parse_failed": (BIND_PARSE_FAILED, "Offer start could not be parsed"),
    }
    code, text = reason_map.get(skip_reason or "", ("BIND_SKIPPED", "Time binding skipped"))
    decide(
        "BIND_TIME",
        subsystem="orchestration",
        winner="skipped",
        reason_code=code,
        reason_text=text,
        node_id=BIND_TIME_DECISION_ID,
        depends_on=deps,
        candidates=candidates,
        category=ROUTING_CATEGORY,
        skipped=True,
        inputs_evaluated={"skip_reason": skip_reason},
    )
