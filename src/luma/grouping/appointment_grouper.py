"""
Appointment/Reservation Booking Grouper

Simple grouping logic for service-based appointment/reservation booking.
Replaces legacy e-commerce grouping with single-intent booking logic.

Assumptions:
- Exactly ONE intent per request: BOOK_APPOINTMENT
- Multiple services can belong to one booking
- No quantities, units, brands, variants
- No cart, no mutation, no sequencing
- No ML-based intent mapping
- No token indexing or reverse mapping
"""
from typing import Dict, Any, Optional

from luma.structure.structure_types import StructureResult
from .time_constraints import resolve_time_constraint


# Default intent for all appointment/reservation requests
BOOK_APPOINTMENT_INTENT = "BOOK_APPOINTMENT"

# Status values
STATUS_OK = "OK"
STATUS_NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"


def group_appointment(
    entities: Dict[str, Any],
    structure: StructureResult
) -> Dict[str, Any]:
    """
    Group extracted entities into a single appointment booking.

    This function takes entities from EntityMatcher and structure from
    structural interpretation, and produces a single booking intent.

    Args:
        entities: Entity extraction result from EntityMatcher.extract_with_parameterization()
                 Contains: service_families, dates, dates_absolute, times,
                          time_windows, durations
        structure: Structural interpretation result from interpret_structure()
                  Contains: booking_count, service_scope, time_scope, date_scope,
                          time_type, has_duration, needs_clarification

    Returns:
        Dictionary with:
        {
            "intent": "BOOK_APPOINTMENT",
            "booking": {
                "services": [...],  # List of service dicts
                "date_ref": "...",   # Date reference (relative or absolute)
                "time_ref": "...",   # Time reference (exact, window, or range)
                "duration": null      # Duration dict or null
            },
            "structure": {...},      # Structure dict (from structure.to_dict())
            "status": "OK" | "NEEDS_CLARIFICATION",
            "reason": null | str      # Reason if status is NEEDS_CLARIFICATION
        }
    """
    # Rule 1: Check if clarification is needed
    if structure.needs_clarification:
        return {
            "intent": BOOK_APPOINTMENT_INTENT,
            "booking": _build_booking_dict(entities, structure),
            "structure": structure.to_dict()["structure"],
            "status": STATUS_NEEDS_CLARIFICATION,
            "reason": _determine_clarification_reason(entities, structure)
        }

    # Rule 2: Build normal booking
    return {
        "intent": BOOK_APPOINTMENT_INTENT,
        "booking": _build_booking_dict(entities, structure),
        "structure": structure.to_dict()["structure"],
        "status": STATUS_OK,
        "reason": None
    }


def _build_booking_dict(
    entities: Dict[str, Any],
    structure: StructureResult
) -> Dict[str, Any]:
    """
    Build the booking dictionary from entities and structure.

    Args:
        entities: Entity extraction result
        structure: Structural interpretation result

    Returns:
        Booking dictionary with services, date_ref, time_ref, duration
    """
    # Extract services
    services = entities.get("business_categories") or entities.get(
        "service_families", [])

    # Extract date reference (prefer absolute over relative)
    date_ref = _extract_date_reference(entities)

    # Extract time constraint based on time_type
    time_constraint = _extract_time_constraint(entities, structure)

    # Extract duration (if present)
    duration = _extract_duration(entities)

    return {
        "services": services,
        "date_ref": date_ref,
        "time_ref": None,  # deprecated; no fallback if time_constraint is None
        "time_constraint": time_constraint,
        "duration": duration
    }


def _extract_date_reference(entities: Dict[str, Any]) -> Optional[str]:
    """
    Extract date reference from entities.

    Prefers absolute dates over relative dates.
    If multiple dates exist, returns the first one.

    Args:
        entities: Entity extraction result

    Returns:
        Date reference string or None
    """
    # Prefer absolute dates
    dates_absolute = entities.get("dates_absolute", [])
    if dates_absolute:
        return dates_absolute[0].get("text")

    # Fallback to relative dates
    dates = entities.get("dates", [])
    if dates:
        return dates[0].get("text")

    return None


def _extract_time_constraint(
    entities: Dict[str, Any],
    structure: StructureResult
) -> Optional[Dict[str, Any]]:
    """Delegate time normalization to canonical resolver."""
    return resolve_time_constraint(
        entities.get("times", []) or [],
        entities.get("time_windows", []) or [],
        structure.time_type
    )


def _extract_duration(entities: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Extract duration from entities.

    Args:
        entities: Entity extraction result

    Returns:
        Duration dict or None
    """
    durations = entities.get("durations", [])
    if durations:
        return durations[0]  # Return first duration

    return None


def _determine_clarification_reason(
    entities: Dict[str, Any],
    structure: StructureResult
) -> Optional[str]:
    """
    Determine reason for clarification needed.

    Args:
        entities: Entity extraction result
        structure: Structural interpretation result

    Returns:
        Reason string or None
    """
    reasons = []

    # Check for multiple dates without range marker
    dates_count = len(entities.get("dates", []))
    dates_abs_count = len(entities.get("dates_absolute", []))
    total_dates = dates_count + dates_abs_count

    if total_dates > 1:
        reasons.append(f"Multiple dates ({total_dates}) without range marker")

    # Check for multiple times without range marker
    times_count = len(entities.get("times", []))
    if times_count > 1 and structure.time_type != "range":
        reasons.append(f"Multiple times ({times_count}) without range marker")

    # Check for conflicting scopes
    if structure.service_scope == "separate" and structure.time_scope == "shared":
        reasons.append(
            "Conflicting scopes: separate services with shared time")

    if reasons:
        return "; ".join(reasons)

    return "Unspecified clarification needed"
