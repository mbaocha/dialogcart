"""CREATE_APPOINTMENT-specific session extensions (availability, constraints)."""

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def resolve_availability_fingerprint(
    outcome: Optional[Dict[str, Any]],
    previous_session_state: Optional[Dict[str, Any]],
    session_store: Optional[Any],
    user_id: Optional[str],
) -> Optional[str]:
    """Resolve availability_fingerprint from outcome, session, or store."""
    fingerprint = None

    if outcome and isinstance(outcome, dict):
        result_obj = outcome.get("result")
        if (
            isinstance(result_obj, dict)
            and result_obj.get("type") == "availability"
            and result_obj.get("status") == "success"
            and result_obj.get("availability_fingerprint")
        ):
            fingerprint = result_obj.get("availability_fingerprint")
            logger.debug(
                "[AVAILABILITY_FINGERPRINT] Extracted from outcome.result: %s",
                fingerprint,
            )
        elif outcome.get("plan") and isinstance(outcome.get("plan"), dict):
            plan_obj = outcome.get("plan")
            if plan_obj.get("availability_fingerprint"):
                fingerprint = plan_obj.get("availability_fingerprint")
                logger.debug(
                    "[AVAILABILITY_FINGERPRINT] Extracted from outcome.plan: %s",
                    fingerprint,
                )
        elif (
            outcome.get("type") == "availability"
            and outcome.get("status") == "success"
            and outcome.get("availability_fingerprint")
        ):
            fingerprint = outcome.get("availability_fingerprint")
            logger.debug(
                "[AVAILABILITY_FINGERPRINT] Extracted from outcome (direct): %s",
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

    if previous_session_state and previous_session_state.get("last_execution_result"):
        session_state["last_execution_result"] = previous_session_state.get(
            "last_execution_result"
        )
        logger.debug(
            "[AVAILABILITY_EXECUTED] Preserving last_execution_result from previous session"
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
