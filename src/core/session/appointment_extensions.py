"""CREATE_APPOINTMENT-specific session extensions (availability, constraints)."""

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _extract_availability_execution_result(
    outcome: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Return a successful availability execution payload from an outcome wrapper."""
    if not isinstance(outcome, dict):
        return None
    candidates = [outcome]
    nested = outcome.get("result")
    if isinstance(nested, dict):
        candidates.insert(0, nested)
    for candidate in candidates:
        if (
            candidate.get("type") == "availability"
            and candidate.get("status") == "success"
        ):
            return candidate
    return None


def resolve_availability_fingerprint(
    outcome: Optional[Dict[str, Any]],
    previous_session_state: Optional[Dict[str, Any]],
    session_store: Optional[Any],
    user_id: Optional[str],
) -> Optional[str]:
    """Resolve availability_fingerprint from outcome, session, or store."""
    fingerprint = None

    if outcome and isinstance(outcome, dict):
        exec_result = _extract_availability_execution_result(outcome)
        if exec_result and exec_result.get("availability_fingerprint"):
            fingerprint = exec_result.get("availability_fingerprint")
            logger.debug(
                "[AVAILABILITY_FINGERPRINT] Extracted from availability execution: %s",
                fingerprint,
            )
        if not fingerprint and outcome.get("availability_fingerprint"):
            fingerprint = outcome.get("availability_fingerprint")
            logger.debug(
                "[AVAILABILITY_FINGERPRINT] Extracted from outcome top-level: %s",
                fingerprint,
            )
        if not fingerprint:
            plan_obj = outcome.get("plan")
            if isinstance(plan_obj, dict) and plan_obj.get("availability_fingerprint"):
                fingerprint = plan_obj.get("availability_fingerprint")
                logger.debug(
                    "[AVAILABILITY_FINGERPRINT] Extracted from outcome.plan: %s",
                    fingerprint,
                )

    if not fingerprint and previous_session_state:
        fingerprint = previous_session_state.get("availability_fingerprint")
        if fingerprint:
            logger.debug(
                "[AVAILABILITY_FINGERPRINT] Extracted from previous_session_state: %s",
                fingerprint,
            )

    if not fingerprint and session_store and user_id:
        try:
            if hasattr(session_store, "get_session"):
                latest_session = session_store.get_session(user_id)
            elif callable(session_store):
                latest_session = session_store(user_id)
            else:
                latest_session = None
            if latest_session and isinstance(latest_session, dict):
                fingerprint = latest_session.get("availability_fingerprint")
                if fingerprint:
                    logger.debug(
                        "[AVAILABILITY_FINGERPRINT] Extracted from session_store: %s",
                        fingerprint,
                    )
        except Exception as exc:
            logger.debug(
                "Failed to read session from session_store for fingerprint preservation: %s",
                exc,
            )

    if not fingerprint and user_id:
        try:
            from core.orchestration.session import get_session

            latest_session = get_session(user_id)
            if latest_session and isinstance(latest_session, dict):
                fingerprint = latest_session.get("availability_fingerprint")
                if fingerprint:
                    logger.debug(
                        "[AVAILABILITY_FINGERPRINT] Extracted from get_session: %s",
                        fingerprint,
                    )
        except Exception as exc:
            logger.debug(
                "Failed to read session using get_session for fingerprint preservation: %s",
                exc,
            )

    return fingerprint


def _read_session_field_from_store(
    session_store: Optional[Any], user_id: Optional[str], field: str
) -> Optional[Any]:
    """Read a session field written during this request (e.g. by orchestrator)."""
    if not session_store or not user_id:
        return None
    try:
        if hasattr(session_store, "get_session"):
            latest_session = session_store.get_session(user_id)
        elif callable(session_store):
            latest_session = session_store(user_id)
        else:
            latest_session = None
        if isinstance(latest_session, dict):
            value = latest_session.get(field)
            if value is not None:
                return value
    except Exception as exc:
        logger.debug(
            "Failed to read %s from session_store: %s",
            field,
            exc,
        )
    return None


def _read_last_execution_result_from_store(
    session_store: Optional[Any], user_id: Optional[str]
) -> Optional[Dict[str, Any]]:
    """Read the freshest last_execution_result written during this request."""
    last_result = _read_session_field_from_store(
        session_store, user_id, "last_execution_result"
    )
    return last_result if isinstance(last_result, dict) else None


def _read_presented_availability_from_store(
    session_store: Optional[Any], user_id: Optional[str]
) -> Optional[Dict[str, Any]]:
    """Read the freshest presented_availability written during this request."""
    presented = _read_session_field_from_store(
        session_store, user_id, "presented_availability"
    )
    return presented if isinstance(presented, dict) else None


def apply_create_appointment_extensions(
    session_state: Dict[str, Any],
    final_intent_name: Optional[str],
    outcome: Dict[str, Any],
    merged_luma_response: Optional[Dict[str, Any]],
    previous_session_state: Optional[Dict[str, Any]],
    session_store: Optional[Any],
    user_id: Optional[str],
) -> None:
    """Apply CREATE_APPOINTMENT-specific fields to session_state (mutates in place)."""
    if final_intent_name != "CREATE_APPOINTMENT":
        return

    if merged_luma_response and isinstance(merged_luma_response, dict):
        time_constraint = merged_luma_response.get("time_constraint")
        if time_constraint is not None:
            session_state["time_constraint"] = time_constraint
            logger.debug(
                "[TIME_CONSTRAINT] Persisting time_constraint to session_state: %s",
                time_constraint,
            )

    plan_obj = outcome.get("plan", {})
    if isinstance(plan_obj, dict) and plan_obj.get("_availability_planned"):
        session_state["availability_planned"] = True
        logger.debug("[AVAILABILITY_PLANNED] Persisting availability_planned=true to session_state")
    elif previous_session_state and previous_session_state.get("availability_planned"):
        session_state["availability_planned"] = True
        logger.debug(
            "[AVAILABILITY_PLANNED] Preserving availability_planned=true from previous session"
        )

    exec_result = _extract_availability_execution_result(outcome)
    if exec_result:
        from core.orchestration.temporal_proposal import enrich_last_execution_result
        from core.rendering.availability_renderer import build_presented_availability

        search_date = None
        plan_obj = outcome.get("plan")
        if isinstance(plan_obj, dict):
            plan_slots = plan_obj.get("slots") or {}
            plan_date = plan_slots.get("date")
            if isinstance(plan_date, str) and plan_date:
                search_date = plan_date.split("T")[0].split(" ")[0]
        session_state["last_execution_result"] = enrich_last_execution_result(
            exec_result, search_date=search_date
        )
        session_state["presented_availability"] = build_presented_availability(
            exec_result.get("slots") or [],
            search_date=session_state["last_execution_result"].get("search_date")
            or search_date,
        )
        logger.debug(
            "[AVAILABILITY_EXECUTED] Persisted last_execution_result and "
            "presented_availability from current search"
        )
    else:
        fresh_last_result = _read_last_execution_result_from_store(session_store, user_id)
        if fresh_last_result:
            session_state["last_execution_result"] = fresh_last_result
            logger.debug(
                "[AVAILABILITY_EXECUTED] Preserved last_execution_result from session_store"
            )
        elif previous_session_state and previous_session_state.get("last_execution_result"):
            session_state["last_execution_result"] = previous_session_state.get(
                "last_execution_result"
            )
            logger.debug(
                "[AVAILABILITY_EXECUTED] Preserving last_execution_result from previous session"
            )

        fresh_presented = _read_presented_availability_from_store(session_store, user_id)
        if fresh_presented:
            session_state["presented_availability"] = fresh_presented
            logger.debug(
                "[AVAILABILITY_EXECUTED] Preserved presented_availability from session_store"
            )
        elif previous_session_state and previous_session_state.get(
            "presented_availability"
        ):
            session_state["presented_availability"] = previous_session_state.get(
                "presented_availability"
            )
            logger.debug(
                "[AVAILABILITY_EXECUTED] Preserving presented_availability from previous session"
            )

    if merged_luma_response and isinstance(merged_luma_response, dict):
        bound_range = merged_luma_response.get("resolved_datetime_range")
        if isinstance(bound_range, dict) and bound_range.get("start"):
            session_state["resolved_datetime_range"] = bound_range
            logger.debug(
                "[DATETIME_RANGE] Persisting resolved_datetime_range from selection bind"
            )
    elif previous_session_state and previous_session_state.get("resolved_datetime_range"):
        session_state["resolved_datetime_range"] = previous_session_state.get(
            "resolved_datetime_range"
        )

    fingerprint = resolve_availability_fingerprint(
        outcome, previous_session_state, session_store, user_id
    )
    if fingerprint:
        session_state["availability_fingerprint"] = fingerprint
        logger.debug(
            "[AVAILABILITY_FINGERPRINT] Preserved in session_state: %s",
            fingerprint,
        )

    _maybe_persist_booking_confirmation_pending(
        session_state, merged_luma_response, outcome
    )

    from core.session.confirmation_gate import normalize_confirmation_state

    normalize_confirmation_state(session_state)


def _maybe_persist_booking_confirmation_pending(
    session_state: Dict[str, Any],
    merged_luma_response: Optional[Dict[str, Any]],
    outcome: Dict[str, Any],
) -> None:
    """Mark session as awaiting booking confirmation when commit prerequisites are met."""
    if session_state.get("intent_name") != "CREATE_APPOINTMENT":
        return

    # User declined the confirmation prompt — leave commit gate, keep booking context.
    if isinstance(merged_luma_response, dict) and merged_luma_response.get(
        "_booking_confirmation_rejected"
    ):
        from core.session.confirmation_gate import clear_pending_confirmation

        clear_pending_confirmation(
            session_state, clear_time=True, reason="reject_persist"
        )
        return

    from core.session.confirmation_gate import (
        get_confirmation_state,
        normalize_confirmation_state,
        set_confirmation_state,
    )

    current = get_confirmation_state(session_state)
    if current in ("pending", "confirmed"):
        normalize_confirmation_state(session_state)
        return

    booking = {}
    if isinstance(merged_luma_response, dict) and isinstance(
        merged_luma_response.get("booking"), dict
    ):
        booking = merged_luma_response["booking"]
    elif isinstance(outcome.get("booking"), dict):
        booking = outcome["booking"]
    incoming = booking.get("confirmation_state")
    if incoming in ("pending", "confirmed"):
        set_confirmation_state(session_state, incoming)
        return

    slots = session_state.get("slots", {})
    if not all(slots.get(key) for key in ("service_id", "date", "time")):
        return

    from core.orchestration.temporal_proposal import has_bound_booking_datetime

    if not has_bound_booking_datetime(slots, session_state):
        return

    set_confirmation_state(session_state, "pending")
    logger.debug(
        "[BOOKING_CONFIRMATION] Persisted confirmation_state=pending on session"
    )
