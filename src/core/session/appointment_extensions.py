"""CREATE_APPOINTMENT-specific persistence for planning constraints."""

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def apply_create_appointment_extensions(
    session_state: Dict[str, Any],
    final_intent_name: Optional[str],
    outcome: Dict[str, Any],
    merged_luma_response: Optional[Dict[str, Any]],
    previous_session_state: Optional[Dict[str, Any]],
) -> None:
    """Apply CREATE_APPOINTMENT-specific fields to session_state (mutates in place)."""
    if final_intent_name != "CREATE_APPOINTMENT":
        return

    from core.planning.temporal_contract import get_temporal

    if merged_luma_response and isinstance(merged_luma_response, dict):
        temporal = get_temporal(merged_luma_response)
        session_state["temporal"] = temporal
        session_state.pop("time_constraint", None)
        logger.debug(
            "[TEMPORAL] Persisting temporal mode=%s start_time=%s",
            temporal.get("mode"),
            temporal.get("start_time"),
        )

    plan_obj = outcome.get("plan", {})
    if isinstance(plan_obj, dict) and plan_obj.get("_availability_planned"):
        session_state["availability_planned"] = True
        logger.debug(
            "[AVAILABILITY_PLANNED] Persisting availability_planned=true to session_state")
    elif previous_session_state and previous_session_state.get("availability_planned"):
        session_state["availability_planned"] = True
        logger.debug(
            "[AVAILABILITY_PLANNED] Preserving availability_planned=true from previous session"
        )

    if merged_luma_response and isinstance(merged_luma_response, dict):
        from core.session.invalidation import REVISION_INVALIDATED_PRIOR_TIME_KEY

        bound_range = merged_luma_response.get("resolved_datetime_range")
        if isinstance(bound_range, dict) and bound_range.get("start"):
            session_state["resolved_datetime_range"] = bound_range
            logger.debug(
                "[DATETIME_RANGE] Persisting resolved_datetime_range from planning merge"
            )
        elif (
            merged_luma_response.get("_bound_datetime_cleared")
            or merged_luma_response.get(REVISION_INVALIDATED_PRIOR_TIME_KEY)
            or merged_luma_response.get("_booking_confirmation_rejected")
        ):
            session_state.pop("resolved_datetime_range", None)
            facts = session_state.get("facts")
            if isinstance(facts, dict):
                facts.pop("resolved_datetime_range", None)
        elif isinstance(previous_session_state, dict):
            previous_bound = previous_session_state.get("resolved_datetime_range")
            if not isinstance(previous_bound, dict):
                previous_planning = previous_session_state.get("planning")
                if isinstance(previous_planning, dict):
                    previous_bound = previous_planning.get("bound_datetime")
            if isinstance(previous_bound, dict) and previous_bound.get("start"):
                session_state["resolved_datetime_range"] = dict(previous_bound)
