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
from ..clarification.reasons import ClarificationReason
from ..utils.missing_slots import derive_missing_slot_from_reason

logger = logging.getLogger(__name__)


def _normalize_canonical_to_full(canonical: str, booking_mode: str = "service") -> str:
    """
    Normalize short canonical form to full canonical form.
    
    Args:
        canonical: Canonical form (short like "haircut" or full like "beauty_and_wellness.haircut")
        booking_mode: Booking mode ("service" or "reservation") to determine domain prefix
    
    Returns:
        Full canonical form (e.g., "beauty_and_wellness.haircut" or "hospitality.room")
    """
    if "." in canonical:
        # Already full canonical form
        return canonical
    
    # Short form - add domain prefix based on booking_mode
    if booking_mode == "reservation":
        return f"hospitality.{canonical}"
    else:
        # Default to service domain
        return f"beauty_and_wellness.{canonical}"


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
       - cardinality > 1 → MULTIPLE_MATCHES (never auto-resolve)
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

    # RULE 1: Exact tenant_service_id or resolved_alias match wins immediately (authoritative)
    # Services with annotation_type="ALIAS" and tenant_service_id already set
    # OR services with resolved_alias set by semantic resolver (explicit alias match)
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
            
            # Also check for resolved_alias (set by semantic resolver for explicit alias matches)
            # This handles FAMILY annotations that were matched to explicit tenant aliases
            resolved_alias = service.get("resolved_alias")
            if resolved_alias:
                # resolved_alias is present - explicit alias match found, resolve immediately
                resolution_metadata["resolution_strategy"] = "resolved_alias_authoritative"
                resolution_metadata["alias_hits"] = [{
                    "alias_text": service.get("text", ""),
                    "tenant_service_id": resolved_alias,
                    "annotation_type": service.get("annotation_type"),
                    "source": "resolved_alias"
                }]
                logger.info(
                    f"[service_resolution] resolved_alias authoritative: '{service.get('text', '')}' → '{resolved_alias}' (resolved immediately)"
                )
                return resolved_alias, None, resolution_metadata

    # RULE 2: Canonical family resolution (fallback only)
    # If no tenant_service_id is present, try to map canonical family → tenant services

    # Get canonical families from services and normalize to full canonical form
    canonical_families = []
    normalized_canonical_families = []
    for service in services:
        canonical = service.get("canonical")
        if canonical:
            # Normalize to full canonical form for consistent matching
            normalized_canonical = _normalize_canonical_to_full(canonical, booking_mode or "service")
            if normalized_canonical not in normalized_canonical_families:
                canonical_families.append(canonical)  # Keep original for metadata
                normalized_canonical_families.append(normalized_canonical)

    if not normalized_canonical_families:
        # No canonical families to resolve
        resolution_metadata["resolution_strategy"] = "no_canonical_families"
        logger.warning(
            "[service_resolution] No canonical families found in services"
        )
        return None, "MISSING_SERVICE", resolution_metadata

    resolution_metadata["canonical_families"] = canonical_families  # Store original for logging

    # Check for tenant context (required for canonical → tenant mapping)
    if not tenant_context:
        resolution_metadata["resolution_strategy"] = "no_tenant_context"
        logger.warning(
            f"[service_resolution] No tenant context - cannot resolve canonical families: {normalized_canonical_families}"
        )
        return None, ClarificationReason.UNSUPPORTED_SERVICE.value, resolution_metadata

    aliases = tenant_context.get("aliases", {})
    if not isinstance(aliases, dict):
        resolution_metadata["resolution_strategy"] = "invalid_aliases"
        return None, ClarificationReason.UNSUPPORTED_SERVICE.value, resolution_metadata

    # Get booking_mode for normalization
    booking_mode_for_normalization = booking_mode
    if not booking_mode_for_normalization and tenant_context:
        booking_mode_for_normalization = tenant_context.get("booking_mode", "service")
    if not booking_mode_for_normalization:
        booking_mode_for_normalization = "service"

    # Build reverse mapping: canonical_family (normalized) -> list of tenant_service_ids
    # aliases dict structure: {"alias_key": "canonical_family"}
    # Normalize canonical_family values to full canonical form for consistent matching
    # Example: {"standard": "room", "deluxe": "room", "suite": "room"} → "hospitality.room" -> ["standard", "deluxe", "suite"]
    family_to_tenant_services: Dict[str, List[str]] = {}
    for alias_key, canonical_family in aliases.items():
        # alias_key is the tenant_service_id, canonical_family is the value
        # Normalize canonical_family to full canonical form
        normalized_family = _normalize_canonical_to_full(canonical_family, booking_mode_for_normalization)
        if normalized_family not in family_to_tenant_services:
            family_to_tenant_services[normalized_family] = []
        if alias_key not in family_to_tenant_services[normalized_family]:
            family_to_tenant_services[normalized_family].append(alias_key)

    # Map canonical families (normalized) to tenant services
    all_tenant_services: List[str] = []
    for i, normalized_canonical_family in enumerate(normalized_canonical_families):
        tenant_services = family_to_tenant_services.get(normalized_canonical_family, [])
        all_tenant_services.extend(tenant_services)
        # Use original canonical for metadata
        original_canonical = canonical_families[i] if i < len(canonical_families) else normalized_canonical_family
        resolution_metadata["family_hits"].append({
            "canonical_family": original_canonical,
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
        return None, ClarificationReason.UNSUPPORTED_SERVICE.value, resolution_metadata
    elif len(unique_tenant_services) > 1:
        # Multiple tenant services - always ambiguous
        # Use MULTIPLE_MATCHES for consistency with semantic resolver
        resolution_metadata["resolution_strategy"] = "cardinality_gt1"
        # Include options for clarification_data
        resolution_metadata["options"] = unique_tenant_services
        logger.warning(
            f"[service_resolution] Cardinality >1: canonical families {canonical_families} map to multiple tenant services: {unique_tenant_services}"
        )
        return None, ClarificationReason.MULTIPLE_MATCHES.value, resolution_metadata
    else:
        # Cardinality == 1: Exactly one tenant service
        # BUT: Never auto-resolve if the family itself maps to >1 tenant services
        resolved_id = unique_tenant_services[0]

        # Check if any canonical family maps to >1 tenant services
        # Even if we resolved to one, if the family has multiple options, it's ambiguous
        for normalized_canonical_family in normalized_canonical_families:
            tenant_services_for_family = family_to_tenant_services.get(
                normalized_canonical_family, [])
            if len(tenant_services_for_family) > 1:
                # This family maps to multiple tenant services - ambiguous
                # Never auto-resolve a family name that has multiple tenant services
                resolution_metadata["resolution_strategy"] = "family_maps_to_multiple_tenant_services"
                resolution_metadata["ambiguous_issue"] = "service_id"
                resolution_metadata["resolved_from_family"] = normalized_canonical_family
                resolution_metadata["family_tenant_services"] = tenant_services_for_family
                # Include options for clarification_data
                resolution_metadata["options"] = tenant_services_for_family
                logger.warning(
                    f"[service_resolution] Canonical family '{normalized_canonical_family}' maps to {len(tenant_services_for_family)} tenant services: {tenant_services_for_family}. "
                    f"Resolved to '{resolved_id}' but family is ambiguous - requiring clarification."
                )
                return None, ClarificationReason.MULTIPLE_MATCHES.value, resolution_metadata

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
    # Determine booking_mode from resolved_booking or tenant_context (fallback to "service")
    booking_mode = resolved_booking.get("booking_mode")
    if not booking_mode and tenant_context:
        booking_mode = tenant_context.get("booking_mode", "service")
    if not booking_mode:
        booking_mode = "service"

    # SERVICE RESOLUTION GATE
    # Policy differs by intent:
    # - All requests require strict tenant-authoritative resolution
    resolved_tenant_service_id = None
    service_resolution_reason = None
    service_resolution_metadata = {}

    is_appointment = intent_name == "CREATE_APPOINTMENT"
    is_reservation = intent_name == "CREATE_RESERVATION"
    is_modify = intent_name == "MODIFY_BOOKING"
    is_cancel = intent_name == "CANCEL_BOOKING"

    # For MODIFY_BOOKING and CANCEL_BOOKING, service is not required - only booking_id is required
    if is_modify or is_cancel:
        # Check for booking_id in entities instead of services
        booking_id = entities.get("booking_id") if entities else None
        if not booking_id:
            # For MODIFY/CANCEL, booking_id is required - return early if missing
            # For MODIFY_BOOKING, also include missing deltas (date/time or start/end dates)
            missing_slots = ["booking_id"]
            
            if is_modify:
                # Determine missing deltas based on what the user is trying to change
                has_date = (date_mode is not None and date_mode != "none" and len(date_refs) > 0) or date_range is not None
                has_time = ((time_mode is not None and time_mode != "none" and len(time_refs) > 0) or time_constraint is not None)
                
                if booking_mode == "service":
                    # Appointment-style: requires date and time
                    if has_time and not has_date:
                        # Time present but no date → missing includes ["booking_id", "date"]
                        missing_slots.append("date")
                    elif has_date and not has_time:
                        # Date present but no time → missing includes ["booking_id", "time"]
                        missing_slots.append("time")
                    elif not has_date and not has_time:
                        # Neither date nor time → missing includes ["booking_id", "date", "time"]
                        missing_slots.extend(["date", "time"])
                elif booking_mode == "reservation":
                    # Reservation-style: requires start_date and end_date
                    # Check if both start and end dates are present (2+ date_refs OR date_range OR date_mode == "range")
                    has_start = len(date_refs) >= 1 or date_mode == "range" or (date_range is not None)
                    has_end = len(date_refs) >= 2 or date_mode == "range" or (date_range is not None and isinstance(date_range, dict) and date_range.get("start") and date_range.get("end"))
                    
                    if not has_start or not has_end:
                        # Missing start_date and/or end_date → missing includes ["booking_id", "start_date", "end_date"]
                        missing_slots.extend(["start_date", "end_date"])
            
            effective_time = _determine_effective_time(
                time_mode, time_refs, time_constraint
            )
            result = DecisionResult(
                status="NEEDS_CLARIFICATION",
                reason=ClarificationReason.MISSING_BOOKING_REFERENCE.value,
                effective_time=effective_time
            )
            trace = {
                "decision": {
                    "state": result.status,
                    "reason": result.reason,
                    "missing_slots": missing_slots,
                    "service_resolution": {
                        "resolved_tenant_service_id": None,
                        "clarification_reason": "MISSING_BOOKING_REFERENCE",
                        "metadata": {"resolution_strategy": "booking_id_required"}
                    }
                }
            }
            return result, trace
        
        # booking_id present - for MODIFY_BOOKING, check for change deltas before proceeding
        # MODIFY_BOOKING requires at least one change delta (date, time, date_range, start_date, end_date, service_id, duration)
        if is_modify:
            # Check for change deltas in resolved_booking
            has_date = (date_mode is not None and date_mode != "none" and len(date_refs) > 0) or date_range is not None
            has_time = ((time_mode is not None and time_mode != "none" and len(time_refs) > 0) or time_constraint is not None)
            has_service_id = bool(services and len(services) > 0)
            has_duration = resolved_booking.get("duration") is not None
            
            # Special case: For reservations, single date should require clarification for end_date
            # Don't treat single date as a valid change delta - semantic resolver should have set clarification
            # Check both: (1) only one date_ref exists, OR (2) date_range exists with start == end (collapsed single date)
            is_single_date_reservation = False
            if booking_mode == "reservation":
                if len(date_refs) == 1 and not date_range:
                    # Single date_ref for reservation - require clarification
                    is_single_date_reservation = True
                elif date_range and isinstance(date_range, dict):
                    # Check if date_range was collapsed from single date (start == end)
                    start_date = date_range.get("start_date") or date_range.get("start")
                    end_date = date_range.get("end_date") or date_range.get("end")
                    if start_date and end_date and start_date == end_date and len(date_refs) <= 1:
                        # Single date collapsed into date_range - require clarification
                        is_single_date_reservation = True
            
            if is_single_date_reservation:
                # Single date for reservation - require clarification for end_date (semantic resolver should have set this)
                effective_time = _determine_effective_time(
                    time_mode, time_refs, time_constraint
                )
                result = DecisionResult(
                    status="NEEDS_CLARIFICATION",
                    reason=ClarificationReason.MISSING_DATE.value,
                    effective_time=effective_time
                )
                trace = {
                    "decision": {
                        "state": result.status,
                        "reason": result.reason,
                        "missing_slots": ["end_date"],
                        "service_resolution": {
                            "resolved_tenant_service_id": None,
                            "clarification_reason": "MISSING_DATE",
                            "metadata": {"resolution_strategy": "single_date_reservation", "booking_id_present": True}
                        }
                    }
                }
                logger.info(
                    f"[decision] MODIFY_BOOKING reservation: single date detected, requiring end_date clarification. "
                    f"date_refs={date_refs}, date_range={date_range}",
                    extra={'missing_slots': ["end_date"], 'booking_id': booking_id, 'date_refs': date_refs, 'date_range': date_range}
                )
                return result, trace
            
            # Check if any change delta exists (after excluding single-date reservations)
            has_change_delta = has_date or has_time or has_service_id or has_duration
            
            if not has_change_delta:
                # booking_id present but no change delta - need clarification
                effective_time = _determine_effective_time(
                    time_mode, time_refs, time_constraint
                )
                
                # Determine missing slots based on booking_mode and wording
                # Priority: booking_mode context > generic wording detection
                # Heuristic: Check booking_mode first; if explicit, use specific deltas
                # Exception: For "service" mode with no temporal entities, check if wording is truly generic
                # Case 84: "modify booking FGH890" with booking_mode="service" → ["date", "time"] (specific)
                # Case 85: "reschedule reservation IJK123" with booking_mode="reservation" → ["start_date", "end_date"] (specific)
                # Case 99: "reschedule my booking ABC123" with booking_mode="service" → ["change"] (generic)
                missing_slots_list = []
                
                # Check if ANY temporal entities were actually extracted
                # Only consider entities extracted, not mode values which might be defaults
                has_any_extracted_temporal_entities = (len(date_refs) > 0 or len(time_refs) > 0 or 
                                                       time_constraint is not None or 
                                                       date_range is not None)
                
                # Check if we can infer generic wording from entities (e.g., "reschedule my booking" vs "modify booking")
                # Try to detect if the original sentence was truly generic by checking entities for clues
                is_generic_wording = False
                if entities and isinstance(entities, dict):
                    # Check for generic patterns in osentence if available
                    osentence = entities.get("osentence", "").lower() if isinstance(entities.get("osentence"), str) else ""
                    if osentence:
                        # Generic patterns: "reschedule my booking" (possessive + "booking")
                        # Specific patterns: "modify booking", "reschedule reservation" (verb + specific noun)
                        has_possessive_my = " my " in osentence or osentence.startswith("my ")
                        has_generic_reschedule = "reschedule" in osentence and (" my booking" in osentence or " my reservation" in osentence)
                        # If sentence has "reschedule my booking" pattern, it's generic wording
                        if has_possessive_my and has_generic_reschedule:
                            is_generic_wording = True
                            logger.info(
                                f"[decision] MODIFY_BOOKING: detected generic wording pattern 'reschedule my booking', "
                                f"booking_mode={booking_mode}, has_temporal_entities={has_any_extracted_temporal_entities}",
                                extra={'booking_mode': booking_mode, 'has_temporal_entities': has_any_extracted_temporal_entities, 'osentence': osentence}
                            )
                
                # Priority 1: If booking_mode is "reservation", always use specific deltas (Case 85)
                if booking_mode == "reservation":
                    missing_slots_list = ["start_date", "end_date"]
                # Priority 2: If booking_mode is "service" and wording is generic (Case 99), use ["change"]
                elif booking_mode == "service" and is_generic_wording and not has_any_extracted_temporal_entities:
                    missing_slots_list = ["change"]
                # Priority 3: If booking_mode is "service" (Case 84), use specific deltas
                elif booking_mode == "service":
                    missing_slots_list = ["date", "time"]
                # Priority 4: No booking_mode context, check temporal entities
                elif not has_any_extracted_temporal_entities:
                    missing_slots_list = ["change"]
                else:
                    # Has temporal entities but no booking_mode - default to generic ["change"]
                    missing_slots_list = ["change"]
                
                result = DecisionResult(
                    status="NEEDS_CLARIFICATION",
                    reason=ClarificationReason.MISSING_CONTEXT.value,
                    effective_time=effective_time
                )
                trace = {
                    "decision": {
                        "state": result.status,
                        "reason": result.reason,
                        "missing_slots": missing_slots_list,
                        "service_resolution": {
                            "resolved_tenant_service_id": None,
                            "clarification_reason": "MISSING_CONTEXT",
                            "metadata": {
                                "resolution_strategy": "no_change_delta", 
                                "booking_id_present": True,
                                "booking_mode": booking_mode,
                                "is_generic_wording": is_generic_wording,
                                "has_temporal_entities": has_any_extracted_temporal_entities
                            }
                        }
                    }
                }
                logger.info(
                    f"[decision] MODIFY_BOOKING: booking_id present but no change delta. "
                    f"Missing slots: {missing_slots_list}, booking_mode={booking_mode}, "
                    f"is_generic_wording={is_generic_wording}, has_temporal_entities={has_any_extracted_temporal_entities}",
                    extra={
                        'missing_slots': missing_slots_list, 
                        'booking_id': booking_id,
                        'booking_mode': booking_mode,
                        'is_generic_wording': is_generic_wording,
                        'has_temporal_entities': has_any_extracted_temporal_entities
                    }
                )
                return result, trace
        
        # booking_id present and change delta exists (for MODIFY_BOOKING) or CANCEL_BOOKING
        # Skip service resolution entirely and continue to temporal validation
        # Set resolved_tenant_service_id to None since we're not resolving services
        resolved_tenant_service_id = None
        service_resolution_reason = None
        service_resolution_metadata = {"resolution_strategy": "skipped_modify_cancel"}
    elif not services:
        # No services extracted - always MISSING_SERVICE (for CREATE_* intents)
        effective_time = _determine_effective_time(
            time_mode, time_refs, time_constraint
        )
        result = DecisionResult(
            status="NEEDS_CLARIFICATION",
            reason=ClarificationReason.MISSING_SERVICE.value,
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
    else:
        # services present - attempt to resolve services to tenant_service_id (for CREATE_* intents)
        resolved_tenant_service_id, service_resolution_reason, service_resolution_metadata = resolve_tenant_service_id(
            services=services,
            entities=entities,
            tenant_context=tenant_context,
            booking_mode=booking_mode
        )

        # POLICY: All CREATE_* requests require tenant-authoritative resolution
        # No special case for appointments - all require tenant_service_id
        if not resolved_tenant_service_id:
            # Service resolution failed - return early with service resolution reason
            # This takes priority over temporal validation
            effective_time = _determine_effective_time(
                time_mode, time_refs, time_constraint
            )
            result = DecisionResult(
                status="NEEDS_CLARIFICATION",
                reason=service_resolution_reason or "MISSING_SERVICE",
                effective_time=effective_time
            )

            # Build trace with service resolution metadata
            # Include "service" in missing_slots since resolution failed
            missing_slots_list = ["service"]
            trace = {
                "decision": {
                    "state": result.status,
                    "reason": result.reason,
                    "missing_slots": missing_slots_list,
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

    # Store resolved tenant_service_id in trace for downstream use
    # Note: service_resolution_reason may be set even if resolved_tenant_service_id is None
    service_resolution_trace = {
        "resolved_tenant_service_id": resolved_tenant_service_id,
        "clarification_reason": service_resolution_reason,
        "metadata": service_resolution_metadata
    }

    # MANDATORY: Validate temporal shape completeness BEFORE any RESOLVED decision
    # This is authoritative - config and YAML define what's required
    # SKIP temporal validation for MODIFY_BOOKING - it only requires change deltas, not temporal shape
    temporal_shape_reason = None
    if intent_name != "MODIFY_BOOKING":
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

        # Build temporal shape derivation
        temporal_shape_derivation = {
            "date_present": bool(date_refs and date_mode != "none"),
            "time_present": bool(time_refs and time_mode != "none") or (time_constraint is not None),
            "derived_shape": actual_shape
        }

        # Build missing_slots list - derive from temporal shape reason
        missing_slots_list = derive_missing_slot_from_reason(temporal_shape_reason)

        # Note: Service resolution is checked first and returns early if it fails,
        # so at this point service resolution must have succeeded
        # The result.reason is already set correctly from temporal validation above

        rule_enforced = "temporal_shape_validation"

        trace = {
            "decision": {
                "state": result.status,
                "reason": result.reason,
                "expected_temporal_shape": expected_temporal_shape,
                "actual_temporal_shape": actual_shape,
                "missing_slots": missing_slots_list,
                "temporal_shape_satisfied": False,
                "rule_enforced": rule_enforced,
                "temporal_shape_derivation": temporal_shape_derivation,
                "service_resolution": service_resolution_trace
            }
        }
        return result, trace

    # SYSTEM INVARIANT:
    # A booking with resolved date + time must always be RESOLVED
    # This overrides all other logic paths to prevent regressions
    # NOTE: Temporal shape validation above ensures this only applies to valid shapes
    # NOTE: Service resolution is checked first and returns early if it fails, so service is always valid here
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
            reason=ClarificationReason.POLICY_TIME_WINDOW.value,
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
            reason=ClarificationReason.MISSING_TIME_FUZZY.value,
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
            reason=ClarificationReason.POLICY_CONSTRAINT_ONLY_TIME.value,
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
