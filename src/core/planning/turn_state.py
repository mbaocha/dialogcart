"""
Turn State Resolution

Centralizes the logic for computing effective_collected_slots, missing_slots, and status
from intent and merged_session_slots using the planner.

This is the single source of truth for turn state resolution, ensuring consistency
across all callers. It applies the invariant:
- READY only if missing_slots empty
"""

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from core.planning.policy.action_policy import load_planning_policy, plan_intent

logger = logging.getLogger(__name__)

# Load planning policy once at module level
_planning_policy = None


def _get_planning_policy() -> Dict[str, Any]:
    """Get planning policy, loading it once if needed."""
    global _planning_policy
    if _planning_policy is None:
        _planning_policy = load_planning_policy()
    return _planning_policy


class DecisionReason(str, Enum):
    """Enumeration of reasons for turn status decision."""

    MISSING_REQUIRED_SLOTS = "MISSING_REQUIRED_SLOTS"
    READY_ALL_SATISFIED = "READY_ALL_SATISFIED"
    NEEDS_CONFIRMATION = "NEEDS_CONFIRMATION"
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"


@dataclass
class TurnState:
    """
    Turn State object capturing all turn processing state.

    Built ONLY at the end of turn processing, containing all slot states,
    status, and decision reasoning. This is the single source of truth for
    what happened in a turn and why.
    """

    intent: str
    raw_luma_slots: Dict[str, Any] = field(default_factory=dict)
    merged_session_slots: Dict[str, Any] = field(default_factory=dict)
    promoted_slots: Dict[str, Any] = field(default_factory=dict)
    effective_collected_slots: Dict[str, Any] = field(default_factory=dict)
    required_slots: List[str] = field(default_factory=list)
    missing_slots: List[str] = field(default_factory=list)
    status: str = ""
    decision_reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Convert TurnState to dictionary for JSON serialization."""
        return {
            "intent": self.intent,
            "raw_luma_slots": {
                "keys": (
                    list(self.raw_luma_slots.keys())
                    if isinstance(self.raw_luma_slots, dict)
                    else []
                ),
                "values": (
                    {k: str(v)[:50] for k, v in self.raw_luma_slots.items()}
                    if isinstance(self.raw_luma_slots, dict)
                    else {}
                ),
            },
            "merged_session_slots": {
                "keys": (
                    list(self.merged_session_slots.keys())
                    if isinstance(self.merged_session_slots, dict)
                    else []
                ),
                "values": (
                    {k: str(v)[:50] for k, v in self.merged_session_slots.items()}
                    if isinstance(self.merged_session_slots, dict)
                    else {}
                ),
            },
            "promoted_slots": {
                "keys": (
                    list(self.promoted_slots.keys())
                    if isinstance(self.promoted_slots, dict)
                    else []
                ),
                "values": (
                    {k: str(v)[:50] for k, v in self.promoted_slots.items()}
                    if isinstance(self.promoted_slots, dict)
                    else {}
                ),
            },
            "effective_collected_slots": {
                "keys": (
                    list(self.effective_collected_slots.keys())
                    if isinstance(self.effective_collected_slots, dict)
                    else []
                ),
                "values": (
                    {k: str(v)[:50] for k, v in self.effective_collected_slots.items()}
                    if isinstance(self.effective_collected_slots, dict)
                    else {}
                ),
            },
            "required_slots": self.required_slots,
            "missing_slots": self.missing_slots,
            "status": self.status,
            "decision_reason": self.decision_reason,
        }


def finalize_turn_state(
    intent_name: str,
    merged_session_slots: Dict[str, Any],
    existing_missing_slots: Optional[List[str]] = None,
    planning_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Finalize turn state by computing effective_collected_slots, missing_slots, and status.

    This function centralizes the decision logic for turn state:
    - Uses planner to compute missing_slots from intent and collected slots
    - Applies invariant: READY only if missing_slots empty

    This is the single source of truth for turn state resolution. It does NOT handle:
    - Intent logic (determines what intent is)
    - Promotion logic (determines how slots are promoted)
    - Persistence (determines what gets saved)

    Only centralizes the decision about what slots are collected, what's missing, and status.

    Args:
        intent_name: Intent name (e.g., "CREATE_APPOINTMENT", "CREATE_RESERVATION")
        merged_session_slots: Merged slots from session (after normalization, promotion, etc.)

    Returns:
        Dictionary with:
        - effective_slots: Dict of effective collected slots (all non-None slots)
        - missing_slots: List of missing slot names (computed by planner)
        - status: "READY" or "NEEDS_CLARIFICATION" (based on missing_slots)
    """
    if not intent_name:
        # No intent - return empty state
        return {
            "effective_slots": {},
            "missing_slots": [],
            "status": "NEEDS_CLARIFICATION",
        }

    # Always recompute missing_slots from current slots + proposals.
    # Stale missing_slots from session carry-over must not skip the planner.
    from core.planning.temporal_proposal import expand_slots_for_planning

    policy = _get_planning_policy()
    pc = planning_context or {}
    planning_slots = expand_slots_for_planning(
        merged_session_slots,
        date_proposal=pc.get("date_proposal"),
        time_proposal=pc.get("time_proposal"),
        date_constraint=pc.get("date_constraint"),
        nlu_facts=pc.get("nlu_facts"),
        time_constraint=pc.get("time_constraint"),
        intent_name=intent_name,
    )
    plan = plan_intent(intent_name, planning_slots, policy)

    collected_slot_names = set(plan["collected_slots"])
    missing_slots = plan["missing_slots"]

    if intent_name == "CREATE_APPOINTMENT":
        from core.planning.temporal_proposal import proposal_satisfies_planning_time

        if proposal_satisfies_planning_time(pc.get("time_proposal")) and "time" in missing_slots:
            missing_slots = [s for s in missing_slots if s != "time"]

    if existing_missing_slots is not None and existing_missing_slots != missing_slots:
        logger.debug(
            "[FINALIZE_TURN_STATE] recomputed missing_slots from current slots "
            "(intent=%s stale=%s computed=%s)",
            intent_name,
            existing_missing_slots,
            missing_slots,
        )

    from core.planning.planner.missing_slots import (
        get_planning_required_slots_for_intent,
    )

    try:
        required_slots = get_planning_required_slots_for_intent(intent_name)
    except Exception:
        required_slots = []

    logger.debug(
        "[FINALIZE_TURN_STATE] intent=%s required=%s collected=%s missing=%s",
        intent_name,
        required_slots,
        sorted(collected_slot_names),
        missing_slots,
    )

    # Build effective_collected_slots from merged_session_slots
    # Include all non-None slots (unordered, additive map)
    effective_collected_slots = {
        slot_name: slot_value
        for slot_name, slot_value in merged_session_slots.items()
        if slot_value is not None
    }

    # INVARIANT: READY only if missing_slots empty (and intent is not UNKNOWN)
    # UNKNOWN means we don't know what the user wants — clarify regardless of missing_slots
    if intent_name == "UNKNOWN":
        status = "NEEDS_CLARIFICATION"
        logger.info(
            "[FINALIZE_TURN_STATE] UNKNOWN intent - forcing NEEDS_CLARIFICATION regardless of missing_slots"
        )
    elif len(missing_slots) > 0:
        # Missing slots exist - must be NEEDS_CLARIFICATION
        status = "NEEDS_CLARIFICATION"
    else:
        # No missing slots - can be READY
        # (Note: Caller may still override with AWAITING_CONFIRMATION based on confirmation_state)
        status = "READY"

    logger.info(
        "[FINALIZE_TURN_STATE] intent=%s collected_slots=%s effective_slots=%s missing_slots=%s status=%s",
        intent_name,
        sorted(collected_slot_names),
        sorted(effective_collected_slots.keys()),
        missing_slots,
        status,
    )

    return {
        "effective_slots": effective_collected_slots,
        "missing_slots": missing_slots,
        "status": status,
    }
