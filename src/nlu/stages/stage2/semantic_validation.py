"""Deterministic validation for final Stage 2 proposal-response evidence.

This module consumes generated structured evidence and structured Core context only.
It never interprets raw user text and never applies Core confirmation lifecycle effects.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_PROPOSAL_RESPONSES = frozenset({"ACCEPT", "REJECT", "MODIFY"})
_DIALOG_INTENTS = frozenset({"CONFIRM_ACTION", "REJECT_ACTION"})
_EMPTY_FACT_KEYS = frozenset(
    {"dates", "times", "date_time_pairs", "service_id", "booking_id"}
)


def canonical_confirmation_pending(
    conversation_context: Optional[Dict[str, Any]],
) -> bool:
    """Whether structured context permits confirmation interpretation this turn."""
    if not isinstance(conversation_context, dict):
        return False
    if conversation_context.get("confirmation_state") != "pending":
        return False
    # Requested input is authoritative slot-fill context and takes precedence.
    return not bool(conversation_context.get("pending_profile_request"))


def _has_changed_facts(result: Dict[str, Any]) -> bool:
    facts = result.get("facts")
    if isinstance(facts, dict):
        for key, value in facts.items():
            if key in _EMPTY_FACT_KEYS and value in (None, [], ""):
                continue
            if value not in (None, [], ""):
                return True
    temporal = result.get("temporal")
    if isinstance(temporal, dict) and any(
        temporal.get(key)
        for key in (
            "start_date_expression", "start_time_expression",
            "end_date_expression", "end_time_expression",
            "start_date", "start_time", "end_date", "end_time",
        )
    ):
        return True
    mentions = result.get("_entity_mentions")
    if isinstance(mentions, dict):
        for evidence in mentions.values():
            state = getattr(evidence, "state", None)
            state_value = getattr(state, "value", state)
            if state_value in {"MENTIONED_VALUE", "MENTIONED_UNRESOLVED"}:
                return True
    return False


def _context_workflow_intent(
    conversation_context: Optional[Dict[str, Any]],
) -> Optional[str]:
    if not isinstance(conversation_context, dict):
        return None
    proposals = conversation_context.get("pending_assistant_proposals")
    if isinstance(proposals, list):
        for proposal in reversed(proposals):
            if not isinstance(proposal, dict) or proposal.get("status") != "PENDING":
                continue
            for key in ("workflow_intent", "underlying_intent", "intent", "operation"):
                value = proposal.get(key)
                if isinstance(value, str) and value:
                    return value
    for key in ("active_booking_intent", "last_intent"):
        value = conversation_context.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def validate_final_stage2_result(
    result: Dict[str, Any],
    conversation_context: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Normalize safe legacy forms and suppress contradictory authorization."""
    validated = dict(result)
    field_present = "proposal_response" in validated
    response = validated.get("proposal_response")
    if response not in _PROPOSAL_RESPONSES:
        response = None

    eligible = canonical_confirmation_pending(conversation_context)
    intent = validated.get("intent")
    changed = _has_changed_facts(validated)
    workflow_intent = _context_workflow_intent(conversation_context)

    # Safe legacy normalization is allowed only under canonical authorization.
    if not field_present and eligible:
        if intent == "CONFIRM_ACTION":
            response = "ACCEPT"
        elif intent == "REJECT_ACTION":
            response = "REJECT"

    if not eligible or intent == "UNKNOWN":
        response = None

    # A different final request owns the turn. Core may classify it as
    # ANOTHER_REQUEST; do not attach authorization to that destination intent.
    if (
        response in {"ACCEPT", "REJECT"}
        and workflow_intent
        and intent not in {workflow_intent, *_DIALOG_INTENTS}
    ):
        response = None

    # A correction or material current-turn change never authorizes the stale proposal.
    if response == "ACCEPT" and (intent == "CORRECTION" or changed):
        logger.warning(
            "Stage2 semantic validation suppressed stale proposal acceptance "
            "intent=%r changed_facts=%s", intent, changed,
        )
        response = None

    # MODIFY is semantic evidence that the previous proposal was not accepted.
    if response == "MODIFY":
        public_response_act = None
    elif response == "ACCEPT":
        public_response_act = "CONFIRM_ACTION"
    elif response == "REJECT":
        public_response_act = "REJECT_ACTION"
    else:
        public_response_act = None

    validated["proposal_response"] = response
    validated["response_act"] = public_response_act
    return validated
