"""
Turn State Resolution

Centralizes the logic for computing effective_collected_slots, missing_slots, and status
from intent, merged_session_slots, and awaiting_slot.

This is the single source of truth for turn state resolution, ensuring consistency
across all callers. It applies the invariant:
- READY only if missing_slots empty AND awaiting_slot is None
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, Any, Optional, List
from enum import Enum

from core.orchestration.api.slot_contract import get_required_slots_for_intent

logger = logging.getLogger(__name__)


class DecisionReason(str, Enum):
    """Enumeration of reasons for turn status decision."""
    AWAITING_SLOT_BLOCK = "AWAITING_SLOT_BLOCK"
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
    awaiting_slot_before: Optional[str] = None
    awaiting_slot_after: Optional[str] = None
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
            "awaiting_slot_before": self.awaiting_slot_before,
            "awaiting_slot_after": self.awaiting_slot_after,
            "status": self.status,
            "decision_reason": self.decision_reason
        }


def finalize_turn_state(
    intent_name: str,
    merged_session_slots: Dict[str, Any],
    awaiting_slot: Optional[str] = None
) -> Dict[str, Any]:
    """
    Finalize turn state by computing effective_collected_slots, missing_slots, and status.
    
    This function centralizes the decision logic for turn state:
    - Computes effective_collected_slots from merged_session_slots (filtered by required slots)
    - Recomputes missing_slots from required(intent) - effective_collected_slots
    - Applies invariant: READY only if missing_slots empty AND awaiting_slot is None
    
    This is the single source of truth for turn state resolution. It does NOT handle:
    - Intent logic (determines what intent is)
    - Promotion logic (determines how slots are promoted)
    - Persistence (determines what gets saved)
    
    Only centralizes the decision about what slots are collected, what's missing, and status.
    
    Args:
        intent_name: Intent name (e.g., "CREATE_APPOINTMENT", "CREATE_RESERVATION")
        merged_session_slots: Merged slots from session (after normalization, promotion, etc.)
        awaiting_slot: Optional slot name that the session is awaiting (from previous turn)
        
    Returns:
        Dictionary with:
        - effective_slots: Dict of effective collected slots (filtered by required slots)
        - missing_slots: List of missing slot names (sorted)
        - status: "READY" or "NEEDS_CLARIFICATION" (based on missing_slots and awaiting_slot)
    """
    if not intent_name:
        # No intent - return empty state
        return {
            "effective_slots": {},
            "missing_slots": [],
            "status": "NEEDS_CLARIFICATION"
        }
    
    # Get required slots for intent
    required_slots_set = set(get_required_slots_for_intent(intent_name))
    
    # Build effective_collected_slots from merged_session_slots
    # Filter to only required slots that have non-None values
    # This matches what Core persists: durable+promoted slots filtered by intent requirements
    effective_collected_slots = {
        slot_name: slot_value
        for slot_name, slot_value in merged_session_slots.items()
        if slot_name in required_slots_set and slot_value is not None
    }
    
    # Also include service_id if present (common across intents)
    if "service_id" in merged_session_slots and merged_session_slots["service_id"] is not None:
        effective_collected_slots["service_id"] = merged_session_slots["service_id"]
    
    # CRITICAL: Clear awaiting_slot if the awaited slot is satisfied
    # After promotion + effective_collected_slots are computed, check if awaiting_slot
    # is present in effective_collected_slots. If so, clear it BEFORE status gating.
    # This prevents the system from forcing NEEDS_CLARIFICATION after the user answers.
    # Do NOT clear awaiting_slot based on "missing_slots empty" alone—only clear when
    # the specific awaited slot key is satisfied.
    effective_awaiting_slot = awaiting_slot
    if awaiting_slot is not None and awaiting_slot in effective_collected_slots:
        # The awaited slot has been satisfied - clear it
        effective_awaiting_slot = None
        logger.info(
            f"[AWAITING_SLOT_CLEAR] Cleared awaiting_slot={awaiting_slot} because it is now "
            f"present in effective_collected_slots: {list(effective_collected_slots.keys())}"
        )
    
    # Compute missing_slots from effective_collected_slots
    # missing_slots = required_slots - effective_collected_slots
    missing_slots = sorted(required_slots_set - set(effective_collected_slots.keys()))
    
    # Determine status based on missing_slots and effective_awaiting_slot
    # INVARIANT: READY only if missing_slots empty AND awaiting_slot is None
    # Use effective_awaiting_slot (may be cleared if satisfied above)
    if len(missing_slots) > 0:
        # Missing slots exist - must be NEEDS_CLARIFICATION
        status = "NEEDS_CLARIFICATION"
    elif effective_awaiting_slot is not None:
        # effective_awaiting_slot is set - must be NEEDS_CLARIFICATION (even if missing_slots is empty)
        # Note: effective_awaiting_slot may have been cleared above if the awaited slot was satisfied
        status = "NEEDS_CLARIFICATION"
    else:
        # No missing slots and no effective_awaiting_slot - can be READY
        # (Note: Caller may still override with AWAITING_CONFIRMATION based on confirmation_state)
        status = "READY"
    
    logger.info(
        f"[FINALIZE_TURN_STATE] intent={intent_name}, "
        f"required_slots={list(required_slots_set)}, "
        f"effective_slots={list(effective_collected_slots.keys())}, "
        f"missing_slots={missing_slots}, "
        f"awaiting_slot_input={awaiting_slot}, "
        f"awaiting_slot_effective={effective_awaiting_slot}, "
        f"status={status}"
    )
    
    return {
        "effective_slots": effective_collected_slots,
        "missing_slots": missing_slots,
        "status": status,
        "awaiting_slot_before": awaiting_slot,
        "awaiting_slot_after": effective_awaiting_slot
    }

