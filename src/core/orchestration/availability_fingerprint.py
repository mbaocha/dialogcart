"""
Slot fingerprint utilities for availability resolution.

This module provides functions to generate deterministic fingerprints from slots
that affect availability. The fingerprint scope is conditional on slot completeness:
- If time is NOT present → fingerprint = { organization_id, service_id, date }
- If time IS present → fingerprint = { organization_id, service_id, date, time }

This ensures availability is re-checked when time is introduced, as availability
for {service_id, date} may differ from {service_id, date, time}.
"""
import hashlib
import json
from typing import Dict, Any, Optional


def _extract_normalized_slots(slots: Dict[str, Any]) -> tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    """
    Extract and normalize slots for fingerprint computation.
    
    Returns:
        Tuple of (organization_id, service_id, date, time) as normalized strings
    """
    # Extract organization_id
    organization_id = slots.get("organization_id")
    normalized_org = str(organization_id).lower().strip() if organization_id else None
    
    # Extract service_id
    service_id = slots.get("service_id")
    if not service_id:
        return (normalized_org, None, None, None)
    normalized_service = str(service_id).lower().strip()
    
    # Extract date from various possible locations
    date = slots.get("date") or slots.get("start_date")
    if not date:
        # Try date_range
        date_range = slots.get("date_range")
        if isinstance(date_range, dict):
            date = date_range.get("start") or date_range.get("start_date")
        # Try datetime_range
        if not date:
            datetime_range = slots.get("datetime_range")
            if isinstance(datetime_range, dict):
                start = datetime_range.get("start")
                if start:
                    # Extract date portion from datetime string
                    date = str(start).split("T")[0].split(" ")[0]
    
    # Normalize date (convert to string, lowercase, strip)
    normalized_date = str(date).lower().strip() if date else None
    
    # Extract time
    time = slots.get("time")
    # Normalize time (convert to string, lowercase, strip)
    normalized_time = str(time).lower().strip() if time else None
    
    return (normalized_org, normalized_service, normalized_date, normalized_time)


def compute_availability_fingerprint(slots: Dict[str, Any], intent_name: Optional[str] = None) -> Optional[str]:
    """
    Compute a deterministic fingerprint from slots that affect availability.
    
    The fingerprint scope is conditional on slot completeness:
    - If time is NOT present → fingerprint = { organization_id, service_id, date }
    - If time IS present → fingerprint = { organization_id, service_id, date, time }
    
    This ensures availability is re-checked when time is introduced, as availability
    for {service_id, date} may differ from {service_id, date, time}.
    
    Args:
        slots: Dictionary of slot values
        intent_name: Optional intent name (kept for backward compatibility, no longer used)
        
    Returns:
        A deterministic hash string, or None if service_id is missing
    """
    normalized_org, normalized_service, normalized_date, normalized_time = _extract_normalized_slots(slots)
    
    if not normalized_service:
        return None
    
    # Base fingerprint always includes organization_id, service_id, and date
    fingerprint_dict = {
        "organization_id": normalized_org,
        "service_id": normalized_service,
        "date": normalized_date
    }
    
    # IMPORTANT: Escalate scope when time is present
    # This ensures availability is re-checked when time is introduced
    # {service_id, date} ≠ {service_id, date, time}
    if normalized_time:
        fingerprint_dict["time"] = normalized_time
    
    # Create deterministic hash from sorted JSON representation
    # Sort keys to ensure consistent ordering
    fingerprint_json = json.dumps(fingerprint_dict, sort_keys=True, ensure_ascii=False)
    
    # Generate SHA256 hash
    fingerprint_hash = hashlib.sha256(fingerprint_json.encode('utf-8')).hexdigest()
    
    return fingerprint_hash


def slots_match_availability_fingerprint(
    slots: Dict[str, Any],
    stored_fingerprint: Optional[str],
    intent_name: Optional[str] = None
) -> bool:
    """
    Check if current slots match a stored availability fingerprint.
    
    Args:
        slots: Current slot values
        stored_fingerprint: Previously stored fingerprint (from session)
        intent_name: Optional intent name to determine fingerprint level
        
    Returns:
        True if fingerprints match (availability is resolved for these slots),
        False otherwise
    """
    if not stored_fingerprint:
        # No stored fingerprint means availability was never checked
        return False
    
    current_fingerprint = compute_availability_fingerprint(slots, intent_name)
    if not current_fingerprint:
        # Cannot compute fingerprint (missing service_id) - not resolved
        return False
    
    return current_fingerprint == stored_fingerprint

