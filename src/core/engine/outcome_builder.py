"""Outcome dict construction utilities.

Neutral module extracted from orchestrator.py so that both orchestrator.py
and turn_planner.py can import these helpers without a circular dependency.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _build_planning_outcome(
    intent_name: str,
    slots: Dict[str, Any],
    missing_slots: List[str],
    executable_actions: List[str],
    dialog_instruction: Optional[Dict[str, Any]] = None,
    status: str = "READY",
) -> Dict[str, Any]:
    """Build planning-only outcome structure.

    Core NEVER executes — only returns planning information.
    """
    outcome: Dict[str, Any] = {
        "intent": intent_name,
        "slots": slots,
        "missing_slots": missing_slots,
        "executable_actions": executable_actions,
    }
    if dialog_instruction:
        outcome["dialog_instruction"] = dialog_instruction
    return outcome


def build_outcome_from_decision(decision: Dict[str, Any]) -> Dict[str, Any]:
    """Build outcome dictionary from decision object.

    Unifies outcome construction across all return paths by extracting
    all required fields from the decision object.
    """
    if not decision or not isinstance(decision, dict):
        return {
            "intent_name": "",
            "status": "NEEDS_CLARIFICATION",
            "plan": {"status": "NEEDS_CLARIFICATION", "stage": None, "action": None},
            "slots": {},
            "missing_slots": [],
            "blocked_actions": [],
            "allowed_actions": [],
            "facts": {},
        }

    plan = decision.get("plan", {})
    facts = decision.get("facts", {})
    if not isinstance(facts, dict):
        facts = {}

    slots = facts.get("slots", {})
    if not isinstance(slots, dict):
        slots = {}
    missing_slots = facts.get("missing_slots", [])
    if not isinstance(missing_slots, list):
        missing_slots = []

    plan_obj = {
        "status": plan.get("status", "NEEDS_CLARIFICATION"),
        "stage": plan.get("stage"),
        "action": plan.get("action"),
    }

    outcome: Dict[str, Any] = {
        "intent_name": decision.get("intent_name", ""),
        "status": plan.get("status", "NEEDS_CLARIFICATION"),
        "stage": plan.get("stage"),
        "action": plan.get("action"),
        "plan": plan_obj,
        "slots": slots,
        "missing_slots": missing_slots,
        "blocked_actions": plan.get("blocked_actions", []),
        "allowed_actions": plan.get("allowed_actions", []),
        "awaiting": plan.get("awaiting"),
        "facts": facts,
    }

    if plan.get("active_capability"):
        outcome["active_capability"] = plan.get("active_capability")

    return outcome
