"""
Decision / Policy Layer

Pure function that decides whether a booking is RESOLVED or NEEDS_CLARIFICATION
based on the semantic dictionary (resolved_booking) and configurable policy.

Policy operates ONLY on semantic roles (time_mode, time_constraint, etc.),
never on raw text or regex patterns.
"""

from dataclasses import dataclass
from typing import Dict, Any, Optional, Literal


@dataclass
class DecisionResult:
    """
    Decision result from the policy layer.
    
    Attributes:
        status: "RESOLVED" or "NEEDS_CLARIFICATION"
        reason: None if RESOLVED, otherwise one of the clarification reason codes
        effective_time: Information about the effective time resolution
    """
    status: Literal["RESOLVED", "NEEDS_CLARIFICATION"]
    reason: Optional[str] = None
    effective_time: Optional[Dict[str, Any]] = None


def decide_booking_status(
    resolved_booking: Dict[str, Any],
    entities: Optional[Dict[str, Any]] = None,
    policy: Optional[Dict[str, bool]] = None
) -> DecisionResult:
    """
    Pure function that decides booking status based on semantic dictionary and policy.
    
    Policy operates ONLY on semantic roles (time_mode, time_constraint, etc.),
    never on raw text or regex patterns.
    
    Args:
        resolved_booking: The resolved booking dictionary from semantic resolution.
                         Contains: services, date_mode, date_refs, time_mode,
                         time_refs, duration, time_constraint
        entities: Optional raw entities for additional context (not used in current logic)
        policy: Optional policy configuration dict with:
               - allow_time_windows: bool (default True)
               - allow_constraint_only_time: bool (default True)
    
    Returns:
        DecisionResult with status, reason, and effective_time information
    """
    # Default policy values
    if policy is None:
        policy = {
            "allow_time_windows": True,
            "allow_constraint_only_time": True
        }
    
    allow_time_windows = policy.get("allow_time_windows", True)
    allow_constraint_only_time = policy.get("allow_constraint_only_time", True)
    
    # Extract key fields from resolved_booking (semantic roles only)
    services = resolved_booking.get("services", [])
    date_mode = resolved_booking.get("date_mode", "none")
    date_refs = resolved_booking.get("date_refs", [])
    time_mode = resolved_booking.get("time_mode", "none")
    time_refs = resolved_booking.get("time_refs", [])
    time_constraint = resolved_booking.get("time_constraint")
    date_range = resolved_booking.get("date_range")
    time_range = resolved_booking.get("time_range")
    
    # SYSTEM INVARIANT:
    # A booking with resolved date + time must always be RESOLVED
    # This overrides all other logic paths to prevent regressions
    has_resolved_date = (
        (date_refs and date_mode != "none") or 
        (date_range is not None)
    )
    has_resolved_time = (
        (time_refs and time_mode != "none") or 
        (time_constraint is not None) or
        (time_range is not None)
    )
    
    if has_resolved_date and has_resolved_time:
        # Determine effective_time information for the invariant path
        effective_time = _determine_effective_time(
            time_mode, time_refs, time_constraint
        )
        return DecisionResult(
            status="RESOLVED",
            reason=None,
            effective_time=effective_time
        )
    
    # Determine effective_time information
    effective_time = _determine_effective_time(
        time_mode, time_refs, time_constraint
    )
    
    # Policy checks: Operate on semantic roles only
    # Policy 1: Reject time windows if not allowed
    if time_mode == "window" and not allow_time_windows:
        return DecisionResult(
            status="NEEDS_CLARIFICATION",
            reason="MISSING_TIME",  # Time window not allowed, need exact time
            effective_time=effective_time
        )
    
    # Policy 2: Reject constraint-only times if not allowed
    if time_constraint and time_mode == "none" and not allow_constraint_only_time:
        return DecisionResult(
            status="NEEDS_CLARIFICATION",
            reason="MISSING_TIME",  # Constraint-only time not allowed, need exact time
            effective_time=effective_time
        )
    
    # Decision logic: Check for missing required information
    
    # Rule 1: Missing date when time is provided (except time constraints)
    if time_mode != "none" and not time_constraint:
        if not date_refs or date_mode == "none":
            return DecisionResult(
                status="NEEDS_CLARIFICATION",
                reason="MISSING_DATE",
                effective_time=effective_time
            )
    
    # Rule 2: Missing time when date is provided (for CREATE_BOOKING intent)
    # Note: This is a policy decision - we require time for bookings
    if date_refs and date_mode != "none":
        if time_mode == "none" and not time_constraint:
            return DecisionResult(
                status="NEEDS_CLARIFICATION",
                reason="MISSING_TIME",
                effective_time=effective_time
            )
    
    # Rule 3: Time constraint without date
    if time_constraint and not date_refs:
        return DecisionResult(
            status="NEEDS_CLARIFICATION",
            reason="MISSING_DATE",
            effective_time=effective_time
        )
    
    # Rule 4: Constraint-only times with date are RESOLVED
    # Time constraints like "by 4pm" are valid when date is present
    if time_constraint and date_refs and date_mode != "none":
        # Constraint-only time (no regular time_refs) with date is RESOLVED
        # Calendar binding will handle constraint-only times appropriately
        return DecisionResult(
            status="RESOLVED",
            reason=None,
            effective_time=effective_time
        )
    
    # Rule 5: All required information present
    # If we have date and (time or time_constraint), we're resolved
    has_date = date_refs and date_mode != "none"
    has_time = time_mode != "none" or time_constraint is not None
    
    if has_date and has_time:
        return DecisionResult(
            status="RESOLVED",
            reason=None,
            effective_time=effective_time
        )
    
    # Rule 6: Fallback - if we have neither date nor time, needs clarification
    if not has_date and not has_time:
        return DecisionResult(
            status="NEEDS_CLARIFICATION",
            reason="MISSING_DATE",  # Primary missing field
            effective_time=effective_time
        )
    
    # Default: RESOLVED (conservative - let existing logic handle edge cases)
    return DecisionResult(
        status="RESOLVED",
        reason=None,
        effective_time=effective_time
    )


def _determine_effective_time(
    time_mode: str,
    time_refs: list,
    time_constraint: Optional[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """
    Determine effective time information.
    
    Returns:
        Dict with "mode" ("exact" | "window") and "source" ("primary" | "constraint" | "window")
    """
    # If we have a time constraint, that's the effective time source
    # Constraints are treated as "exact" mode with "constraint" source
    if time_constraint:
        return {
            "mode": "exact",  # Constraints specify exact times (e.g., "by 4pm")
            "source": "constraint"
        }
    
    # If we have exact time, that's primary
    if time_mode == "exact" and time_refs:
        return {
            "mode": "exact",
            "source": "primary"
        }
    
    # If we have time window, that's the source
    if time_mode == "window" and time_refs:
        return {
            "mode": "window",
            "source": "window"
        }
    
    # If we have range, treat as "exact" mode (range is a flexible exact time)
    if time_mode == "range" and time_refs:
        return {
            "mode": "exact",  # Range is treated as exact time window
            "source": "primary"
        }
    
    # No time information - return None to indicate no effective time
    return None

