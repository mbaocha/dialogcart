"""Fallback response construction when NLU cannot process the current turn.

**Planner admission boundary (Phase 5):** This module is intentionally outside
Decision ownership. It runs before Attach when NLU fails; there is no
``AttachedRequest`` or Evaluate evidence. It preserves recoverable session
state for response shaping but never replays an executable action.

This module does not recover or apply the user's current message.
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


def _fallback_metadata(error_code: str) -> Dict[str, Any]:
    return {
        "recovered": True,
        "nlu_failure_recovery": True,
        "recovery_reason": error_code,
        "message_applied": False,
    }


def _durable_session_recovery_outcome(
    session_state: Dict[str, Any],
    session_intent_str: str,
    error_code: str,
) -> Dict[str, Any]:
    session_slots = session_state.get("slots", {})
    if not isinstance(session_slots, dict):
        session_slots = {}
    session_missing_slots = session_state.get("missing_slots", [])
    if not isinstance(session_missing_slots, list):
        session_missing_slots = []
    session_stage = session_state.get("stage", "AVAILABILITY")
    session_status = session_state.get("status", "NEEDS_CLARIFICATION")
    # Status must be NEEDS_CLARIFICATION if there are missing slots.
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
        "action": None,
        "slots": session_slots,
        "missing_slots": session_missing_slots,
        "status": final_status,
        "plan": {
            "intent": session_intent_str,
            "stage": session_stage,
            "action": None,
            "missing_slots": session_missing_slots,
            "slots": session_slots,
            "status": final_status,
            "executable_actions": [],
        },
        "facts": {
            "slots": session_slots,
            "missing_slots": session_missing_slots,
        },
        **_fallback_metadata(error_code),
    }
    return {"success": True, "outcome": outcome}


def _needs_clarification_session_outcome(
    session_state: Dict[str, Any],
    session_intent_str: str,
    error_code: str,
) -> Dict[str, Any]:
    from core.planning.turn_state import finalize_turn_state

    session_slots = session_state.get("slots", {})
    if not isinstance(session_slots, dict):
        session_slots = {}

    missing_slots: list = []
    if session_intent_str:
        turn_state = finalize_turn_state(
            intent_name=session_intent_str,
            merged_session_slots=session_slots,
            planning_context={
                "date_proposal": session_state.get("date_proposal"),
                "time_proposal": session_state.get("time_proposal"),
                "temporal": session_state.get("temporal"),
                "awaiting_slot": session_state.get("awaiting_slot"),
            },
        )
        missing_slots = turn_state.get("missing_slots") or []

    session_stage = session_state.get("stage", "AVAILABILITY")
    return {
        "success": True,
        "outcome": {
            "intent_name": session_intent_str,
            "stage": session_stage,
            "action": None,
            "slots": session_slots,
            "missing_slots": missing_slots,
            "status": "NEEDS_CLARIFICATION" if missing_slots else "READY",
            **_fallback_metadata(error_code),
        },
    }


def build_nlu_failure_fallback(
    session_state: Optional[Dict[str, Any]],
    *,
    user_id: str,
    error_code: str,
    error_message: str,
    fallback_reason: Optional[str] = None,
) -> Dict[str, Any]:
    """Replay session state when possible; otherwise return the NLU error."""
    recovery_reason = fallback_reason or error_code
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
            f"[LUMA_ERROR_FALLBACK] user_id={user_id}, "
            f"session_intent={session_intent_str}, is_durable={is_durable}, "
            f"session_status={session_state.get('status')}, "
            f"session_missing_slots={session_state.get('missing_slots', [])}"
        )

        if is_durable:
            return _durable_session_recovery_outcome(
                session_state,
                session_intent_str,
                recovery_reason,
            )

        if session_state.get("status") == "NEEDS_CLARIFICATION":
            return _needs_clarification_session_outcome(
                session_state,
                session_intent_str,
                recovery_reason,
            )

    return {
        "success": False,
        "error": error_code,
        "message": error_message,
    }
