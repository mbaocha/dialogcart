"""
Capability boundary — single integration point between core planning and extensions.

Core planning (turn_planner) emits AWAITING_CAPABILITY; this module owns runner
invocation, presentation rendering, and fact merging at the API boundary.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from core.rendering.llm_renderer import LlmRenderRequest, render_llm

logger = logging.getLogger(__name__)


def build_capability_context(
    outcome: Dict[str, Any],
    *,
    user_id: str,
    session_state: Optional[Dict[str, Any]],
    domain: Optional[str],
    timezone: Optional[str],
    organization_id: int,
    transaction_id: Optional[str],
    decision: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build runner context from session, outcome, and optional decision booking."""
    session_slots: Dict[str, Any] = {}
    session_facts: Dict[str, Any] = {}

    if session_state and isinstance(session_state, dict):
        raw_slots = session_state.get("slots", {})
        if isinstance(raw_slots, dict):
            session_slots.update(raw_slots)
        raw_facts = session_state.get("facts", {})
        if isinstance(raw_facts, dict):
            session_facts.update(raw_facts)

    outcome_slots = outcome.get("slots", {})
    if isinstance(outcome_slots, dict):
        session_slots.update(outcome_slots)

    outcome_facts = outcome.get("facts", {})
    if isinstance(outcome_facts, dict):
        session_facts.update(outcome_facts)

    booking = (decision or {}).get("booking", {})
    if isinstance(booking, dict):
        if "booking_id" in booking and "booking_id" not in session_slots:
            session_slots["booking_id"] = booking.get("booking_id")
        if "booking_code" in booking and "booking_code" not in session_slots:
            session_slots["booking_code"] = booking.get("booking_code")
        if "code" in booking and "booking_code" not in session_slots:
            session_slots["booking_code"] = booking.get("code")
        if "total_amount" in booking and "total_amount" not in session_slots:
            session_slots["total_amount"] = booking.get("total_amount")
        if "amount" in booking and "total_amount" not in session_slots:
            session_slots["total_amount"] = booking.get("amount")
        if "currency" in booking and "currency" not in session_slots:
            session_slots["currency"] = booking.get("currency")

    return {
        "user_id": user_id,
        "session_slots": session_slots,
        "session_facts": session_facts,
        "domain": domain,
        "timezone": timezone,
        "organization_id": organization_id,
        "transaction_id": transaction_id,
    }
def apply_capability_to_result(
    result: Dict[str, Any],
    runner: Any,
    *,
    user_id: str,
    user_text: str,
    session_state: Optional[Dict[str, Any]],
    domain: Optional[str] = "service",
    timezone: Optional[str] = "UTC",
    organization_id: int,
    transaction_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Run capability boundary for AWAITING_CAPABILITY outcomes.

    Mutates result in place when adapter completes. Returns an early-response
    payload when the adapter is active (caller should return without persisting).
    """
    outcome = result.get("outcome")
    if not outcome or not isinstance(outcome, dict):
        return None
    if outcome.get("status") != "AWAITING_CAPABILITY":
        return None
    if not outcome.get("active_capability"):
        logger.warning("AWAITING_CAPABILITY without active_capability in outcome")
        return None

    decision = result.get("_decision") if isinstance(result.get("_decision"), dict) else None
    context = build_capability_context(
        outcome,
        user_id=user_id,
        session_state=session_state,
        domain=domain,
        timezone=timezone,
        organization_id=organization_id,
        transaction_id=transaction_id,
        decision=decision,
    )

    active_capability = outcome.get("active_capability")
    runner_result = runner.handle(
        user_input=user_text or None,
        core_outcome=outcome,
        context=context,
    )

    if not runner_result.passthrough:
        adapter_text = runner_result.text or ""
        if adapter_text:
            render_instruction = (
                "Present the following action request to the user naturally. "
                f"Preserve any URLs or payment links exactly as given:\n{adapter_text}"
            )
        else:
            render_instruction = "Ask the user to complete the required action to continue with their booking."
        prompt_text = render_llm(LlmRenderRequest(
            render_instruction=render_instruction,
            facts={"structured_context": {}},
        ))
        return {
            "success": True,
            "outcome": {
                "status": "AWAITING_CAPABILITY",
                "text": prompt_text,
                "active_capability": runner_result.active_capability
                or active_capability,
                "awaiting": "CAPABILITY",
            },
        }

    if runner_result.facts:
        if "facts" not in outcome or not isinstance(outcome.get("facts"), dict):
            outcome["facts"] = {}
        outcome["facts"].update(runner_result.facts)
        outcome["active_capability"] = None
        outcome["status"] = "READY"
        outcome["awaiting"] = None
        result["outcome"] = outcome
        logger.info(
            "[capability] Adapter completed, merged facts: %s user_id=%s",
            list(runner_result.facts.keys()),
            user_id,
        )

    return None
