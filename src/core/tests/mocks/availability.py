"""
Mock Service Availability Endpoint

Mocks GET /api/internal/availability/services for SEARCH_AVAILABILITY execution.

Response format matches real API:
{
  "slots": [
    {
      "start": "2026-01-16T10:00:00Z",
      "end": "2026-01-16T11:00:00Z",
      "available": true
    }
  ]
}
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Default service duration in minutes
DEFAULT_SERVICE_DURATION_MINUTES = 60


def mock_get_service_availability(
    organization_id: int,
    service_id: int,
    date: str,
    time_constraint: Optional[Dict[str, Any]] = None,
    extra_params: Optional[Dict[str, Any]] = None,
    **kwargs,
) -> Dict[str, Any]:
    """
    Mock GET /api/internal/availability/services endpoint.

    Generates deterministic availability slots based on:
    - date: ISO date string (e.g., "2026-01-16")
    - time_constraint: Optional time constraint dict with "start" and "end" keys
      (can be passed via extra_params or directly)

    Rules:
    - Start time must align with time_constraint.start if provided
    - End time = start + service duration (default 60 minutes)
    - Deterministic values only (no now(), no randomness)

    Args:
        organization_id: Organization identifier (unused in mock)
        service_id: Service identifier (unused in mock)
        date: ISO date string (e.g., "2026-01-16")
        time_constraint: Optional dict with "start" and "end" time strings (HH:MM format)
        extra_params: Optional dict that may contain time_constraint
        **kwargs: Additional parameters (may contain time_constraint)

    Returns:
        Mock availability response with slots array
    """
    # Extract time_constraint from various sources (priority: direct param > extra_params > kwargs)
    effective_time_constraint = time_constraint
    if not effective_time_constraint and extra_params:
        effective_time_constraint = extra_params.get("time_constraint")
    if not effective_time_constraint:
        effective_time_constraint = kwargs.get("time_constraint")

    # Determine start time from time_constraint or use default
    if effective_time_constraint and effective_time_constraint.get("start"):
        start_time_str = effective_time_constraint[
            "start"
        ]  # Format: "HH:MM" (e.g., "10:00")
        # Parse HH:MM format
        hour, minute = map(int, start_time_str.split(":"))
    else:
        # Default to 10:00 if no time constraint
        hour, minute = 10, 0

    # Parse date and create datetime
    try:
        date_obj = datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        # Fallback: try other date formats
        try:
            date_obj = datetime.strptime(date, "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            logger.warning(f"Could not parse date '{date}', using default")
            date_obj = datetime(2026, 1, 16)

    # Create start datetime
    start_datetime = date_obj.replace(hour=hour, minute=minute, second=0, microsecond=0)

    # Calculate end time (start + duration)
    duration_minutes = DEFAULT_SERVICE_DURATION_MINUTES
    end_datetime = start_datetime + timedelta(minutes=duration_minutes)

    # Format as ISO-8601 with timezone
    start_iso = start_datetime.strftime("%Y-%m-%dT%H:%M:%SZ")
    end_iso = end_datetime.strftime("%Y-%m-%dT%H:%M:%SZ")

    logger.debug(
        f"[MOCK] Service availability: date={date}, start={start_iso}, end={end_iso}"
    )

    return {"slots": [{"start": start_iso, "end": end_iso, "available": True}]}


def mock_get_reservation_availability(
    organization_id: int,
    start_date: str,
    end_date: str,
    extra_params: Optional[Dict[str, Any]] = None,
    **kwargs,
) -> Dict[str, Any]:
    """
    Mock reservation availability for CREATE_RESERVATION SEARCH_AVAILABILITY.

    Returns the same slots shape as mock_get_service_availability.
    """
    del organization_id, extra_params, kwargs

    try:
        start_dt = datetime.strptime(str(start_date).split("T")[0], "%Y-%m-%d")
        end_dt = datetime.strptime(str(end_date).split("T")[0], "%Y-%m-%d")
    except ValueError:
        start_dt = datetime(2026, 3, 5)
        end_dt = datetime(2026, 3, 8)

    check_in = start_dt.replace(hour=15, minute=0, second=0, microsecond=0)
    check_out = (end_dt + timedelta(days=1)).replace(
        hour=11, minute=0, second=0, microsecond=0
    )
    start_iso = check_in.strftime("%Y-%m-%dT%H:%M:%SZ")
    end_iso = check_out.strftime("%Y-%m-%dT%H:%M:%SZ")

    logger.debug(
        f"[MOCK] Reservation availability: start={start_date}, end={end_date}, "
        f"slot={start_iso}..{end_iso}"
    )

    return {"slots": [{"start": start_iso, "end": end_iso, "available": True}]}
