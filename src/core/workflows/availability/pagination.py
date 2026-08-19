"""Browse cached availability via presentation advance (no SEARCH_AVAILABILITY)."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from core.workflows.availability.browse import (
    resolve_browse_intent,
)
from core.rendering.availability_renderer import (
    build_availability_browse_status_render_request,
    build_availability_render_request,
)
from core.workflows.availability.discovery.bridge import (
    browse_via_discovery,
    present_via_discovery,
)
from core.workflows.availability.presentation import (
    availability_cache_from_session,
    presentation_meta_from_presented,
    presented_availability_from_session,
    search_criteria_from_session,
)
from core.workflows.availability.selection import (
    REASON_CRITERIA_CHANGED,
    search_criteria_changed,
)
from core.planning.temporal_proposal import build_selection_user_facts
from core.rendering.llm_renderer import render_llm
from core.execution.result import normalize_execution_result

logger = logging.getLogger(__name__)

_NO_MORE_FALLBACK_TEXT = (
    "There are no more available times to show from your last search. "
    "Ask for another date."
)


def _emit_pagination_skip(
    *,
    skip_reason: str,
    session_state: Optional[Dict[str, Any]] = None,
    browse_resolve_id: Optional[str] = None,
) -> None:
    try:
        from core.tracing.browse import BROWSE_RESOLVE_ID, emit_pagination_handle_trace

        resolve_id = browse_resolve_id
        if resolve_id is None:
            from core.tracing.decision_trace import TurnTrace

            trace = TurnTrace.current()
            if trace and trace.has_record(BROWSE_RESOLVE_ID):
                resolve_id = BROWSE_RESOLVE_ID
        emit_pagination_handle_trace(
            handled=False,
            skip_reason=skip_reason,
            session_state=session_state,
            browse_resolve_id=resolve_id,
        )
    except ImportError:
        pass


def _render_pagination_text(
    *,
    decision: Optional[Dict[str, Any]],
    execution_result: Dict[str, Any],
    session_state: Optional[Dict[str, Any]],
    presented: Optional[Dict[str, Any]] = None,
    browse_status: Optional[str] = None,
    direction: Optional[str] = None,
    no_more: bool = False,
) -> Optional[str]:
    from core.rendering.response_renderer import _structured_context_from_decision

    conversation_history = (session_state or {}).get("messages", [])
    structured_context: Dict[str, Any] = {}
    if isinstance(decision, dict):
        facts = decision.get("facts")
        if isinstance(facts, dict) and isinstance(facts.get("context"), dict):
            structured_context = facts["context"]

    if no_more or browse_status:
        hints = {}
        search_date = None
        if isinstance(presented, dict):
            if isinstance(presented.get("browse_hints"), dict):
                hints = presented["browse_hints"]
            search_date = presented.get("search_date")
        request = build_availability_browse_status_render_request(
            decision,
            direction=direction or "next",
            browse_status=browse_status or "exhausted",
            browse_hints=hints,
            search_date=search_date if isinstance(search_date, str) else None,
            structured_context=structured_context,
            conversation_history=conversation_history,
        )
        return render_llm(request)

    if presented is None:
        return None
    render_request = build_availability_render_request(
        decision,
        execution_result,
        structured_context=_structured_context_from_decision(decision or {}),
        conversation_history=conversation_history,
        presented=presented,
    )
    if not render_request:
        return None
    return render_llm(render_request)


def _criteria_changed_for_turn(
    merged: Dict[str, Any],
    session_state: Optional[Dict[str, Any]],
) -> bool:
    return search_criteria_changed(
        user_facts=build_selection_user_facts(merged),
        session_state=session_state,
    )


def _build_presentation_response(
    *,
    plan: Dict[str, Any],
    merged: Dict[str, Any],
    session: Dict[str, Any],
    organization_id: int,
    presented_payload: Dict[str, Any],
    presentation_payload: Dict[str, Any],
    moved: bool,
    reason_code: str,
    direction: Optional[str],
    axis_hint: Optional[str],
    session_state: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    from core.engine.outcome_builder import build_outcome_from_decision

    decision = plan.get("_decision")
    outcome_base = (
        build_outcome_from_decision(decision) if isinstance(decision, dict) else {}
    )
    search_date = presented_payload.get("search_date")

    if not moved:
        rendered_text = _render_pagination_text(
            decision=decision if isinstance(decision, dict) else None,
            execution_result={"type": "availability", "status": "success", "slots": []},
            session_state=session_state,
            presented=presented_payload,
            browse_status=reason_code,
            direction=direction or "next",
            no_more=True,
        ) or _NO_MORE_FALLBACK_TEXT
        outcome = dict(outcome_base)
        outcome["availability_pagination"] = {
            "direction": direction,
            "axis_hint": axis_hint,
            "exhausted": True,
            "browse_status": reason_code,
            "page_index": presentation_payload.get("page_index"),
        }
        outcome["text"] = rendered_text
        plan_obj = outcome.get("plan")
        if isinstance(plan_obj, dict):
            plan_obj = dict(plan_obj)
            plan_obj["action"] = None
            outcome["plan"] = plan_obj
            outcome["action"] = None
        response = {
            "success": True,
            "result": outcome,
            "outcome": outcome,
            "plan": plan,
            "_merged_luma_response": merged,
            "availability_pagination": outcome["availability_pagination"],
            "_working_session": session,
            "_workflow_result": {
                "kind": "availability_pagination",
                # Preserve last successful window even when browse did not move.
                "presented_availability": presented_payload,
                "availability_presentation": presentation_payload,
                "page_index": presentation_payload.get("page_index"),
                "page_size": presentation_payload.get("page_size"),
                "search_date": search_date,
                "exhausted": True,
                "browse_status": reason_code,
            },
            "text": rendered_text,
        }
        response.setdefault("ui_actions", [])
        return response

    synthetic_plan = dict(plan)
    synthetic_slots = dict(plan.get("slots") or {})
    synthetic_slots["organization_id"] = organization_id
    synthetic_plan["slots"] = synthetic_slots
    synthetic_plan["action"] = "SEARCH_AVAILABILITY"
    synthetic_execution = normalize_execution_result(
        synthetic_plan,
        {
            "type": "availability",
            "status": "success",
            "slots": presented_payload.get("slots") or [],
            "search_date": presented_payload.get("search_date"),
        },
    )
    rendered_text = _render_pagination_text(
        decision=decision if isinstance(decision, dict) else None,
        execution_result=synthetic_execution,
        session_state=session_state,
        presented=presented_payload,
    )
    outcome = dict(outcome_base)
    outcome["type"] = "availability"
    outcome["status"] = "success"
    outcome["slots"] = presented_payload.get("slots") or []
    outcome["availability_pagination"] = {
        "direction": direction,
        "axis_hint": axis_hint,
        "exhausted": False,
        "browse_status": reason_code,
        "page_index": presentation_payload.get("page_index"),
    }
    if rendered_text:
        outcome["text"] = rendered_text
    plan_obj = outcome.get("plan")
    if isinstance(plan_obj, dict):
        plan_obj = dict(plan_obj)
        plan_obj["action"] = None
        outcome["plan"] = plan_obj
        outcome["action"] = None
    response = {
        "success": True,
        "result": outcome,
        "outcome": outcome,
        "plan": plan,
        "_merged_luma_response": merged,
        "availability_pagination": outcome["availability_pagination"],
        "_working_session": session,
        "_workflow_result": {
            "kind": "availability_pagination",
            "presented_availability": presented_payload,
            "availability_presentation": presentation_payload,
            "page_index": presentation_payload.get("page_index"),
            "page_size": presentation_payload.get("page_size"),
            "search_date": search_date,
            "exhausted": False,
            "browse_status": reason_code,
        },
    }
    if rendered_text:
        response["text"] = rendered_text
    response.setdefault("ui_actions", [])
    return response


def try_handle_availability_browse_turn(
    *,
    plan: Dict[str, Any],
    session_state: Optional[Dict[str, Any]],
    session_store: Optional[Any],
    organization_id: int,
    user_id: str,
) -> Optional[Dict[str, Any]]:
    """Advance presented availability for browse_next / browse_previous turns.

    Returns a full handle_message response when handled, otherwise None.
    Never calls SEARCH_AVAILABILITY or mutates booking slots/proposals.
    Absolute date requests are not handled here — they require SEARCH.
    """
    _ = session_store  # Compatibility-only; browse persistence is turn-end only.

    merged = plan.get("_merged_luma_response") if isinstance(plan, dict) else None
    intent_obj = merged.get("intent") if isinstance(merged, dict) else None
    intent_name = (
        intent_obj.get("name") if isinstance(intent_obj, dict) else None
    )
    browse_intent = (
        resolve_browse_intent(merged, session_state)
        if isinstance(merged, dict)
        else None
    )
    cache = availability_cache_from_session(session_state)
    logger.debug(
        "[AVAILABILITY_PAGINATION] ENTRY user_id=%s called=true "
        "plan_keys=%s merged_is_dict=%s merged_intent=%s "
        "browse_intent=%s session_has_cache=%s",
        user_id,
        list(plan.keys()) if isinstance(plan, dict) else None,
        isinstance(merged, dict),
        intent_name,
        browse_intent,
        cache is not None,
    )

    if not isinstance(merged, dict):
        _emit_pagination_skip(
            skip_reason="no_merged_luma_response",
            session_state=session_state,
        )
        return None

    logger.debug(
        "[AVAILABILITY_PAGINATION] probe user_id=%s merged_keys=%s intent=%s "
        "operation=%s resolved_browse=%s",
        user_id,
        list(merged.keys()),
        intent_name,
        merged.get("operation"),
        browse_intent,
    )

    if not browse_intent:
        _emit_pagination_skip(
            skip_reason="browse_not_detected",
            session_state=session_state,
        )
        return None

    # Planner already decided a new search is required (e.g. date/service
    # criteria changed). Do not paginate from the stale cache —
    # that would swallow SEARCH_AVAILABILITY.
    if plan.get("action") == "SEARCH_AVAILABILITY":
        _emit_pagination_skip(
            skip_reason="plan_requires_search",
            session_state=session_state,
        )
        return None

    if cache is None or not (cache.get("slots") or []):
        _emit_pagination_skip(
            skip_reason="no_cached_availability",
            session_state=session_state,
        )
        return None

    # Criteria changes are planner concerns — do not browse or search here.
    if _criteria_changed_for_turn(merged, session_state):
        merged["_selection_resolution"] = {
            "status": "criteria_changed",
            "source": None,
            "reason_code": REASON_CRITERIA_CHANGED,
        }
        _emit_pagination_skip(
            skip_reason="search_criteria_changed",
            session_state=session_state,
        )
        return None

    session = dict(session_state or {})
    criteria_slots, criteria_date_proposal = search_criteria_from_session(session_state)
    current = presented_availability_from_session(session_state)
    if current is None or not (current.get("slots") or []):
        current = present_via_discovery(
            cache,
            slots=criteria_slots,
            date_proposal=criteria_date_proposal,
        )

    direction = browse_intent.get("direction")
    if direction not in ("next", "previous"):
        _emit_pagination_skip(
            skip_reason="invalid_browse_direction",
            session_state=session_state,
        )
        return None
    try:
        from core.tracing.browse import (
            BROWSE_RESOLVE_ID,
            emit_pagination_handle_trace,
        )
        from core.tracing.decision_trace import TurnTrace

        browse_resolve_id = None
        trace = TurnTrace.current()
        if trace and trace.has_record(BROWSE_RESOLVE_ID):
            browse_resolve_id = BROWSE_RESOLVE_ID
        emit_pagination_handle_trace(
            handled=True,
            direction=direction,
            session_state=session_state,
            browse_resolve_id=browse_resolve_id,
        )
    except ImportError:
        pass

    projection = browse_via_discovery(
        cache,
        current,
        browse_intent,
        slots=criteria_slots,
        date_proposal=criteria_date_proposal,
    )
    presented_payload = projection.get("presented") or current
    moved = bool(projection.get("moved"))
    reason_code = projection.get("reason_code") or (
        "moved" if moved else "exhausted"
    )
    return _build_presentation_response(
        plan=plan,
        merged=merged,
        session=session,
        organization_id=organization_id,
        presented_payload=presented_payload,
        presentation_payload=presentation_meta_from_presented(presented_payload),
        moved=moved,
        reason_code=reason_code,
        direction=direction,
        axis_hint=browse_intent.get("axis_hint") or "any",
        session_state=session_state,
    )
