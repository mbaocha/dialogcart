"""
Readiness Gate

Computes missing slots for an intent based on merged semantic state and service resolution.
This gate runs BEFORE binder invocation to ensure READY responses are only built when all required slots are present.
"""
from typing import Dict, Any, List, Optional
from ..config.intent_meta import get_intent_registry
from ..config.temporal import APPOINTMENT_TEMPORAL_TYPE, RESERVATION_TEMPORAL_TYPE, TimeMode, ALLOW_BARE_WEEKDAY_RANGE_BINDING
from .invariants import _slot_present, _validate_temporal_shape, _is_bare_weekday_range


def compute_missing_slots(
    intent_name: str,
    merged_state: Dict[str, Any],
    service_resolution: Dict[str, Any],
    tenant_context: Optional[Dict[str, Any]] = None
) -> List[str]:
    """
    Compute missing slots for an intent based on merged semantic state and service resolution.
    
    This is the readiness gate - it determines if a request is ready for binder invocation.
    
    Args:
        intent_name: Intent name (e.g., "CREATE_APPOINTMENT", "CREATE_RESERVATION")
        merged_state: Merged semantic booking state (current turn + memory)
        service_resolution: Service resolution result with resolved_tenant_service_id
        tenant_context: Optional tenant context with aliases
        
    Returns:
        List of missing slot keys (e.g., ["service_id", "date", "time"])
        Empty list if all required slots are present (ready for binder)
    """
    missing: List[str] = []
    
    # Get intent metadata
    registry = get_intent_registry()
    intent_meta = registry.get(intent_name) if intent_name else None
    if not intent_meta:
        # No metadata - cannot validate, assume ready
        return []
    
    # Check service_id (must be resolved to tenant alias key)
    resolved_tenant_service_id = service_resolution.get("resolved_tenant_service_id")
    service_resolution_reason = service_resolution.get("clarification_reason")
    
    # Service must be resolved (tenant alias key exists)
    if not resolved_tenant_service_id:
        # Check if service resolution failed for a specific reason
        if service_resolution_reason == "MULTIPLE_MATCHES":
            # Service ambiguity - don't mark as missing, will be handled separately
            pass
        elif service_resolution_reason == "UNSUPPORTED_SERVICE":
            # Service unresolved - mark as missing
            missing.append("service_id")
        else:
            # No service resolved - check if services exist in semantic state
            if not _slot_present("service_id", merged_state):
                missing.append("service_id")
    
    # Validate service_id is tenant alias key (not canonical)
    # CRITICAL: This prevents canonical IDs from leaking into responses
    # aliases structure: {tenant_alias_key: canonical_family}
    # Example: {"suite": "room", "delux": "room"} means "suite" and "delux" are tenant alias keys, "room" is canonical
    if resolved_tenant_service_id and tenant_context:
        aliases = tenant_context.get("aliases", {})
        if isinstance(aliases, dict) and aliases:
            # Check if resolved_tenant_service_id is a tenant alias key (key in aliases dict)
            if resolved_tenant_service_id not in aliases:
                # Check if it's a canonical value (value in aliases dict)
                is_canonical_value = False
                for alias_key, canonical_value in aliases.items():
                    # Direct match with canonical family
                    if canonical_value == resolved_tenant_service_id:
                        is_canonical_value = True
                        break
                    # Match with full canonical ID (e.g., "hospitality.room" ends with ".room")
                    if isinstance(resolved_tenant_service_id, str) and isinstance(canonical_value, str):
                        if "." in resolved_tenant_service_id and resolved_tenant_service_id.endswith(f".{canonical_value}"):
                            is_canonical_value = True
                            break
                
                if is_canonical_value:
                    # Service ID is canonical, not tenant alias key - mark as missing
                    # This will prevent READY response and trigger NEEDS_CLARIFICATION
                    missing.append("service_id")
    
    # Validate temporal shape based on intent
    temporal_shape = intent_meta.temporal_shape
    if temporal_shape == APPOINTMENT_TEMPORAL_TYPE:
        # CREATE_APPOINTMENT requires date + time (or datetime_range)
        
        # Check date
        if not _slot_present("date", merged_state):
            missing.append("date")
        
        # Check time
        if not _slot_present("time", merged_state):
            missing.append("time")
        
        # Additional validation for unsupported binding patterns
        temporal_issues = _validate_temporal_shape(temporal_shape, merged_state, intent_meta)
        for slot in temporal_issues.keys():
            if slot not in missing:
                missing.append(slot)
    
    elif temporal_shape == RESERVATION_TEMPORAL_TYPE:
        # CREATE_RESERVATION requires start_date + end_date (or date_range)
        
        # Check start_date
        if not _slot_present("start_date", merged_state):
            missing.append("start_date")
        
        # Check end_date (if required)
        if intent_meta.requires_end_date:
            if not _slot_present("end_date", merged_state):
                missing.append("end_date")
        
        # Additional validation for unsupported binding patterns
        temporal_issues = _validate_temporal_shape(temporal_shape, merged_state, intent_meta)
        for slot in temporal_issues.keys():
            if slot not in missing:
                missing.append(slot)
    
    return missing

