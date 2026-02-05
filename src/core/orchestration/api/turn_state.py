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
from typing import Dict, Any, Optional, List
from enum import Enum

from core.planning.policy.action_policy import plan_intent, load_planning_policy

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
                "keys": list(self.raw_luma_slots.keys()) if isinstance(self.raw_luma_slots, dict) else [],
                "values": {k: str(v)[:50] for k, v in self.raw_luma_slots.items()} if isinstance(self.raw_luma_slots, dict) else {}
            },
            "merged_session_slots": {
                "keys": list(self.merged_session_slots.keys()) if isinstance(self.merged_session_slots, dict) else [],
                "values": {k: str(v)[:50] for k, v in self.merged_session_slots.items()} if isinstance(self.merged_session_slots, dict) else {}
            },
            "promoted_slots": {
                "keys": list(self.promoted_slots.keys()) if isinstance(self.promoted_slots, dict) else [],
                "values": {k: str(v)[:50] for k, v in self.promoted_slots.items()} if isinstance(self.promoted_slots, dict) else {}
            },
            "effective_collected_slots": {
                "keys": list(self.effective_collected_slots.keys()) if isinstance(self.effective_collected_slots, dict) else [],
                "values": {k: str(v)[:50] for k, v in self.effective_collected_slots.items()} if isinstance(self.effective_collected_slots, dict) else {}
            },
            "required_slots": self.required_slots,
            "missing_slots": self.missing_slots,
            "status": self.status,
            "decision_reason": self.decision_reason
        }


def finalize_turn_state(
    intent_name: str,
    merged_session_slots: Dict[str, Any]
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
            "status": "NEEDS_CLARIFICATION"
        }
    
    # Use planner to compute missing_slots
    # Slots are treated as an unordered, additive map
    # CRITICAL: For MODIFY_BOOKING, use compute_missing_slots which properly handles
    # context-aware required slots. plan_intent doesn't handle MODIFY_BOOKING correctly
    # because it's not in intent_policy.yaml.
    if intent_name == "MODIFY_BOOKING":
        from core.planning.orchestration.missing_slots import compute_missing_slots
        # Use compute_missing_slots which properly handles MODIFY_BOOKING
        missing_slots = compute_missing_slots(
            intent_name=intent_name,
            collected_slots=merged_session_slots,
            modification_context=None,  # Will be derived from collected_slots
            session_state=None,
            time_constraint=None
        )
        # Extract collected slot names
        collected_slot_names = set([
            slot_name for slot_name, slot_value in merged_session_slots.items()
            if slot_value is not None
        ])
    else:
        # For other intents, use plan_intent
        policy = _get_planning_policy()
        plan = plan_intent(intent_name, merged_session_slots, policy)
        
        # Extract results from planner
        collected_slot_names = set(plan["collected_slots"])
        missing_slots = plan["missing_slots"]
    
    # INVESTIGATION: Log initial missing_slots computation from planner
    from core.planning.orchestration.missing_slots import get_planning_required_slots_for_intent
    try:
        required_slots = get_planning_required_slots_for_intent(intent_name)
    except (ImportError, Exception):
        required_slots = []
    
    logger.error(
        f"[MISSING_SLOTS_TRACE] finalize_turn_state: INITIAL computation from planner, "
        f"intent={intent_name}, "
        f"required_slots={required_slots}, "
        f"collected_slots={sorted(collected_slot_names)}, "
        f"effective_collected_slots_keys={sorted([k for k, v in merged_session_slots.items() if v is not None])}, "
        f"missing_slots={missing_slots}, "
        f"missing_slots_length={len(missing_slots)}"
    )
    
    # Build effective_collected_slots from merged_session_slots
    # Include all non-None slots (unordered, additive map)
    effective_collected_slots = {
        slot_name: slot_value
        for slot_name, slot_value in merged_session_slots.items()
        if slot_value is not None
    }
    
    # CRITICAL PLANNING INVARIANT: UNKNOWN intent ALWAYS requires clarification
    # UNKNOWN means we don't know what the user wants, so we must clarify regardless of missing_slots
    # This prevents UNKNOWN from being marked as READY even when missing_slots is empty
    if intent_name == "UNKNOWN":
        status = "NEEDS_CLARIFICATION"
        logger.info(
            f"[FINALIZE_TURN_STATE] UNKNOWN intent - forcing NEEDS_CLARIFICATION regardless of missing_slots"
        )
    # Determine status based on missing_slots
    # INVARIANT: READY only if missing_slots empty (and intent is not UNKNOWN)
    elif len(missing_slots) > 0:
        # Missing slots exist - must be NEEDS_CLARIFICATION
        status = "NEEDS_CLARIFICATION"
    else:
        # No missing slots - can be READY
        # (Note: Caller may still override with AWAITING_CONFIRMATION based on confirmation_state)
        status = "READY"
    
    logger.info(
        f"[FINALIZE_TURN_STATE] intent={intent_name}, "
        f"collected_slots={sorted(collected_slot_names)}, "
        f"effective_slots={sorted(effective_collected_slots.keys())}, "
        f"missing_slots={missing_slots}, "
        f"status={status}"
    )
    
    return {
        "effective_slots": effective_collected_slots,
        "missing_slots": missing_slots,
        "status": status
    }

