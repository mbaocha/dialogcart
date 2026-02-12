"""
Clarification Reason Mapper

Derives a deterministic clarification_reason from planner output.
"""

import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


def derive_clarification_reason(decision: Dict[str, Any]) -> Optional[str]:
    """
    Derive clarification reason from decision/plan object.
    
    Args:
        decision: Decision/plan dictionary with:
            - status: "READY", "NEEDS_CLARIFICATION", or "AWAITING_CONFIRMATION"
            - missing_slots: List of missing slot names (e.g., ["time"], ["date"], [])
    
    Returns:
        Clarification reason string if status is NEEDS_CLARIFICATION, None otherwise.
        Mapping:
            - First missing slot → "MISSING_<SLOT_NAME_UPPER>" (e.g., ["date"] → "MISSING_DATE")
            - Empty missing_slots → "NEEDS_CLARIFICATION"
    """
    status = decision.get("status")
    
    # If status is not NEEDS_CLARIFICATION, return None
    if status != "NEEDS_CLARIFICATION":
        return None
    
    # Get missing_slots from decision
    missing_slots = decision.get("missing_slots", [])
    
    # Ensure missing_slots is a list
    if not isinstance(missing_slots, list):
        missing_slots = []
    
    # Simple logic: Use first missing slot if available
    if missing_slots:
        first = missing_slots[0]
        if isinstance(first, str) and first:
            # Convert slot name to uppercase and generate reason
            reason = f"MISSING_{first.upper()}"
            print(f"[MAPPER_DEBUG] Generated reason: {reason!r} from slot: {first!r}")
            print(f"[MAPPER_DEBUG] reason type: {type(reason)}, reason repr: {repr(reason)}")
            logger.debug(
                f"[SMART_MAPPING] Generated clarification reason '{reason}' from first missing slot '{first}'"
            )
            return reason
    
    # Fallback to generic reason if missing_slots is empty
    return "NEEDS_CLARIFICATION"

