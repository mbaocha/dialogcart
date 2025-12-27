"""
Decision / Policy Layer

Pure function that decides whether a booking is RESOLVED or NEEDS_CLARIFICATION
based on the semantic dictionary (resolved_booking) and configurable policy.

Policy operates ONLY on semantic roles (time_mode, time_constraint, etc.),
never on raw text or regex patterns.
"""

from dataclasses import dataclass
from typing import Dict, Any, Optional, Literal, Tuple, List
import logging
from ..config.temporal import (
    APPOINTMENT_TEMPORAL_TYPE,
    RESERVATION_TEMPORAL_TYPE,
    TimeMode,
)
from ..config.intent_meta import get_intent_registry

logger = logging.getLogger(__name__)


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


def resolve_tenant_service_id(
    services: List[Dict[str, Any]],
    entities: Optional[Dict[str, Any]] = None,
    tenant_context: Optional[Dict[str, Any]] = None,
    booking_mode: Optional[str] = None
) -> Tuple[Optional[str], Optional[str], Dict[str, Any]]:
    """
    Enforce tenant-authoritative service resolution with strict rules.
    
    INVARIANTS:
    1. Tenant service IDs are the only bookable services
    2. Canonical/family IDs (e.g. hospitality.room) are never bookable
    3. Exact tenant_service_id match wins immediately (no ambiguity checks)
    4. Canonical family resolution is allowed only as a fallback
    5. Never auto-resolve a family name that has multiple tenant services behind it
    
    Resolution Logic (ordered):
    1. If service has tenant_service_id set (from ALIAS annotation) → resolve immediately (authoritative)
    2. Map canonical family → tenant services via tenant_context.aliases
    3. Apply cardinality rules:
       - cardinality = 0 → UNSUPPORTED_SERVICE
       - cardinality > 1 → AMBIGUOUS_SERVICE (never auto-resolve)
       - cardinality = 1 → resolve only if family doesn't map to >1 tenant services
    
    Args:
        services: List of service dictionaries from resolved_booking
        entities: Optional raw entities containing service annotations
        tenant_context: Optional tenant context with aliases mapping
        booking_mode: Optional booking mode ("service" or "reservation")
    
    Returns:
        Tuple of (tenant_service_id, clarification_reason, resolution_metadata)
        - tenant_service_id: Resolved tenant service ID (None if clarification needed)
        - clarification_reason: Reason code if clarification needed (None if resolved)
        - resolution_metadata: Diagnostic information about the resolution
    """
    resolution_metadata = {
        "canonical_families": [],
        "alias_hits": [],
        "family_hits": [],
        "cardinality": 0,
        "resolution_strategy": None
    }
    
    if not services:
        return None, "MISSING_SERVICE", resolution_metadata
    
    # Filter out MODIFIER annotations - modifiers are not services
    # Only ALIAS and FAMILY annotations count as services
    services = [
        s for s in services
        if isinstance(s, dict) and s.get("annotation_type") != "MODIFIER"
    ]
    
    if not services:
        # Only modifiers present - no actual services
        resolution_metadata["resolution_strategy"] = "only_modifiers"
        logger.warning(
            "[service_resolution] Only MODIFIER annotations present - no services to resolve"
        )
        return None, "MISSING_SERVICE", resolution_metadata
    
    # RULE 1: Exact tenant_service_id match wins immediately (authoritative)
    # Services with annotation_type="ALIAS" and tenant_service_id already set
    # are resolved from tenant aliases and require no further processing
    for service in services:
        if isinstance(service, dict):
            tenant_service_id = service.get("tenant_service_id")
            if tenant_service_id:
                # tenant_service_id is present - this is authoritative, resolve immediately
                resolution_metadata["resolution_strategy"] = "tenant_service_id_authoritative"
                resolution_metadata["alias_hits"] = [{
                    "alias_text": service.get("text", ""),
                    "tenant_service_id": tenant_service_id,
                    "annotation_type": service.get("annotation_type")
                }]
                logger.info(
                    f"[service_resolution] tenant_service_id authoritative: '{service.get('text', '')}' → '{tenant_service_id}' (resolved immediately)"
                )
                return tenant_service_id, None, resolution_metadata
    
    # RULE 2: Canonical family resolution (fallback only)
    # If no tenant_service_id is present, try to map canonical family → tenant services
    
    # Get canonical families from services
    canonical_families = []
    for service in services:
        canonical = service.get("canonical")
        if canonical and canonical not in canonical_families:
            canonical_families.append(canonical)
    
    if not canonical_families:
        # No canonical families to resolve
        resolution_metadata["resolution_strategy"] = "no_canonical_families"
        logger.warning(
            "[service_resolution] No canonical families found in services"
        )
        return None, "MISSING_SERVICE", resolution_metadata
    
    resolution_metadata["canonical_families"] = canonical_families
    
    # Check for tenant context (required for canonical → tenant mapping)
    if not tenant_context:
        resolution_metadata["resolution_strategy"] = "no_tenant_context"
        logger.warning(
            f"[service_resolution] No tenant context - cannot resolve canonical families: {canonical_families}"
        )
        return None, "UNSUPPORTED_SERVICE", resolution_metadata
    
    aliases = tenant_context.get("aliases", {})
    if not isinstance(aliases, dict):
        resolution_metadata["resolution_strategy"] = "invalid_aliases"
        return None, "UNSUPPORTED_SERVICE", resolution_metadata
    
    # Build reverse mapping: canonical_family -> list of tenant_service_ids
    # aliases dict structure: {"alias_key": "canonical_family"}
    # Example: {"standard": "room", "deluxe": "room", "suite": "room"} → "room" -> ["standard", "deluxe", "suite"]
    family_to_tenant_services: Dict[str, List[str]] = {}
    for alias_key, canonical_family in aliases.items():
        # alias_key is the tenant_service_id, canonical_family is the value
        if canonical_family not in family_to_tenant_services:
            family_to_tenant_services[canonical_family] = []
        if alias_key not in family_to_tenant_services[canonical_family]:
            family_to_tenant_services[canonical_family].append(alias_key)
    
    # Map canonical families to tenant services
    all_tenant_services: List[str] = []
    for canonical_family in canonical_families:
        tenant_services = family_to_tenant_services.get(canonical_family, [])
        all_tenant_services.extend(tenant_services)
        resolution_metadata["family_hits"].append({
            "canonical_family": canonical_family,
            "tenant_services": tenant_services
        })
    
    # Remove duplicates while preserving order
    unique_tenant_services = []
    for ts in all_tenant_services:
        if ts not in unique_tenant_services:
            unique_tenant_services.append(ts)
    
    resolution_metadata["cardinality"] = len(unique_tenant_services)
    
    # RULE 3: Apply cardinality rules
    if len(unique_tenant_services) == 0:
        # No tenant services map to this canonical family
        resolution_metadata["resolution_strategy"] = "cardinality_0"
        logger.warning(
            f"[service_resolution] Cardinality 0: canonical families {canonical_families} map to no tenant services"
        )
        return None, "UNSUPPORTED_SERVICE", resolution_metadata
    elif len(unique_tenant_services) > 1:
        # Multiple tenant services - always ambiguous
        resolution_metadata["resolution_strategy"] = "cardinality_gt1"
        logger.warning(
            f"[service_resolution] Cardinality >1: canonical families {canonical_families} map to multiple tenant services: {unique_tenant_services}"
        )
        return None, "AMBIGUOUS_SERVICE", resolution_metadata
    else:
        # Cardinality == 1: Exactly one tenant service
        # BUT: Never auto-resolve if the family itself maps to >1 tenant services
        resolved_id = unique_tenant_services[0]
        
        # Check if any canonical family maps to >1 tenant services
        # Even if we resolved to one, if the family has multiple options, it's ambiguous
        for canonical_family in canonical_families:
            tenant_services_for_family = family_to_tenant_services.get(canonical_family, [])
            if len(tenant_services_for_family) > 1:
                # This family maps to multiple tenant services - ambiguous
                # Never auto-resolve a family name that has multiple tenant services
                resolution_metadata["resolution_strategy"] = "family_maps_to_multiple_tenant_services"
                resolution_metadata["ambiguous_issue"] = "service_id"
                resolution_metadata["resolved_from_family"] = canonical_family
                resolution_metadata["family_tenant_services"] = tenant_services_for_family
                logger.warning(
                    f"[service_resolution] Canonical family '{canonical_family}' maps to {len(tenant_services_for_family)} tenant services: {tenant_services_for_family}. "
                    f"Resolved to '{resolved_id}' but family is ambiguous - requiring clarification."
                )
                return None, "AMBIGUOUS_SERVICE", resolution_metadata
        
        # Cardinality == 1 AND family maps to exactly 1 tenant service → resolve
        resolution_metadata["resolution_strategy"] = "cardinality_1_unique"
        logger.info(
            f"[service_resolution] Cardinality 1: canonical families {canonical_families} → unique tenant_service_id '{resolved_id}'"
        )
        return resolved_id, None, resolution_metadata


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

    # Get temporal shape from IntentRegistry (sole policy source)
    registry = get_intent_registry()
    intent_meta = registry.get(intent_name)
    temporal_shape = intent_meta.temporal_shape if intent_meta else None
    
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
    intent_name: Optional[str] = None,
    tenant_context: Optional[Dict[str, Any]] = None
) -> Tuple[DecisionResult, Dict[str, Any]]:
    """
    Pure function that decides booking status based on semantic dictionary and policy.

    Policy operates ONLY on semantic roles (time_mode, time_constraint, etc.),
    never on raw text or regex patterns.

    Args:
        resolved_booking: The resolved booking dictionary from semantic resolution.
                         Contains: services, date_mode, date_refs, time_mode,
                         time_refs, duration, time_constraint
        entities: Optional raw entities for additional context (contains service annotations)
        policy: Optional policy configuration dict
        intent_name: Optional intent name for temporal shape validation
        tenant_context: Optional tenant context with aliases mapping

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

    # Extract services from resolved_booking
    services = resolved_booking.get("services", [])
    date_mode = resolved_booking.get("date_mode", "none")
    date_refs = resolved_booking.get("date_refs", [])
    time_mode = resolved_booking.get("time_mode", "none")
    time_refs = resolved_booking.get("time_refs", [])
    time_constraint = resolved_booking.get("time_constraint")
    date_range = resolved_booking.get("date_range")
    time_range = resolved_booking.get("time_range")
    booking_mode = resolved_booking.get("booking_mode", "service")

    # SERVICE RESOLUTION GATE
    # Policy differs by intent:
    # - CREATE_APPOINTMENT: Accept any extracted service (ALIAS or FAMILY) as sufficient
    # - CREATE_RESERVATION: Require strict tenant-authoritative resolution
    resolved_tenant_service_id = None
    service_resolution_reason = None
    service_resolution_metadata = {}
    
    is_appointment = intent_name == "CREATE_APPOINTMENT"
    is_reservation = intent_name == "CREATE_RESERVATION"
    
    if not services:
        # No services extracted - always MISSING_SERVICE
        effective_time = _determine_effective_time(
            time_mode, time_refs, time_constraint
        )
        result = DecisionResult(
            status="NEEDS_CLARIFICATION",
            reason="MISSING_SERVICE",
            effective_time=effective_time
        )
        trace = {
            "decision": {
                "state": result.status,
                "reason": result.reason,
                "service_resolution": {
                    "resolved_tenant_service_id": None,
                    "clarification_reason": "MISSING_SERVICE",
                    "metadata": {"resolution_strategy": "no_services"}
                }
            }
        }
        return result, trace
    
    if services:
        # Attempt to resolve services to tenant_service_id
        resolved_tenant_service_id, service_resolution_reason, service_resolution_metadata = resolve_tenant_service_id(
            services=services,
            entities=entities,
            tenant_context=tenant_context,
            booking_mode=booking_mode
        )
        
        # POLICY: For CREATE_APPOINTMENT, extracted services (ALIAS or FAMILY) are sufficient
        # For CREATE_RESERVATION, strict tenant-authoritative resolution is required
        if not resolved_tenant_service_id:
            if is_reservation:
                # CREATE_RESERVATION: Strict tenant-authoritative - require tenant_service_id
                # Canonical services exist but cannot be resolved to tenant service
                effective_time = _determine_effective_time(
                    time_mode, time_refs, time_constraint
                )
                result = DecisionResult(
                    status="NEEDS_CLARIFICATION",
                    reason=service_resolution_reason or "MISSING_SERVICE",
                    effective_time=effective_time
                )
                
                # Build trace with service resolution metadata
                trace = {
                    "decision": {
                        "state": result.status,
                        "reason": result.reason,
                        "service_resolution": {
                            "resolved_tenant_service_id": None,
                            "clarification_reason": service_resolution_reason,
                            "metadata": service_resolution_metadata
                        },
                        "rule_enforced": "tenant_authoritative_service_resolution"
                    }
                }
                logger.info(
                    f"[decision] Service resolution failed: canonical services exist but no tenant_service_id resolved. "
                    f"Reason: {service_resolution_reason}"
                )
                return result, trace
            elif is_appointment:
                # CREATE_APPOINTMENT: Check if service was extracted but not resolved
                # If services exist but resolved_tenant_service_id is None, this is UNSUPPORTED_SERVICE
                # This must be checked BEFORE temporal shape validation to prevent datetime completeness from overriding
                if services and resolved_tenant_service_id is None:
                    effective_time = _determine_effective_time(
                        time_mode, time_refs, time_constraint
                    )
                    result = DecisionResult(
                        status="NEEDS_CLARIFICATION",
                        reason="UNSUPPORTED_SERVICE",
                        effective_time=effective_time
                    )
                    
                    trace = {
                        "decision": {
                            "state": result.status,
                            "reason": result.reason,
                            "expected_temporal_shape": (
                                get_intent_registry().get(intent_name).temporal_shape
                                if intent_name and get_intent_registry().get(intent_name)
                                else None
                            ),
                            "actual_temporal_shape": "none",
                            "missing_slots": [],
                            "temporal_shape_satisfied": False,
                            "rule_enforced": "service_resolution_required_for_appointments",
                            "service_resolution": {
                                "resolved_tenant_service_id": None,
                                "clarification_reason": "UNSUPPORTED_SERVICE",
                                "metadata": service_resolution_metadata
                            }
                        }
                    }
                    logger.info(
                        f"[decision] CREATE_APPOINTMENT: Service extracted but not resolved (cardinality=0). "
                        f"Marking as NEEDS_CLARIFICATION with UNSUPPORTED_SERVICE"
                    )
                    return result, trace
                # CREATE_APPOINTMENT: Accept extracted services as sufficient (don't require tenant resolution)
                # Services were extracted and resolved, so service_id is treated as PRESENT
                logger.debug(
                    f"[decision] CREATE_APPOINTMENT: Accepting extracted services without tenant resolution. "
                    f"Service count: {len(services)}"
                )
            else:
                # Unknown intent - default to strict behavior
                effective_time = _determine_effective_time(
                    time_mode, time_refs, time_constraint
                )
                result = DecisionResult(
                    status="NEEDS_CLARIFICATION",
                    reason=service_resolution_reason or "MISSING_SERVICE",
                    effective_time=effective_time
                )
                
                trace = {
                    "decision": {
                        "state": result.status,
                        "reason": result.reason,
                        "service_resolution": {
                            "resolved_tenant_service_id": None,
                            "clarification_reason": service_resolution_reason,
                            "metadata": service_resolution_metadata
                        }
                    }
                }
                return result, trace
    
    # Service resolution successful - proceed with temporal shape validation
    # Store resolved tenant_service_id in trace for downstream use
    service_resolution_trace = {
        "resolved_tenant_service_id": resolved_tenant_service_id,
        "clarification_reason": None,
        "metadata": service_resolution_metadata
    }

    # MANDATORY: Validate temporal shape completeness BEFORE any RESOLVED decision
    # This is authoritative - config and YAML define what's required
    temporal_shape_reason = _validate_temporal_shape_for_decision(
        intent_name, resolved_booking)
    
    # Get expected temporal shape from IntentRegistry (sole policy source)
    registry = get_intent_registry()
    intent_meta = registry.get(intent_name) if intent_name else None
    expected_temporal_shape = intent_meta.temporal_shape if intent_meta else None

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
                "temporal_shape_derivation": temporal_shape_derivation,
                "service_resolution": service_resolution_trace
            }
        }
        return result, trace

    # GUARD: For CREATE_APPOINTMENT, service resolution must succeed before RESOLVED
    # Do not allow datetime completeness to override service validity
    if is_appointment and services and resolved_tenant_service_id is None:
        effective_time = _determine_effective_time(
            time_mode, time_refs, time_constraint
        )
        result = DecisionResult(
            status="NEEDS_CLARIFICATION",
            reason="UNSUPPORTED_SERVICE",
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
                "temporal_shape_satisfied": False,
                "rule_enforced": "service_resolution_required_for_appointments",
                "temporal_shape_derivation": temporal_shape_derivation,
                "service_resolution": service_resolution_trace
            }
        }
        logger.info(
            f"[decision] CREATE_APPOINTMENT: Service resolution guard - preventing RESOLVED despite datetime completeness. "
            f"Service extracted but not resolved (cardinality=0)"
        )
        return result, trace

    # SYSTEM INVARIANT:
    # A booking with resolved date + time must always be RESOLVED
    # This overrides all other logic paths to prevent regressions
    # NOTE: Temporal shape validation above ensures this only applies to valid shapes
    # NOTE: Service resolution guard above prevents this from overriding service validity for appointments
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
                "temporal_shape_derivation": temporal_shape_derivation,
                "service_resolution": service_resolution_trace
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
                "temporal_shape_derivation": temporal_shape_derivation,
                "service_resolution": service_resolution_trace
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
                "temporal_shape_derivation": temporal_shape_derivation,
                "service_resolution": service_resolution_trace
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
                "temporal_shape_derivation": temporal_shape_derivation,
                "service_resolution": service_resolution_trace
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
            "temporal_shape_derivation": temporal_shape_derivation,
            "service_resolution": service_resolution_trace
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
