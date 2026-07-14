"""Paginate presented availability over cached SEARCH_AVAILABILITY results."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from core.workflows.availability.browse import (
    extract_availability_browse,
    resolve_availability_browse,
)
from core.rendering.availability_renderer import (
    _DEFAULT_MAX_TIMES,
    build_availability_no_more_render_request,
    build_availability_presentation,
    build_availability_render_request,
    build_presented_availability_page,
    compute_target_page_index,
    dedupe_availability_slots,
)
from core.rendering.llm_renderer import render_llm

logger = logging.getLogger(__name__)

_NO_MORE_FALLBACK_TEXT = (
    "There are no more available times to show from your last search."
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


def _resolve_search_date(
    session_state: Dict[str, Any],
    last_result: Dict[str, Any],
) -> Optional[str]:
    presented = session_state.get("presented_availability")
    if isinstance(presented, dict) and presented.get("search_date"):
        return str(presented["search_date"]).split("T")[0].split(" ")[0]
    search_date = last_result.get("search_date")
    if isinstance(search_date, str) and search_date:
        return search_date.split("T")[0].split(" ")[0]
    return None


def _render_pagination_text(
    *,
    decision: Optional[Dict[str, Any]],
    execution_result: Dict[str, Any],
    session_state: Optional[Dict[str, Any]],
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

    if no_more and direction:
        request = build_availability_no_more_render_request(
            decision,
            direction=direction,
            structured_context=structured_context,
            conversation_history=conversation_history,
        )
        return render_llm(request)

    render_request = build_availability_render_request(
        decision,
        execution_result,
        structured_context=_structured_context_from_decision(decision or {}),
        conversation_history=conversation_history,
    )
    if not render_request:
        return None
    return render_llm(render_request)


def try_handle_availability_browse_turn(
    *,
    plan: Dict[str, Any],
    session_state: Optional[Dict[str, Any]],
    session_store: Optional[Any],
    user_id: str,
) -> Optional[Dict[str, Any]]:
    """Paginate cached availability when a browse signal is present.

    Returns a full handle_message response when handled, otherwise None.
    Never calls SEARCH_AVAILABILITY or mutates booking slots/proposals.
    """
    from core.session.session_ops import _persist_to_session
    from core.engine.outcome_builder import build_outcome_from_decision

    merged = plan.get("_merged_luma_response") if isinstance(plan, dict) else None
    intent_obj = merged.get("intent") if isinstance(merged, dict) else None
    intent_name = (
        intent_obj.get("name") if isinstance(intent_obj, dict) else None
    )
    resolved_browse = (
        resolve_availability_browse(merged, session_state)
        if isinstance(merged, dict)
        else None
    )
    logger.debug(
        "[AVAILABILITY_PAGINATION] ENTRY user_id=%s called=true "
        "plan_keys=%s merged_is_dict=%s merged_intent=%s "
        "resolve_availability_browse=%s session_has_last_execution=%s "
        "session_page_index=%s",
        user_id,
        list(plan.keys()) if isinstance(plan, dict) else None,
        isinstance(merged, dict),
        intent_name,
        resolved_browse,
        isinstance((session_state or {}).get("last_execution_result"), dict)
        if session_state
        else False,
        ((session_state or {}).get("availability_presentation") or {}).get("page_index")
        if session_state
        else None,
    )

    if not isinstance(merged, dict):
        logger.debug(
            "[AVAILABILITY_PAGINATION] EARLY_RETURN user_id=%s reason=no_merged_luma_response "
            "plan_keys=%s",
            user_id,
            list(plan.keys()) if isinstance(plan, dict) else None,
        )
        _emit_pagination_skip(
            skip_reason="no_merged_luma_response",
            session_state=session_state,
        )
        return None

    facts_obj = merged.get("facts")
    facts_operation = (
        facts_obj.get("operation")
        if isinstance(facts_obj, dict)
        else None
    )
    explicit_browse = extract_availability_browse(merged)
    browse = resolved_browse
    logger.debug(
        "[AVAILABILITY_PAGINATION] probe user_id=%s merged_keys=%s intent=%s "
        "availability_browse=%s operation=%s facts.operation=%s "
        "extract_browse=%s resolved_browse=%s",
        user_id,
        list(merged.keys()),
        intent_name,
        merged.get("availability_browse"),
        merged.get("operation"),
        facts_operation,
        explicit_browse,
        browse,
    )
    if not browse:
        logger.debug(
            "[AVAILABILITY_PAGINATION] EARLY_RETURN user_id=%s reason=browse_not_detected "
            "merged_intent=%s extract_browse=%s operation=%s facts.operation=%s",
            user_id,
            intent_name,
            explicit_browse,
            merged.get("operation"),
            facts_operation,
        )
        _emit_pagination_skip(
            skip_reason="browse_not_detected",
            session_state=session_state,
        )
        return None

    direction = browse.get("direction")
    if direction not in ("next", "previous"):
        logger.debug(
            "[AVAILABILITY_PAGINATION] EARLY_RETURN user_id=%s reason=invalid_browse_direction "
            "direction=%r browse=%s",
            user_id,
            direction,
            browse,
        )
        _emit_pagination_skip(
            skip_reason="invalid_browse_direction",
            session_state=session_state,
        )
        return None

    session = dict(session_state or {})
    last_result = session.get("last_execution_result")
    if (
        not isinstance(last_result, dict)
        or last_result.get("type") != "availability"
        or last_result.get("status") != "success"
    ):
        logger.debug(
            "[AVAILABILITY_PAGINATION] EARLY_RETURN user_id=%s reason=no_cached_availability "
            "last_result_type=%s last_result_status=%s",
            user_id,
            last_result.get("type") if isinstance(last_result, dict) else None,
            last_result.get("status") if isinstance(last_result, dict) else None,
        )
        _emit_pagination_skip(
            skip_reason="no_cached_availability",
            session_state=session_state,
        )
        return None

    cached_slots = last_result.get("slots") or []
    unique_slots, _, _ = dedupe_availability_slots(cached_slots)
    if not unique_slots:
        logger.debug(
            "[AVAILABILITY_PAGINATION] EARLY_RETURN user_id=%s reason=empty_cached_slots",
            user_id,
        )
        _emit_pagination_skip(
            skip_reason="empty_cached_slots",
            session_state=session_state,
        )
        return None

    presentation = session.get("availability_presentation")
    if not isinstance(presentation, dict):
        presentation = {}
    page_size = int(presentation.get("page_size") or _DEFAULT_MAX_TIMES)
    current_index = int(presentation.get("page_index") or 0)
    search_date = _resolve_search_date(session, last_result)

    target_index, exhausted = compute_target_page_index(
        current_index,
        direction,
        len(unique_slots),
        page_size,
    )

    try:
        from core.tracing.browse import (
            BROWSE_RESOLVE_ID,
            emit_pagination_handle_trace,
            emit_pagination_page_target_trace,
        )
        from core.tracing.decision_trace import TurnTrace

        browse_resolve_id = None
        trace = TurnTrace.current()
        if trace and trace.has_record(BROWSE_RESOLVE_ID):
            browse_resolve_id = BROWSE_RESOLVE_ID
        pagination_handle_id = emit_pagination_handle_trace(
            handled=True,
            direction=direction,
            session_state=session_state,
            browse_resolve_id=browse_resolve_id,
        )
        emit_pagination_page_target_trace(
            current_index=current_index,
            target_index=target_index,
            direction=direction,
            exhausted=exhausted,
            page_size=page_size,
            total_slots=len(unique_slots),
            pagination_handle_id=pagination_handle_id,
        )
    except ImportError:
        pass

    decision = plan.get("_decision")
    outcome_base = (
        build_outcome_from_decision(decision) if isinstance(decision, dict) else {}
    )

    if exhausted:
        logger.debug(
            "[AVAILABILITY_PAGINATION] HANDLED user_id=%s branch=exhausted "
            "direction=%s page_index=%s target_index=%s unique_slots=%s page_size=%s",
            user_id,
            direction,
            current_index,
            target_index,
            len(unique_slots),
            page_size,
        )
        logger.debug(
            "[AVAILABILITY_PAGINATION] no more pages direction=%s page_index=%s",
            direction,
            current_index,
        )
        rendered_text = _render_pagination_text(
            decision=decision if isinstance(decision, dict) else None,
            execution_result={"type": "availability", "status": "success", "slots": []},
            session_state=session_state,
            direction=direction,
            no_more=True,
        ) or _NO_MORE_FALLBACK_TEXT
        outcome = dict(outcome_base)
        outcome["availability_pagination"] = {
            "direction": direction,
            "exhausted": True,
            "page_index": current_index,
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
            "text": rendered_text,
        }
        response.setdefault("ui_actions", [])
        return response

    presented_payload = build_presented_availability_page(
        cached_slots,
        page_index=target_index,
        page_size=page_size,
        search_date=search_date,
    )
    presentation_payload = build_availability_presentation(
        cached_slots,
        page_size=page_size,
        page_index=target_index,
    )

    session = _persist_to_session(
        session_store,
        user_id,
        session,
        "presented_availability",
        presented_payload,
    )
    session = _persist_to_session(
        session_store,
        user_id,
        session,
        "availability_presentation",
        presentation_payload,
    )

    synthetic_execution = {
        "type": "availability",
        "status": "success",
        "slots": presented_payload.get("slots") or [],
        "search_date": presented_payload.get("search_date"),
    }

    rendered_text = _render_pagination_text(
        decision=decision if isinstance(decision, dict) else None,
        execution_result=synthetic_execution,
        session_state=session_state,
    )

    logger.debug(
        "[AVAILABILITY_PAGINATION] page_index=%s direction=%s slots=%s",
        target_index,
        direction,
        len(presented_payload.get("slots") or []),
    )

    outcome = dict(outcome_base)
    outcome["type"] = "availability"
    outcome["status"] = "success"
    outcome["slots"] = presented_payload.get("slots") or []
    outcome["availability_pagination"] = {
        "direction": direction,
        "exhausted": False,
        "page_index": target_index,
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
    }
    if rendered_text:
        response["text"] = rendered_text
    response.setdefault("ui_actions", [])
    return response
