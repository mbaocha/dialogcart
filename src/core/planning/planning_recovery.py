"""Planning recovery when NLU invocation fails or returns unusable output.

Preserves session lifecycle rules used by plan_turn for UpstreamError,
empty/invalid Luma responses, and contract violations.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from core.session.durable_intents import is_durable_intent

logger = logging.getLogger(__name__)


def _session_intent_str(session_state: Dict[str, Any]) -> str:
    return session_state.get("intent_name") or (
        session_state.get("intent")
        if isinstance(session_state.get("intent"), str)
        else (
            session_state.get("intent", {}).get("name", "")
            if isinstance(session_state.get("intent"), dict)
            else ""
        )
    )


def _durable_session_replay_outcome(
    session_state: Dict[str, Any], session_intent_str: str
) -> Dict[str, Any]:
    session_slots = session_state.get("slots", {})
    if not isinstance(session_slots, dict):
        session_slots = {}
    session_missing_slots = session_state.get("missing_slots", [])
    if not isinstance(session_missing_slots, list):
        session_missing_slots = []
    session_stage = session_state.get("stage", "AVAILABILITY")
    session_action = session_state.get("action")
    # Derive action from stage if missing (for empty response recovery)
    if not session_action and session_stage == "AVAILABILITY":
        session_action = "SEARCH_AVAILABILITY"
    elif not session_action and session_stage == "CONFIRM":
        session_action = "CONFIRM_APPOINTMENT"
    session_status = session_state.get("status", "NEEDS_CLARIFICATION")
    # CRITICAL: Status must be NEEDS_CLARIFICATION if there are missing slots
    # Do NOT use session_status if it's READY but there are missing slots
    final_status = (
        "NEEDS_CLARIFICATION" if session_missing_slots else session_status
    )

    logger.error(
        f"[LUMA_ERROR_FALLBACK] Final status={final_status} (session_status={session_status}, "
        f"missing_slots={session_missing_slots})"
    )

    outcome = {
        "intent_name": session_intent_str,
        "stage": session_stage,
        "action": session_action,
        "slots": session_slots,
        "missing_slots": session_missing_slots,
        "status": final_status,
        "plan": {
            "intent": session_intent_str,
            "stage": session_stage,
            "action": session_action,
            "missing_slots": session_missing_slots,
            "slots": session_slots,
            "status": final_status,
            "executable_actions": ([session_action] if session_action else []),
        },
        "facts": {
            "slots": session_slots,
            "missing_slots": session_missing_slots,
        },
    }
    return {"success": True, "outcome": outcome}


def _needs_clarification_session_outcome(
    session_state: Dict[str, Any], session_intent_str: str
) -> Dict[str, Any]:
    from core.planning.planner.missing_slots import compute_missing_slots

    session_slots = session_state.get("slots", {})
    if not isinstance(session_slots, dict):
        session_slots = {}

    missing_slots = (
        compute_missing_slots(session_intent_str, session_slots)
        if session_intent_str
        else []
    )

    session_stage = session_state.get("stage", "AVAILABILITY")
    session_action = session_state.get("action")

    return {
        "success": True,
        "outcome": {
            "intent_name": session_intent_str,
            "stage": session_stage,
            "action": session_action,
            "slots": session_slots,
            "missing_slots": missing_slots,
            "status": "NEEDS_CLARIFICATION" if missing_slots else "READY",
        },
    }


def recover_planning_from_session(
    session_state: Optional[Dict[str, Any]],
    *,
    user_id: str,
    error_code: str,
    error_message: str,
) -> Dict[str, Any]:
    """
    Attempt to continue planning from session when NLU output is unavailable.

    Preserves prior behaviour for UpstreamError / empty response / ContractViolation.
    """
    if session_state:
        session_intent_str = _session_intent_str(session_state)

        is_durable = False
        if session_intent_str:
            try:
                is_durable = is_durable_intent(session_intent_str)
            except (ImportError, Exception) as e:
                logger.warning(
                    f"[LUMA_ERROR_FALLBACK] Failed to check durable status: {e}"
                )

        logger.error(
            f"[LUMA_ERROR_FALLBACK] session_intent={session_intent_str}, is_durable={is_durable}, "
            f"session_status={session_state.get('status')}, session_missing_slots={session_state.get('missing_slots', [])}"
        )

        if is_durable:
            return _durable_session_replay_outcome(session_state, session_intent_str)

        if session_state.get("status") == "NEEDS_CLARIFICATION":
            return _needs_clarification_session_outcome(
                session_state, session_intent_str
            )

    return {
        "success": False,
        "error": error_code,
        "message": error_message,
    }
