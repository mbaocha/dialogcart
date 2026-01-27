"""
Execution Dispatcher

Minimal execution dispatcher that routes planning results to appropriate execution handlers.
Currently supports only SEARCH_AVAILABILITY actions.
"""

import logging
from typing import Dict, Any, Optional, List, Tuple

logger = logging.getLogger(__name__)


def execute(
    plan: Dict[str, Any],
    availability_client: Any
) -> Dict[str, Any]:
    """
    Execute a planning result using injected clients.
    
    Routes ONLY SEARCH_AVAILABILITY actions. Other actions are not supported.
    
    Args:
        plan: Planning result from plan_message() containing:
            - action: Action to execute (must be "SEARCH_AVAILABILITY")
            - slots: Collected slots dictionary
            - intent_name: Intent name (e.g., "CREATE_APPOINTMENT", "CREATE_RESERVATION")
            - time_constraint: Optional time constraint (if present)
        availability_client: Injected availability client instance with methods:
            - get_service_availability(organization_id, service_id, date, extra_params)
            - get_reservation_availability(organization_id, start_date, end_date, extra_params)
    
    Returns:
        Execution result dictionary with normalized structure:
        {
            "type": "availability",
            "status": "success" | "error",
            "slots": [
                {
                    "starts_at": "ISO datetime string",
                    "ends_at": "ISO datetime string"
                }
            ]
        }
    
    Raises:
        ValueError: If action is not SEARCH_AVAILABILITY or required slots are missing
        AttributeError: If availability_client doesn't have required methods
    """
    action = plan.get("action")
    
    # Only route SEARCH_AVAILABILITY actions
    if action != "SEARCH_AVAILABILITY":
        raise ValueError(
            f"Unsupported action: {action}. Only SEARCH_AVAILABILITY is supported."
        )
    
    slots = plan.get("slots", {})
    intent_name = plan.get("intent_name", "")
    time_constraint = plan.get("time_constraint")
    
    # Extract organization_id (required for all availability calls)
    organization_id = slots.get("organization_id")
    if not organization_id:
        raise ValueError("organization_id is required in slots for availability search")
    
    # Route based on intent to determine service vs reservation
    if intent_name == "CREATE_APPOINTMENT":
        # Service availability search
        return _execute_service_availability(
            organization_id=organization_id,
            slots=slots,
            time_constraint=time_constraint,
            availability_client=availability_client
        )
    elif intent_name == "CREATE_RESERVATION":
        # Reservation availability search
        return _execute_reservation_availability(
            organization_id=organization_id,
            slots=slots,
            time_constraint=time_constraint,
            availability_client=availability_client
        )
    else:
        # Default to service availability for unknown intents
        logger.warning(
            f"Unknown intent '{intent_name}', defaulting to service availability search"
        )
        return _execute_service_availability(
            organization_id=organization_id,
            slots=slots,
            time_constraint=time_constraint,
            availability_client=availability_client
        )


def _execute_service_availability(
    organization_id: int,
    slots: Dict[str, Any],
    time_constraint: Optional[Dict[str, Any]],
    availability_client: Any
) -> Dict[str, Any]:
    """
    Execute service availability search.
    
    Args:
        organization_id: Organization ID
        slots: Slots dictionary (must include service_id and date)
        time_constraint: Optional time constraint
        availability_client: Availability client instance
    
    Returns:
        Normalized execution result
    """
    # Extract required fields
    service_id = slots.get("service_id")
    if not service_id:
        raise ValueError("service_id is required in slots for service availability search")
    
    # Extract date (can be date, start_date, or from date_range/datetime_range)
    date = _extract_date_from_slots(slots)
    if not date:
        raise ValueError("date is required in slots for service availability search")
    
    # Build extra_params from time_constraint if present
    extra_params: Optional[Dict[str, Any]] = None
    if time_constraint:
        extra_params = {"time_constraint": time_constraint}
    
    # Call availability client
    try:
        response = availability_client.get_service_availability(
            organization_id=organization_id,
            service_id=service_id,
            date=date,
            extra_params=extra_params
        )
    except AttributeError as e:
        raise AttributeError(
            f"availability_client must have get_service_availability method: {e}"
        ) from e
    
    # Normalize response
    return _normalize_availability_response(response)


def _execute_reservation_availability(
    organization_id: int,
    slots: Dict[str, Any],
    time_constraint: Optional[Dict[str, Any]],
    availability_client: Any
) -> Dict[str, Any]:
    """
    Execute reservation availability search.
    
    Args:
        organization_id: Organization ID
        slots: Slots dictionary (must include start_date and end_date or date_range)
        time_constraint: Optional time constraint
        availability_client: Availability client instance
    
    Returns:
        Normalized execution result
    """
    # Extract date range (can be start_date/end_date or date_range)
    start_date, end_date = _extract_date_range_from_slots(slots)
    if not start_date or not end_date:
        raise ValueError(
            "start_date and end_date (or date_range) are required in slots "
            "for reservation availability search"
        )
    
    # Build extra_params from time_constraint if present
    extra_params: Optional[Dict[str, Any]] = None
    if time_constraint:
        extra_params = {"time_constraint": time_constraint}
    
    # Call availability client
    try:
        response = availability_client.get_reservation_availability(
            organization_id=organization_id,
            start_date=start_date,
            end_date=end_date,
            extra_params=extra_params
        )
    except AttributeError as e:
        raise AttributeError(
            f"availability_client must have get_reservation_availability method: {e}"
        ) from e
    
    # Normalize response
    return _normalize_availability_response(response)


def _extract_date_from_slots(slots: Dict[str, Any]) -> Optional[str]:
    """
    Extract date string from slots.
    
    Checks multiple possible locations:
    1. slots.date
    2. slots.start_date
    3. slots.date_range.start
    4. slots.datetime_range.start (date part)
    
    Returns:
        Date string in YYYY-MM-DD format, or None if not found
    """
    # Direct date field
    if slots.get("date"):
        return str(slots["date"])
    
    # start_date field
    if slots.get("start_date"):
        return str(slots["start_date"])
    
    # date_range.start
    date_range = slots.get("date_range")
    if isinstance(date_range, dict):
        start = date_range.get("start") or date_range.get("start_date")
        if start:
            # Extract date part if it's a datetime string
            date_str = str(start).split("T")[0].split(" ")[0]
            return date_str
    
    # datetime_range.start (extract date part)
    datetime_range = slots.get("datetime_range")
    if isinstance(datetime_range, dict):
        start = datetime_range.get("start")
        if start:
            # Extract date part if it's a datetime string
            date_str = str(start).split("T")[0].split(" ")[0]
            return date_str
    
    return None


def _extract_date_range_from_slots(slots: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """
    Extract start_date and end_date from slots.
    
    Checks multiple possible locations:
    1. slots.start_date and slots.end_date
    2. slots.date_range.start and slots.date_range.end
    3. slots.datetime_range.start and slots.datetime_range.end
    
    Returns:
        Tuple of (start_date, end_date) as strings in YYYY-MM-DD format
    """
    start_date = None
    end_date = None
    
    # Direct start_date/end_date fields
    if slots.get("start_date"):
        start_date = str(slots["start_date"])
    if slots.get("end_date"):
        end_date = str(slots["end_date"])
    
    # date_range
    date_range = slots.get("date_range")
    if isinstance(date_range, dict):
        if not start_date:
            start = date_range.get("start") or date_range.get("start_date")
            if start:
                start_date = str(start).split("T")[0].split(" ")[0]
        if not end_date:
            end = date_range.get("end") or date_range.get("end_date")
            if end:
                end_date = str(end).split("T")[0].split(" ")[0]
    
    # datetime_range
    datetime_range = slots.get("datetime_range")
    if isinstance(datetime_range, dict):
        if not start_date:
            start = datetime_range.get("start")
            if start:
                start_date = str(start).split("T")[0].split(" ")[0]
        if not end_date:
            end = datetime_range.get("end")
            if end:
                end_date = str(end).split("T")[0].split(" ")[0]
    
    return (start_date, end_date)


def _normalize_availability_response(response: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize availability client response to standard format.
    
    Input format (from availability client):
    {
        "slots": [
            {
                "start": "ISO datetime string",
                "end": "ISO datetime string",
                "staff_id": 5,  # optional
                ...  # other fields preserved
            }
        ]
    }
    
    Output format:
    {
        "type": "availability",
        "status": "success",
        "slots": [
            {
                "starts_at": "ISO datetime string",
                "ends_at": "ISO datetime string"
            }
        ]
    }
    
    Args:
        response: Raw response from availability client
    
    Returns:
        Normalized execution result
    """
    if not isinstance(response, dict):
        raise ValueError(f"Expected dict response from availability client, got {type(response)}")
    
    # Extract slots from response
    raw_slots = response.get("slots", [])
    if not isinstance(raw_slots, list):
        raw_slots = []
    
    # Normalize each slot
    normalized_slots: List[Dict[str, str]] = []
    for slot in raw_slots:
        if not isinstance(slot, dict):
            continue
        
        # Extract start/end times (handle both "start"/"end" and "starts_at"/"ends_at")
        starts_at = slot.get("starts_at") or slot.get("start")
        ends_at = slot.get("ends_at") or slot.get("end")
        
        if not starts_at or not ends_at:
            logger.warning(f"Skipping slot missing start/end times: {slot}")
            continue
        
        normalized_slot = {
            "starts_at": str(starts_at),
            "ends_at": str(ends_at)
        }
        normalized_slots.append(normalized_slot)
    
    return {
        "type": "availability",
        "status": "success",
        "slots": normalized_slots
    }

