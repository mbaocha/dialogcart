"""
Centralized missing slot derivation logic.

This module contains all logic for deriving/computing missing slots from various
inputs (decision reasons, temporal shapes, resolved booking state, etc.).

All missing-slot computation should go through functions in this module to ensure
consistency and maintainability.
"""
from typing import Dict, Any, List, Optional
from ..config.temporal import APPOINTMENT_TEMPORAL_TYPE
from ..config.intent_meta import validate_required_slots


def derive_missing_slot_from_reason(temporal_shape_reason: Optional[str]) -> List[str]:
    """
    Derive missing slot name from temporal shape reason code.
    
    This is used in the decision layer to convert reason codes like "MISSING_TIME"
    to slot names like ["time"].
    
    Args:
        temporal_shape_reason: Reason code from temporal shape validation
                             (e.g., "MISSING_TIME", "MISSING_DATE", "MISSING_START_DATE", "MISSING_END_DATE")
    
    Returns:
        List of missing slot names. Empty list if no reason provided.
    
    Examples:
        >>> derive_missing_slot_from_reason("MISSING_TIME")
        ['time']
        >>> derive_missing_slot_from_reason("MISSING_START_DATE")
        ['start_date']
        >>> derive_missing_slot_from_reason(None)
        []
    """
    if not temporal_shape_reason:
        return []
    
    # Extract missing slot name from reason
    # Convert "MISSING_TIME" -> "time", "MISSING_START_DATE" -> "start_date", etc.
    missing_slot = temporal_shape_reason.lower().replace("missing_", "").replace("_", "_")
    
    # Explicit mapping to ensure correct slot names
    if missing_slot == "time":
        missing_slot = "time"
    elif missing_slot == "date":
        missing_slot = "date"
    elif missing_slot == "start_date":
        missing_slot = "start_date"
    elif missing_slot == "end_date":
        missing_slot = "end_date"
    
    return [missing_slot] if missing_slot else []


def compute_temporal_shape_missing_slots(
    intent_name: str,
    calendar_booking: Dict[str, Any],
    merged_semantic_result: Optional[Any],
    temporal_shape: Optional[str]
) -> List[str]:
    """
    Compute missing slots based on temporal shape requirements.
    
    This checks if the temporal shape (e.g., datetime_range for appointments) is satisfied
    and returns missing slots if not.
    
    Args:
        intent_name: Intent name (e.g., "CREATE_APPOINTMENT")
        calendar_booking: Calendar binding result dictionary
        merged_semantic_result: Merged semantic resolution result (optional)
        temporal_shape: Expected temporal shape (APPOINTMENT_TEMPORAL_TYPE or RESERVATION_TEMPORAL_TYPE)
    
    Returns:
        List of missing slot names. Empty list if temporal shape is satisfied.
    
    This is used in resolve_service.py for post-binding temporal shape enforcement.
    """
    if not temporal_shape:
        return []
    
    if temporal_shape == APPOINTMENT_TEMPORAL_TYPE:
        # Check if datetime_range is present (required for appointments)
        has_dtr = bool((calendar_booking or {}).get("datetime_range"))
        if not has_dtr:
            temporal_missing: List[str] = []
            # Get date_refs from semantic result
            date_refs = merged_semantic_result.resolved_booking.get(
                "date_refs") if merged_semantic_result else []
            if date_refs:
                temporal_missing = ["time"]
            else:
                temporal_missing = ["date", "time"]
            return temporal_missing
    
    # For other temporal shapes or if datetime_range is present, no missing slots
    return []


def compute_missing_slots_for_intent(
    intent_name: str,
    resolved_slots: Dict[str, Any],
    entities: Dict[str, Any],
    extraction_result: Optional[Dict[str, Any]] = None,
    merged_semantic_result: Optional[Any] = None,
    intent_name_for_filter: Optional[str] = None
) -> List[str]:
    """
    Compute missing slots for an intent using required_slots validation.
    
    This wraps validate_required_slots() and adds special-case filtering logic
    for CREATE_APPOINTMENT where extracted services should not count as missing service_id,
    and for MODIFY_BOOKING where "delta" placeholder is normalized to a readable missing slot.
    
    Args:
        intent_name: Intent name (e.g., "CREATE_APPOINTMENT")
        resolved_slots: Resolved booking slots dictionary
        entities: Raw extraction entities
        extraction_result: Optional extraction result for service filtering
        merged_semantic_result: Optional merged semantic result for service filtering
        intent_name_for_filter: Optional intent name for filtering (defaults to intent_name)
    
    Returns:
        List of missing slot names.
    
    This preserves the special-case logic from resolve_service.py where CREATE_APPOINTMENT
    with extracted services should not have service_id in missing slots.
    """
    # Use the centralized validate_required_slots function
    enforced_missing = validate_required_slots(
        intent_name, resolved_slots, entities
    )
    
    # MODIFY_BOOKING delta semantics: booking_id mandatory, at least one delta slot required
    # READY when: booking_id exists AND at least one delta slot exists
    # All slots except booking_id are optional deltas (presence = replace, absence = leave unchanged)
    # Exception: For reservation date-range modifications, if start_date is present, end_date is required
    if intent_name == "MODIFY_BOOKING":
        booking_id_present = bool(entities.get("booking_id"))
        
        if booking_id_present:
            # Check if at least one delta slot is present
            # Delta slots: date, time, service_id, duration, date_range, datetime_range, start_date, end_date
            # Must check both resolved_slots (processed/bound) and entities (raw extracted) for delta presence
            has_delta = False
            
            # Check date-related deltas in resolved_slots
            has_start_date = bool(resolved_slots.get("start_date"))
            has_end_date = bool(resolved_slots.get("end_date"))
            has_date_range = bool(resolved_slots.get("date_range"))
            has_date_refs = bool(resolved_slots.get("date_refs"))
            
            # Also check entities for raw date extraction (dates, dates_absolute)
            has_date_in_entities = bool(entities.get("dates") or entities.get("dates_absolute"))
            
            has_date_delta = has_start_date or has_end_date or has_date_range or has_date_refs or has_date_in_entities
            if has_date_delta:
                has_delta = True
                
                # For reservation date-range modifications: if start_date is present, end_date is required
                # (full date-range modification is expected for reservations, not partial)
                # Note: This validation only applies when start_date is present without date_range
                # If date_range is present, it already contains both dates, so no need to check
                if has_start_date and not has_end_date and not has_date_range:
                    # start_date present but end_date missing - add end_date to missing
                    # This handles the "partial modification" case for reservations
                    # Don't return early - continue checking for other deltas (time, service_id, etc.)
                    # If other deltas are present, the request might still be READY
                    if "end_date" not in enforced_missing:
                        enforced_missing.append("end_date")
            
            # Check time-related deltas in resolved_slots
            has_time_in_resolved = bool(
                resolved_slots.get("time_refs") or 
                resolved_slots.get("time_constraint") or 
                resolved_slots.get("time_range") or 
                resolved_slots.get("datetime_range")
            )
            
            # Also check entities for raw time extraction (times, time_windows, durations)
            has_time_in_entities = bool(
                entities.get("times") or 
                entities.get("time_windows") or 
                entities.get("durations")
            )
            
            if has_time_in_resolved or has_time_in_entities:
                has_delta = True
            
            # Check service_id delta (check both resolved_slots and entities)
            if (resolved_slots.get("service_id") or 
                resolved_slots.get("services") or
                entities.get("service_id") or
                entities.get("business_categories") or
                entities.get("service_families")):
                has_delta = True
            
            # Check duration delta (check both resolved_slots and entities)
            if (resolved_slots.get("duration") or
                entities.get("durations")):
                has_delta = True
            
            # If booking_id is present but no deltas, we need at least one delta
            # Since we can't specify which delta is needed (all are optional),
            # we'll add "change" as a placeholder that indicates a change is needed
            # This will cause NEEDS_CLARIFICATION
            if not has_delta:
                # Remove booking_id from missing (it's present)
                enforced_missing = [slot for slot in enforced_missing if slot != "booking_id"]
                # Add "change" as a placeholder - indicates at least one delta slot is required
                # The presence of "change" means "what should be modified?"
                enforced_missing.append("change")
    
    # INVARIANT: For CREATE_APPOINTMENT, if extraction produced services, service_id is NEVER missing
    # Filter service_id from missing slots and prevent MISSING_SERVICE clarification_reason
    # Track if services were extracted (for CREATE_APPOINTMENT)
    filter_intent = intent_name_for_filter or intent_name
    if filter_intent == "CREATE_APPOINTMENT" and enforced_missing:
        extracted_services = None
        # Check if services were extracted (from extraction or semantic result)
        # First check extraction_result (most direct - what was actually extracted)
        if extraction_result:
            business_categories = extraction_result.get("business_categories", [])
            if business_categories:
                extracted_services = business_categories
        # Fallback to semantic result if extraction_result doesn't have services
        if not extracted_services:
            if merged_semantic_result and merged_semantic_result.resolved_booking:
                extracted_services = merged_semantic_result.resolved_booking.get("services")
            elif resolved_slots.get("services"):
                extracted_services = resolved_slots.get("services")
        
        # If services were extracted (ALIAS or FAMILY), remove service_id from missing slots
        if extracted_services:
            enforced_missing = [slot for slot in enforced_missing
                              if slot not in ("service_id", "service")]
    
    return enforced_missing

