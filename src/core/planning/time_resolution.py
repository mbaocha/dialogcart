"""Deterministic time resolution after SEARCH_AVAILABILITY.

Compares an exact ``time_proposal`` against normalized availability offers.
The LLM renderer consumes the structured result; it does not decide matches.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from core.workflows.availability.fingerprint import _normalize_time_for_fingerprint
from core.planning.temporal_proposal import (
    _derive_presentation_date_from_offers,
    _filter_offers_to_search_date,
    _normalize_search_date,
    _parse_offer_start_parts,
    build_datetime_range_from_slots,
    get_presented_availability_offers,
)
from core.rendering.availability_renderer import dedupe_availability_slots

logger = logging.getLogger(__name__)

TIME_MATCH_EXACT = "TIME_MATCH_EXACT"
TIME_MATCH_MISMATCH = "TIME_MATCH_MISMATCH"
TIME_MATCH_NOT_APPLICABLE = "TIME_MATCH_NOT_APPLICABLE"

_UNSET = object()

TimeResolutionStatus = str


def _exact_requested_time(time_proposal: Optional[Dict[str, Any]]) -> Optional[str]:
    if not isinstance(time_proposal, dict):
        return None
    if time_proposal.get("mode") != "exact":
        return None
    raw = time_proposal.get("value")
    if not raw:
        return None
    return _normalize_time_for_fingerprint(raw)


def _resolve_search_date(
    *,
    search_date: Optional[str],
    date_proposal: Optional[Dict[str, Any]],
    offers: List[Dict[str, Any]],
) -> Optional[str]:
    normalized = _normalize_search_date(search_date)
    if normalized:
        return normalized
    if isinstance(date_proposal, dict):
        start = date_proposal.get("start")
        if isinstance(start, str) and start:
            return _normalize_search_date(start)
    return _derive_presentation_date_from_offers(offers)


def _collect_offer_starts(
    offers: List[Dict[str, Any]],
    *,
    expected_date: Optional[str],
) -> List[str]:
    """Return deduped ISO start timestamps, optionally filtered to one day."""
    unique_slots, unique_starts, _ = dedupe_availability_slots(offers)
    if not unique_starts:
        return []
    if not expected_date:
        return list(unique_starts)
    filtered: List[str] = []
    for start in unique_starts:
        parsed = _parse_offer_start_parts(start)
        if parsed and parsed[0] == expected_date:
            filtered.append(start)
        elif not parsed and start.startswith(expected_date):
            filtered.append(start)
    return filtered


def _build_bind_result(
    *,
    slots: Dict[str, Any],
    offer: Dict[str, Any],
    offer_date: str,
    user_time_norm: str,
) -> Dict[str, Any]:
    start_raw = offer.get("starts_at") or offer.get("start")
    end_raw = offer.get("ends_at") or offer.get("end")
    bound_slots = dict(slots or {})
    bound_slots["date"] = offer_date
    bound_slots["time"] = user_time_norm
    resolved_datetime_range = None
    if start_raw and end_raw:
        resolved_datetime_range = {
            "start": str(start_raw), "end": str(end_raw)}
    else:
        resolved_datetime_range = build_datetime_range_from_slots(
            bound_slots, None)
    if not resolved_datetime_range:
        return {}
    return {
        "slots": bound_slots,
        "resolved_datetime_range": resolved_datetime_range,
    }


def resolve_time_after_availability(
    *,
    offers: List[Dict[str, Any]],
    time_proposal: Optional[Dict[str, Any]],
    date_proposal: Optional[Dict[str, Any]] = None,
    search_date: Optional[str] = None,
    slots: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Resolve whether an exact ``time_proposal`` is satisfied by search offers.

    Returns:
        {
            "time_resolution": {...} | None,
            "bind_result": {...} | None,
        }

    When ``time_proposal`` is absent or not exact, outcome is
    ``TIME_MATCH_NOT_APPLICABLE`` and no binding is performed.
    """
    requested_norm = _exact_requested_time(time_proposal)
    if not requested_norm:
        return {
            "time_resolution": {"outcome": TIME_MATCH_NOT_APPLICABLE},
            "bind_result": None,
        }

    offer_list = [o for o in (offers or []) if isinstance(o, dict)]
    expected_date = _resolve_search_date(
        search_date=search_date,
        date_proposal=date_proposal,
        offers=offer_list,
    )
    candidates = offer_list
    if expected_date:
        filtered = _filter_offers_to_search_date(offer_list, expected_date)
        if filtered:
            candidates = filtered

    alternatives = _collect_offer_starts(
        candidates, expected_date=expected_date)

    for offer in candidates:
        start_raw = offer.get("starts_at") or offer.get("start")
        parsed = _parse_offer_start_parts(start_raw)
        if not parsed:
            continue
        offer_date, offer_time = parsed
        if offer_time != requested_norm:
            continue
        if expected_date and offer_date != expected_date:
            continue
        bind_result = _build_bind_result(
            slots=slots or {},
            offer=offer,
            offer_date=offer_date,
            user_time_norm=requested_norm,
        )
        if not bind_result:
            continue
        resolution = {
            "outcome": TIME_MATCH_EXACT,
            "requested_time": requested_norm,
            "matched_offer": str(start_raw),
        }
        logger.info(
            "[TIME_RESOLUTION] %s requested=%s matched=%s date=%s",
            TIME_MATCH_EXACT,
            requested_norm,
            start_raw,
            offer_date,
        )
        _emit_time_resolution_trace(resolution, alternatives=alternatives)
        return {"time_resolution": resolution, "bind_result": bind_result}

    resolution = {
        "outcome": TIME_MATCH_MISMATCH,
        "requested_time": requested_norm,
        "alternatives": alternatives,
    }
    logger.info(
        "[TIME_RESOLUTION] %s requested=%s alternatives=%s expected_date=%s",
        TIME_MATCH_MISMATCH,
        requested_norm,
        len(alternatives),
        expected_date,
    )
    _emit_time_resolution_trace(resolution, alternatives=alternatives)
    return {"time_resolution": resolution, "bind_result": None}


def _patch_plan_container(
    plan: Dict[str, Any],
    *,
    status: str,
    stage: Any = _UNSET,
    action: Any = _UNSET,
    awaiting: Any = _UNSET,
    time_match_outcome: Optional[str] = None,
    time_resolution: Optional[Dict[str, Any]] = None,
    bound_slots: Optional[Dict[str, Any]] = None,
    resolved_range: Optional[Dict[str, Any]] = None,
) -> None:
    plan["status"] = status
    if time_match_outcome is not None:
        plan["time_match_outcome"] = time_match_outcome
    if time_resolution is not None:
        plan["time_resolution"] = time_resolution
    if bound_slots is not None:
        plan["slots"] = bound_slots
    if resolved_range is not None:
        plan["resolved_datetime_range"] = resolved_range

    plan_obj = plan.get("plan")
    if not isinstance(plan_obj, dict):
        plan_obj = {}
        plan["plan"] = plan_obj
    plan_obj["status"] = status
    if stage is not _UNSET:
        plan_obj["stage"] = stage
        plan["stage"] = stage
    if action is not _UNSET:
        plan_obj["action"] = action
        plan["action"] = action
    if awaiting is not _UNSET:
        plan_obj["awaiting"] = awaiting
        plan["awaiting"] = awaiting

    decision = plan.get("_decision")
    if isinstance(decision, dict):
        decision["status"] = status
        decision_plan = decision.get("plan")
        if not isinstance(decision_plan, dict):
            decision_plan = {}
            decision["plan"] = decision_plan
        decision_plan["status"] = status
        if stage is not _UNSET:
            decision_plan["stage"] = stage
        if action is not _UNSET:
            decision_plan["action"] = action
        if awaiting is not _UNSET:
            decision_plan["awaiting"] = awaiting
        if time_match_outcome is not None:
            decision["time_match_outcome"] = time_match_outcome
        if time_resolution is not None:
            decision["time_resolution"] = time_resolution
        facts = decision.get("facts")
        if isinstance(facts, dict):
            if bound_slots is not None:
                facts["slots"] = dict(bound_slots)
            if resolved_range is not None:
                facts["resolved_datetime_range"] = dict(resolved_range)
            if time_match_outcome is not None:
                facts["time_match_outcome"] = time_match_outcome
            if time_resolution is not None:
                facts["time_resolution"] = time_resolution


def apply_time_match_exact_to_plan(
    plan: Dict[str, Any],
    *,
    bind_result: Dict[str, Any],
    time_resolution: Optional[Dict[str, Any]] = None,
) -> None:
    """Apply exact-match binding and move plan toward confirmation."""
    bound_slots = bind_result.get("slots")
    resolved_range = bind_result.get("resolved_datetime_range")
    if not isinstance(bound_slots, dict) or not isinstance(resolved_range, dict):
        return

    _patch_plan_container(
        plan,
        status="AWAITING_CONFIRMATION",
        stage="CONFIRM",
        action=None,
        awaiting="USER_CONFIRMATION",
        time_match_outcome=TIME_MATCH_EXACT,
        time_resolution=time_resolution,
        bound_slots=bound_slots,
        resolved_range=resolved_range,
    )

    merged = plan.get("_merged_luma_response")
    if isinstance(merged, dict):
        merged_slots = merged.get("slots")
        if not isinstance(merged_slots, dict):
            merged_slots = {}
        merged_slots.update(bound_slots)
        merged["slots"] = merged_slots
        merged["resolved_datetime_range"] = dict(resolved_range)
        merged["time_match_outcome"] = TIME_MATCH_EXACT
        if time_resolution is not None:
            merged["time_resolution"] = dict(time_resolution)
        try:
            from core.session.confirmation_gate import set_confirmation_state

            set_confirmation_state(merged, "pending")
        except ImportError:
            pass


def apply_time_match_mismatch_to_plan(
    plan: Dict[str, Any],
    *,
    time_resolution: Dict[str, Any],
    time_proposal: Optional[Dict[str, Any]] = None,
) -> None:
    """Retain requested time and require a conversational response (no execution)."""
    _patch_plan_container(
        plan,
        status="NEEDS_CLARIFICATION",
        stage="AVAILABILITY",
        action=None,
        awaiting="TIME_SELECTION",
        time_match_outcome=TIME_MATCH_MISMATCH,
        time_resolution=time_resolution,
    )
    if isinstance(time_proposal, dict):
        plan["time_proposal"] = time_proposal

    merged = plan.get("_merged_luma_response")
    if isinstance(merged, dict):
        merged["time_match_outcome"] = TIME_MATCH_MISMATCH
        merged["time_resolution"] = dict(time_resolution)
        if isinstance(time_proposal, dict):
            merged["time_proposal"] = time_proposal

    decision = plan.get("_decision")
    if isinstance(decision, dict) and isinstance(time_proposal, dict):
        decision["time_proposal"] = time_proposal
        facts = decision.get("facts")
        if isinstance(facts, dict):
            facts["time_proposal"] = time_proposal


def apply_post_bind_time_resolution(
    merged: Dict[str, Any],
    session_state: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """After merge.bind_time fails, resolve against currently presented offers."""
    time_proposal = merged.get("time_proposal")
    if not _exact_requested_time(time_proposal):
        return None
    if not isinstance(session_state, dict):
        return None
    last_result = session_state.get("last_execution_result")
    if not isinstance(last_result, dict):
        return None
    if last_result.get("type") != "availability" or last_result.get("status") != "success":
        return None

    offers = get_presented_availability_offers(session_state)
    if not offers:
        return None

    search_date = None
    presented = session_state.get("presented_availability")
    if isinstance(presented, dict) and presented.get("search_date"):
        search_date = _normalize_search_date(presented.get("search_date"))
    if not search_date:
        search_date = _normalize_search_date(last_result.get("search_date"))

    slots = merged.get("slots") if isinstance(
        merged.get("slots"), dict) else {}
    payload = resolve_time_after_availability(
        offers=offers,
        time_proposal=time_proposal,
        date_proposal=merged.get("date_proposal"),
        search_date=search_date,
        slots=slots,
    )
    resolution = payload.get("time_resolution")
    if not isinstance(resolution, dict):
        return None

    outcome = resolution.get("outcome")
    merged["time_resolution"] = resolution
    if outcome == TIME_MATCH_EXACT:
        bind_result = payload.get("bind_result")
        if not isinstance(bind_result, dict):
            return payload
        merged["slots"] = bind_result["slots"]
        merged["resolved_datetime_range"] = bind_result["resolved_datetime_range"]
        merged["time_match_outcome"] = TIME_MATCH_EXACT
        try:
            from core.session.confirmation_gate import set_confirmation_state

            set_confirmation_state(merged, "pending")
        except ImportError:
            pass
        return payload

    if outcome == TIME_MATCH_MISMATCH:
        merged["time_match_outcome"] = TIME_MATCH_MISMATCH
        return payload

    return None


def sync_execution_plan_from_time_resolution(
    plan: Dict[str, Any],
    execution_result: Dict[str, Any],
) -> None:
    """Ensure execution_result.plan reflects post-resolution planner state."""
    if not plan.get("time_match_outcome"):
        return
    if not isinstance(execution_result, dict):
        return
    plan_snapshot = execution_result.get("plan")
    if not isinstance(plan_snapshot, dict):
        plan_snapshot = {}
        execution_result["plan"] = plan_snapshot
    for key in ("status", "stage", "action", "awaiting", "time_match_outcome"):
        if key in plan:
            plan_snapshot[key] = plan.get(key)
    execution_result["time_match_outcome"] = plan.get("time_match_outcome")
    if plan.get("time_resolution"):
        execution_result["time_resolution"] = plan.get("time_resolution")


def build_execution_result_for_time_resolution_render(
    session_state: Optional[Dict[str, Any]],
    *,
    time_resolution: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Build a synthetic availability execution payload for mismatch rendering."""
    if not isinstance(session_state, dict):
        return None
    last_result = session_state.get("last_execution_result")
    if not isinstance(last_result, dict):
        return None
    if last_result.get("type") != "availability":
        return None
    resolution = time_resolution or last_result.get("time_resolution")
    if not isinstance(resolution, dict):
        return None
    if resolution.get("outcome") != TIME_MATCH_MISMATCH:
        return None
    return {
        "type": "availability",
        "status": last_result.get("status", "success"),
        "slots": list(last_result.get("slots") or []),
        "time_resolution": resolution,
    }


def _emit_time_resolution_trace(
    resolution: Dict[str, Any],
    *,
    alternatives: Optional[List[str]] = None,
) -> None:
    try:
        from core.tracing.decision_trace import emit_evidence

        facts: Dict[str, Any] = {"time_resolution": dict(resolution)}
        if alternatives is not None:
            facts["alternatives_count"] = len(alternatives)
        emit_evidence(
            "TIME_RESOLUTION",
            subsystem="execution",
            facts=facts,
            node_id="evidence.time_resolution",
            source="time_resolution.resolve_time_after_availability",
            observed_at_stage="execution",
        )
    except ImportError:
        pass
