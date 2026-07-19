"""Stage 07 — canonical capability gating."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from core.config.capabilities_loader import load_capability_policies
from core.planning.pipeline.types import (
    CapabilityDecision,
    ConfirmationDecision,
    SlotTurnState,
    WorkingTurn,
)
from core.policy.intent_policy import get_commit_action

logger = logging.getLogger(__name__)


def _evaluate_condition(
    condition_expr: str,
    facts: Dict[str, Any],
    slots: Dict[str, Any],
    session_state: Optional[Dict[str, Any]] = None,
) -> bool:
    try:
        parts = condition_expr.strip().split()
        if len(parts) != 3:
            return False
        left_side, operator, right_side = parts
        if "." not in left_side:
            return False
        namespace, key = left_side.split(".", 1)
        if namespace == "org":
            org_data = facts.get("org") if isinstance(facts, dict) else None
            if org_data is None and session_state:
                org_data = session_state.get("org")
            value = org_data.get(key) if isinstance(org_data, dict) else None
        elif namespace == "facts":
            value = facts.get(key) if isinstance(facts, dict) else None
        elif namespace == "slots":
            value = slots.get(key) if isinstance(slots, dict) else None
        else:
            return False
        if right_side.lower() == "true":
            expected_value = True
        elif right_side.lower() == "false":
            expected_value = False
        else:
            try:
                expected_value = float(right_side)
                if isinstance(value, (int, float)):
                    value = float(value)
            except ValueError:
                expected_value = right_side
        if operator == "==":
            return value == expected_value
        if operator == "!=":
            return value != expected_value
        return False
    except Exception:
        return False


def _evaluate_capability_blocking(
    *,
    intent_name: str,
    next_action: Optional[str],
    effective_slots: Dict[str, Any],
    payload: Dict[str, Any],
    session_state: Optional[Dict[str, Any]],
) -> Optional[str]:
    if not next_action:
        return None
    booking_id = effective_slots.get("booking_id")
    if not booking_id:
        return None

    policies = load_capability_policies()
    capabilities = policies.get("capabilities", {})
    if not capabilities:
        return None

    facts: Dict[str, Any] = {}
    if isinstance(session_state, dict):
        facts = session_state.get("facts", {})
        if not isinstance(facts, dict):
            facts = {}
    luma_facts = payload.get("facts", {})
    if isinstance(luma_facts, dict):
        facts = {**facts, **luma_facts}

    for capability_name, capability_config in capabilities.items():
        if not isinstance(capability_config, dict):
            continue
        applies_to = capability_config.get("applies_to", {})
        required_intent = applies_to.get("intent") if isinstance(applies_to, dict) else None
        if required_intent and required_intent != intent_name:
            continue
        blocks = capability_config.get("blocks", [])
        if not isinstance(blocks, list) or next_action not in blocks:
            continue
        when_condition = capability_config.get("when", {})
        when_all = when_condition.get("all", []) if isinstance(when_condition, dict) else []
        if not isinstance(when_all, list):
            continue
        all_met = True
        for condition_expr in when_all:
            if not isinstance(condition_expr, str):
                all_met = False
                break
            if not _evaluate_condition(condition_expr, facts, effective_slots, session_state):
                all_met = False
                break
        if all_met:
            return capability_name
    return None


def _payment_capability_decision(
    *,
    payload: Dict[str, Any],
    session_state: Optional[Dict[str, Any]],
    confirmation: ConfirmationDecision,
    slot_state: SlotTurnState,
) -> Optional[CapabilityDecision]:
    org_data = None
    facts = payload.get("facts", {})
    if isinstance(facts, dict):
        org_data = facts.get("org")
    payment_required = bool(
        isinstance(org_data, dict) and org_data.get("payment_required", False)
    )
    if not payment_required:
        return None

    payment_satisfied = False
    if isinstance(session_state, dict):
        session_facts = session_state.get("facts", {})
        if isinstance(session_facts, dict):
            session_payment = session_facts.get("payment_satisfied")
            if session_payment is not None:
                payment_satisfied = bool(session_payment)
    if not payment_satisfied and isinstance(facts, dict):
        outcome_payment = facts.get("payment_satisfied")
        if outcome_payment is not None:
            payment_satisfied = bool(outcome_payment)

    if payment_required and not payment_satisfied:
        if slot_state.missing_slots:
            return None
        return CapabilityDecision(
            active_capability="payment",
            awaiting_capability=True,
            awaiting_kind="PAYMENT",
        )
    return None


def resolve_capability_gating(
    *,
    slot_state: SlotTurnState,
    working_turn: WorkingTurn,
    availability_ready: bool,
    confirmation: ConfirmationDecision,
    session_state: Optional[Dict[str, Any]],
    organization_id: int,
) -> CapabilityDecision:
    payload = working_turn.payload
    intent_name = slot_state.intent_name
    missing_slots = slot_state.missing_slots
    needs_clarification = slot_state.needs_clarification

    payment_decision = _payment_capability_decision(
        payload=payload,
        session_state=session_state,
        confirmation=confirmation,
        slot_state=slot_state,
    )
    if payment_decision is not None:
        return payment_decision

    active_capability = None
    if isinstance(session_state, dict):
        active_capability = session_state.get("active_capability")

    evaluated = None
    booking_id = slot_state.effective_collected_slots.get("booking_id")
    if (
        not missing_slots
        and not needs_clarification
        and intent_name != "UNKNOWN"
        and booking_id
    ):
        commit_action = get_commit_action(intent_name)
        evaluated = _evaluate_capability_blocking(
            intent_name=intent_name,
            next_action=commit_action,
            effective_slots=slot_state.effective_collected_slots,
            payload=payload,
            session_state=session_state,
        )
        if evaluated and not active_capability:
            active_capability = evaluated

    if active_capability:
        return CapabilityDecision(
            active_capability=active_capability,
            awaiting_capability=True,
            awaiting_kind="CAPABILITY",
        )
    return CapabilityDecision()
