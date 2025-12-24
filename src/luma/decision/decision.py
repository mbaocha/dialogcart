"""
Decision / Policy Layer

Pure function that decides whether a booking is RESOLVED or NEEDS_CLARIFICATION
based on the semantic dictionary (resolved_booking) and configurable policy.

Policy operates ONLY on semantic roles (time_mode, time_constraint, etc.),
never on raw text or regex patterns.
"""

from dataclasses import dataclass
from typing import Dict, Any, Optional, Literal, Tuple
from ..config.temporal import (
    APPOINTMENT_TEMPORAL_TYPE,
    INTENT_TEMPORAL_SHAPE,
    RESERVATION_TEMPORAL_TYPE,
    TimeMode,
)


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


def _validate_temporal_shape_for_decision(
    intent_name: Optional[str],
    resolved_booking: Dict[str, Any]
) -> Optional[str]:
    """
    Validate temporal shape completeness for decision layer.

    Returns:
        Clarification reason code if temporal shape incomplete, None if complete.
    """
    if not intent_name:
        return None

    temporal_shape = INTENT_TEMPORAL_SHAPE.get(intent_name)
    if not temporal_shape:
        # No temporal shape requirement for this intent
        return None

    date_mode = resolved_booking.get("date_mode", "none")
    date_refs = resolved_booking.get("date_refs", [])
    time_mode = resolved_booking.get("time_mode", "none")
    time_constraint = resolved_booking.get("time_constraint")

    if temporal_shape == APPOINTMENT_TEMPORAL_TYPE:
        # CREATE_APPOINTMENT requires datetime_range:
        # - Must have valid date (date_mode != "none" and date_refs present)
        # - Must have valid time:
        #   * time_mode in {exact, range, window} with time_refs OR time_constraint, OR
        #   * time_constraint with mode in {exact, window, fuzzy}
        has_valid_date = (
            date_mode != "none"
            and date_mode != "flexible"
            and len(date_refs) > 0
        )

        time_refs = resolved_booking.get("time_refs", [])
        has_valid_time = False
        if time_constraint is not None:
            tc_mode = time_constraint.get("mode")
            if tc_mode in {TimeMode.EXACT.value, TimeMode.WINDOW.value, TimeMode.FUZZY.value}:
                has_valid_time = True
        elif time_mode in {TimeMode.EXACT.value, TimeMode.RANGE.value, TimeMode.WINDOW.value}:
            # time_mode is set, but need time_refs or time_constraint to construct datetime_range
            if len(time_refs) > 0:
                has_valid_time = True

        if not has_valid_time:
            return "MISSING_TIME"
        if not has_valid_date:
            return "MISSING_DATE"

    elif temporal_shape == RESERVATION_TEMPORAL_TYPE:
        # CREATE_RESERVATION requires date_range:
        # - Must have start_date (at least 1 date_ref)
        # - Must have end_date (at least 2 date_refs OR date_mode == "range")
        has_start = len(date_refs) >= 1 or date_mode == "range"
        has_end = len(date_refs) >= 2 or date_mode == "range"

        if not has_start:
            return "MISSING_START_DATE"
        if not has_end:
            return "MISSING_END_DATE"

    return None


def decide_booking_status(
    resolved_booking: Dict[str, Any],
    entities: Optional[Dict[str, Any]] = None,
    policy: Optional[Dict[str, bool]] = None,
    intent_name: Optional[str] = None
) -> Tuple[DecisionResult, Dict[str, Any]]:
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
    _ = entities  # unused, kept for signature compatibility

    allow_time_windows = policy.get("allow_time_windows", True)
    allow_constraint_only_time = policy.get("allow_constraint_only_time", True)

    # Extract key fields from resolved_booking (semantic roles only)
    resolved_booking.get("services", [])
    date_mode = resolved_booking.get("date_mode", "none")
    date_refs = resolved_booking.get("date_refs", [])
    time_mode = resolved_booking.get("time_mode", "none")
    time_refs = resolved_booking.get("time_refs", [])
    time_constraint = resolved_booking.get("time_constraint")
    date_range = resolved_booking.get("date_range")
    time_range = resolved_booking.get("time_range")
    booking_mode = resolved_booking.get("booking_mode", "service")

    # MANDATORY: Validate temporal shape completeness BEFORE any RESOLVED decision
    # This is authoritative - config and YAML define what's required
    temporal_shape_reason = _validate_temporal_shape_for_decision(
        intent_name, resolved_booking)
    expected_temporal_shape = INTENT_TEMPORAL_SHAPE.get(
        intent_name) if intent_name else None

    # Fail-fast guardrail: If temporal_shape == datetime_range and missing slots, use specific reason
    if expected_temporal_shape == APPOINTMENT_TEMPORAL_TYPE and temporal_shape_reason:
        # For datetime_range, use "temporal_shape_not_satisfied" as the reason
        decision_reason = "temporal_shape_not_satisfied"
    else:
        decision_reason = temporal_shape_reason

    if temporal_shape_reason:
        # Temporal shape incomplete - force NEEDS_CLARIFICATION
        effective_time = _determine_effective_time(
            time_mode, time_refs, time_constraint
        )
        result = DecisionResult(
            status="NEEDS_CLARIFICATION",
            reason=decision_reason,
            effective_time=effective_time
        )
        # Determine actual temporal shape
        actual_shape = "none"
        if date_refs and date_mode != "none":
            if time_refs and time_mode != "none":
                actual_shape = "datetime_range" if expected_temporal_shape == APPOINTMENT_TEMPORAL_TYPE else "date_range"
            else:
                actual_shape = "date_only"
        elif time_refs and time_mode != "none":
            actual_shape = "time_only"

        # Extract missing slot name from reason
        missing_slot = temporal_shape_reason.lower().replace(
            "missing_", "").replace("_", "_")
        if missing_slot == "time":
            missing_slot = "time"
        elif missing_slot == "date":
            missing_slot = "date"
        elif missing_slot == "start_date":
            missing_slot = "start_date"
        elif missing_slot == "end_date":
            missing_slot = "end_date"

        # Build temporal shape derivation
        temporal_shape_derivation = {
            "date_present": bool(date_refs and date_mode != "none"),
            "time_present": bool(time_refs and time_mode != "none") or (time_constraint is not None),
            "derived_shape": actual_shape
        }

        trace = {
            "decision": {
                "state": result.status,
                "reason": result.reason,
                "expected_temporal_shape": expected_temporal_shape,
                "actual_temporal_shape": actual_shape,
                "missing_slots": [missing_slot] if temporal_shape_reason else [],
                "temporal_shape_satisfied": False,
                "rule_enforced": "temporal_shape_validation",
                "temporal_shape_derivation": temporal_shape_derivation
            }
        }
        return result, trace

    # SYSTEM INVARIANT:
    # A booking with resolved date + time must always be RESOLVED
    # This overrides all other logic paths to prevent regressions
    # NOTE: Temporal shape validation above ensures this only applies to valid shapes
    has_resolved_date = (
        (date_refs and date_mode != "none") or
        (date_range is not None)
    )

    # For reservations, require an explicit end date (date range) or 2+ date refs
    if booking_mode == "reservation":
        has_start = bool(date_range and date_range.get("start_date")) or (
            date_refs and len(date_refs) >= 1)
        has_end = bool(date_range and date_range.get("end_date")
                       ) or (date_refs and len(date_refs) >= 2)
        has_resolved_date = has_start and has_end
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
        result = DecisionResult(
            status="RESOLVED",
            reason=None,
            effective_time=effective_time
        )
        # Build temporal shape derivation
        temporal_shape_derivation = {
            "date_present": has_resolved_date,
            "time_present": has_resolved_time,
            "derived_shape": expected_temporal_shape or "datetime_range"
        }

        trace = {
            "decision": {
                "state": result.status,
                "reason": None,
                "expected_temporal_shape": expected_temporal_shape,
                "actual_temporal_shape": expected_temporal_shape,
                "missing_slots": [],
                "temporal_shape_satisfied": True,
                "rule_enforced": "invariant_date_time_resolved",
                "temporal_shape_derivation": temporal_shape_derivation
            }
        }
        return result, trace

    # NOTE: Reservation temporal shape validation is handled above by _validate_temporal_shape_for_decision
    # No need for duplicate logic here

    # Determine effective_time information
    effective_time = _determine_effective_time(
        time_mode, time_refs, time_constraint
    )

    # Policy checks only (no completeness checks)
    if time_mode == "window" and not allow_time_windows:
        result = DecisionResult(
            status="NEEDS_CLARIFICATION",
            reason="POLICY_TIME_WINDOW",
            effective_time=effective_time
        )
        # Build temporal shape derivation
        temporal_shape_derivation = {
            "date_present": bool(date_refs and date_mode != "none"),
            "time_present": bool(time_refs and time_mode != "none") or (time_constraint is not None),
            "derived_shape": expected_temporal_shape or "datetime_range"
        }
        trace = {
            "decision": {
                "state": result.status,
                "reason": result.reason,
                "expected_temporal_shape": expected_temporal_shape,
                "actual_temporal_shape": expected_temporal_shape,
                "missing_slots": [],
                "temporal_shape_derivation": temporal_shape_derivation
            }
        }
        return result, trace

    # Fuzzy time must clarify for service/appointment; allowed for reservation
    if (
        time_constraint
        and time_constraint.get("mode") == "fuzzy"
        and booking_mode != "reservation"
    ):
        result = DecisionResult(
            status="NEEDS_CLARIFICATION",
            reason="MISSING_TIME_FUZZY",
            effective_time=effective_time
        )
        # Build temporal shape derivation
        temporal_shape_derivation = {
            "date_present": bool(date_refs and date_mode != "none"),
            "time_present": bool(time_refs and time_mode != "none") or (time_constraint is not None),
            "derived_shape": expected_temporal_shape or "datetime_range"
        }
        trace = {
            "decision": {
                "state": result.status,
                "reason": result.reason,
                "expected_temporal_shape": expected_temporal_shape,
                "actual_temporal_shape": expected_temporal_shape,
                "missing_slots": ["time"],
                "temporal_shape_satisfied": False,
                "rule_enforced": "fuzzy_time_policy",
                "temporal_shape_derivation": temporal_shape_derivation
            }
        }
        return result, trace

    if time_constraint and time_mode == "none" and not allow_constraint_only_time:
        result = DecisionResult(
            status="NEEDS_CLARIFICATION",
            reason="POLICY_CONSTRAINT_ONLY_TIME",
            effective_time=effective_time
        )
        # Build temporal shape derivation
        temporal_shape_derivation = {
            "date_present": bool(date_refs and date_mode != "none"),
            "time_present": bool(time_refs and time_mode != "none") or (time_constraint is not None),
            "derived_shape": expected_temporal_shape or "datetime_range"
        }
        trace = {
            "decision": {
                "state": result.status,
                "reason": result.reason,
                "expected_temporal_shape": expected_temporal_shape,
                "actual_temporal_shape": expected_temporal_shape,
                "missing_slots": [],
                "temporal_shape_derivation": temporal_shape_derivation
            }
        }
        return result, trace

    result = DecisionResult(
        status="RESOLVED",
        reason=None,
        effective_time=effective_time
    )
    # Build temporal shape derivation
    temporal_shape_derivation = {
        "date_present": bool(date_refs and date_mode != "none"),
        "time_present": bool(time_refs and time_mode != "none") or (time_constraint is not None),
        "derived_shape": expected_temporal_shape or "datetime_range"
    }
    trace = {
        "decision": {
            "state": result.status,
            "reason": None,
            "expected_temporal_shape": expected_temporal_shape,
            "actual_temporal_shape": expected_temporal_shape,
            "missing_slots": [],
            "temporal_shape_derivation": temporal_shape_derivation
        }
    }
    return result, trace


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
            # Constraints specify exact times (e.g., "by 4pm")
            "mode": "exact",
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
