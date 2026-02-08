"""
Clarification Reason Mapper

Derives a deterministic clarification_reason from planner output.
"""

from typing import Dict, Any, Optional


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
            - ["time"] → "MISSING_TIME"
            - ["date"] → "MISSING_DATE"
            - [] → "NEEDS_CLARIFICATION"
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
    
    # Map missing_slots to clarification reason
    if missing_slots == ["time"]:
        return "MISSING_TIME"
    elif missing_slots == ["date"]:
        return "MISSING_DATE"
    elif missing_slots == []:
        return "NEEDS_CLARIFICATION"
    else:
        # For other cases (e.g., multiple missing slots), return generic reason
        return "NEEDS_CLARIFICATION"

