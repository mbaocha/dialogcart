"""
Canonical invariant validation layer.

This module computes booking status (READY | NEEDS_CLARIFICATION) and
issues based purely on intent metadata and semantic slots, without
applying runtime logic, temporal anchoring, or inspecting raw entities.

This is a diagnostic/validation layer that runs in parallel with the
main decision logic and does not affect runtime behavior.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Any, Optional, List

from ..config.intent_meta import IntentMeta
from ..config.temporal import (
    APPOINTMENT_TEMPORAL_TYPE,
    RESERVATION_TEMPORAL_TYPE,
    TimeMode,
    ALLOW_BARE_WEEKDAY_RANGE_BINDING
)
from ..config.core import STATUS_READY, STATUS_NEEDS_CLARIFICATION
from ..clarification.reasons import ClarificationReason


@dataclass
class ValidationResult:
    """
    Result of canonical invariant validation.
    
    Contains the computed status and issues based purely on intent metadata
    and semantic slots, without applying runtime logic or temporal anchoring.
    """
    status: str  # STATUS_READY | STATUS_NEEDS_CLARIFICATION
    issues: Dict[str, str]  # {slot: reason}
    missing_slots: List[str]  # Derived from issues.keys()
    clarification_reason: Optional[str] = None  # Clarification reason code if NEEDS_CLARIFICATION
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary format."""
        return {
            "status": self.status,
            "issues": self.issues,
            "missing_slots": sorted(self.missing_slots),
            "clarification_reason": self.clarification_reason
        }


def compute_invariants(
    intent_name: str,
    semantic_slots: Dict[str, Any],
    intent_meta: Optional[IntentMeta]
) -> ValidationResult:
    """
    Compute canonical invariants for booking status and issues.
    
    This function determines status and issues based solely on:
    - IntentMeta (required_slots, temporal_shape, requires_end_date)
    - Semantic slots (date_refs, time_refs, time_constraint, services)
    
    It does NOT:
    - Read raw text
    - Inspect entities directly
    - Apply temporal anchoring
    - Reference binder or calendar logic
    
    Args:
        intent_name: The intent name (e.g., "CREATE_APPOINTMENT")
        semantic_slots: Semantic booking slots (from resolved_booking)
        intent_meta: Intent metadata, or None if not available
        
    Returns:
        ValidationResult with computed status, issues, and missing_slots
    """
    issues: Dict[str, str] = {}
    
    # If no intent_meta, cannot validate - assume ready
    if not intent_meta:
        return ValidationResult(
            status=STATUS_READY,
            issues={},
            missing_slots=[]
        )
    
    # Validate required slots
    required_slots = intent_meta.required_slots or set()
    if required_slots:
        for slot in required_slots:
            if not _slot_present(slot, semantic_slots):
                issues[slot] = "missing"
    
    # For reservations, exclude service_id from required slots check
    # (reservations can proceed with just dates)
    if intent_name == "CREATE_RESERVATION" and "service_id" in issues:
        del issues["service_id"]
    
    # Validate temporal shape
    temporal_shape = intent_meta.temporal_shape
    if temporal_shape:
        temporal_issues = _validate_temporal_shape(
            temporal_shape,
            semantic_slots,
            intent_meta
        )
        issues.update(temporal_issues)
    
    # Determine status based on issues
    status = STATUS_NEEDS_CLARIFICATION if issues else STATUS_READY
    
    # Derive missing_slots from issues
    missing_slots = sorted(list(issues.keys()))
    
    # Determine clarification_reason based on issues
    clarification_reason = None
    if status == STATUS_NEEDS_CLARIFICATION:
        clarification_reason = _derive_clarification_reason(issues, intent_name, semantic_slots, intent_meta)
    
    return ValidationResult(
        status=status,
        issues=issues,
        missing_slots=missing_slots,
        clarification_reason=clarification_reason
    )


def _slot_present(slot: str, semantic_slots: Dict[str, Any]) -> bool:
    """
    Check if a slot is present in semantic_slots.
    
    Handles different slot types:
    - service_id: Check services list
    - date/time slots: Check date_refs, time_refs, time_constraint
    - Other slots: Direct lookup
    """
    if slot == "service_id":
        services = semantic_slots.get("services", [])
        if services and len(services) > 0:
            # Check if any service has a valid identifier
            for service in services:
                if isinstance(service, dict):
                    if service.get("tenant_service_id") or service.get("family_id") or service.get("text"):
                        return True
                elif service:
                    return True
        return False
    
    if slot == "date" or slot == "start_date":
        date_refs = semantic_slots.get("date_refs", [])
        date_mode = semantic_slots.get("date_mode", "none")
        # Also check for bound dates (date_range or datetime_range from calendar binding)
        date_range = semantic_slots.get("date_range")
        datetime_range = semantic_slots.get("datetime_range")
        has_bound_date = bool(date_range or datetime_range)
        # Consider date present if:
        # - We have date_refs (from current semantic resolution), OR
        # - We have bound dates (from calendar binding in current request)
        # Luma is stateless - all data comes from the current request
        return len(date_refs) > 0 or has_bound_date
    
    if slot == "end_date":
        date_refs = semantic_slots.get("date_refs", [])
        date_mode = semantic_slots.get("date_mode", "none")
        # For end_date, need at least 2 refs or date_mode == "range"
        return (len(date_refs) >= 2) or (date_mode == "range")
    
    if slot == "time":
        time_refs = semantic_slots.get("time_refs", [])
        time_constraint = semantic_slots.get("time_constraint")
        time_mode = semantic_slots.get("time_mode", "none")
        
        # Check time_constraint first (most explicit)
        if time_constraint is not None:
            tc_mode = time_constraint.get("mode")
            if tc_mode in {TimeMode.EXACT.value, TimeMode.WINDOW.value, TimeMode.FUZZY.value}:
                return True
        
        # Check time_mode and time_refs
        if time_mode in {TimeMode.EXACT.value, TimeMode.RANGE.value, TimeMode.WINDOW.value}:
            return len(time_refs) > 0
        
        return False
    
    # For other slots, check direct presence
    val = semantic_slots.get(slot)
    return val is not None and val != "" and val != []


def _is_weekday(ref: str) -> bool:
    """Check if a date reference is a weekday name."""
    weekdays = {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"}
    ref_lower = ref.lower().strip()
    return ref_lower in weekdays


def _is_bare_weekday_range(date_refs: List[str], date_modifiers: List[str]) -> bool:
    """
    Check if date_refs represents a bare weekday range (weekdays without anchors).
    
    A bare weekday range is one where:
    - All date_refs are weekday names
    - No date_modifiers are present (like "this", "next", "last")
    - No explicit dates/months/years are present
    """
    if len(date_refs) < 2:
        return False
    
    # Check if all refs are weekdays
    all_weekdays = all(_is_weekday(ref) for ref in date_refs)
    if not all_weekdays:
        return False
    
    # Check if there are any modifiers
    if date_modifiers:
        return False
    
    # Bare weekday range detected
    return True


def _validate_temporal_shape(
    temporal_shape: str,
    semantic_slots: Dict[str, Any],
    intent_meta: IntentMeta
) -> Dict[str, str]:
    """
    Validate temporal shape requirements.
    
    Returns a dict of {slot: reason} for missing temporal slots.
    
    This function also checks for unsupported binding patterns that the binder will reject:
    - Fuzzy time without date (binder cannot bind fuzzy time without date)
    - Bare weekday ranges when policy forbids them (binder will raise ValueError)
    """
    issues: Dict[str, str] = {}
    
    date_mode = semantic_slots.get("date_mode", "none")
    date_refs = semantic_slots.get("date_refs", [])
    date_modifiers = semantic_slots.get("date_modifiers", [])
    time_mode = semantic_slots.get("time_mode", "none")
    time_refs = semantic_slots.get("time_refs", [])
    time_constraint = semantic_slots.get("time_constraint")
    
    # Check for bound dates (date_range or datetime_range from calendar binding)
    date_range = semantic_slots.get("date_range")
    datetime_range = semantic_slots.get("datetime_range")
    has_bound_date = bool(date_range or datetime_range)
    
    if temporal_shape == APPOINTMENT_TEMPORAL_TYPE:
        # CREATE_APPOINTMENT requires both date and time
        
        # Check for fuzzy time without date - binder cannot bind this
        is_fuzzy_time = (
            time_constraint is not None and time_constraint.get("mode") == TimeMode.FUZZY.value
        ) or (time_mode == TimeMode.FUZZY.value)
        
        # CREATE_APPOINTMENT date validation: check current semantic state
        # Accept date_refs if they exist (regardless of date_mode), OR if bound dates exist (from calendar binding)
        # Luma is stateless - all data comes from the current request
        has_valid_date = (
            len(date_refs) > 0 or
            has_bound_date
        )
        
        # CRITICAL: Fuzzy time requires a date - if date is missing, mark as missing
        if is_fuzzy_time and not has_valid_date:
            issues["date"] = "missing"
        
        # General date validation
        if not has_valid_date:
            issues["date"] = "missing"
        
        # Time validation
        # CREATE_APPOINTMENT accepts: exact, range, window, or fuzzy time
        # Fuzzy time is bindable when date exists (binder converts via FUZZY_TIME_WINDOWS)
        has_valid_time = (
            (time_constraint is not None and time_constraint.get("mode") in {TimeMode.EXACT.value, TimeMode.WINDOW.value, TimeMode.FUZZY.value}) or
            (time_mode in {TimeMode.EXACT.value, TimeMode.RANGE.value, TimeMode.WINDOW.value, TimeMode.FUZZY.value} and len(time_refs) > 0)
        )
        
        if not has_valid_time:
            issues["time"] = "missing"
    
    elif temporal_shape == RESERVATION_TEMPORAL_TYPE:
        # CREATE_RESERVATION requires start_date and optionally end_date
        
        # Check for bare weekday range when policy forbids it
        if not ALLOW_BARE_WEEKDAY_RANGE_BINDING:
            # If date_mode is "flexible", this indicates an unanchored weekday range
            if date_mode == "flexible" and len(date_refs) >= 2:
                # Unanchored weekday range - mark both dates as missing
                issues["start_date"] = "missing"
                issues["end_date"] = "missing"
                return issues
            
            # Defensive check: detect bare weekday range even if date_mode is "range"
            # (semantic resolver might set date_mode to "range" but it's still unanchored)
            if date_mode == "range" and len(date_refs) >= 2 and _is_bare_weekday_range(date_refs, date_modifiers):
                if not has_bound_date:
                    # Bare weekday range without bound dates - mark as missing
                    issues["start_date"] = "missing"
                    issues["end_date"] = "missing"
                    return issues
        
        # General date validation (consider bound dates from calendar binding)
        has_start = (
            (len(date_refs) >= 1 and date_mode not in ("none", "flexible")) or
            (has_bound_date and date_range)  # date_range implies start_date
        )
        
        if not has_start:
            issues["start_date"] = "missing"
        
        requires_end_date = intent_meta.requires_end_date or False
        if requires_end_date:
            has_end = (
                (len(date_refs) >= 2 and date_mode not in ("none", "flexible")) or
                (date_mode == "range") or
                (has_bound_date and date_range and date_range.get("end"))  # date_range with end implies end_date
            )
            if not has_end:
                issues["end_date"] = "missing"
    
    return issues


def _derive_clarification_reason(
    issues: Dict[str, str],
    intent_name: str,
    semantic_slots: Dict[str, Any],
    intent_meta: Optional[IntentMeta]
) -> Optional[str]:
    """
    Derive clarification_reason from issues.
    
    Returns appropriate ClarificationReason enum value based on missing slots.
    Priority order:
    1. MISSING_SERVICE (if service_id missing)
    2. MISSING_DATE_RANGE (if both start_date and end_date missing)
    3. MISSING_START_DATE (if start_date missing)
    4. MISSING_END_DATE (if end_date missing)
    5. MISSING_DATE (if date missing)
    6. MISSING_TIME (if time missing)
    7. INCOMPLETE_BINDING (fallback for temporal shape issues)
    """
    if "service_id" in issues or "service" in issues:
        return ClarificationReason.MISSING_SERVICE.value
    
    # Check for date range issues (reservation-specific)
    if intent_meta and intent_meta.temporal_shape == RESERVATION_TEMPORAL_TYPE:
        if "start_date" in issues and "end_date" in issues:
            return ClarificationReason.MISSING_DATE_RANGE.value
        if "start_date" in issues:
            return ClarificationReason.MISSING_DATE.value  # Use MISSING_DATE for start_date
        if "end_date" in issues:
            return ClarificationReason.MISSING_DATE.value  # Use MISSING_DATE for end_date
    
    # Appointment temporal shape issues
    if intent_meta and intent_meta.temporal_shape == APPOINTMENT_TEMPORAL_TYPE:
        if "date" in issues and "time" in issues:
            return ClarificationReason.INCOMPLETE_BINDING.value
        if "date" in issues:
            return ClarificationReason.MISSING_DATE.value
        if "time" in issues:
            # Check if it's fuzzy time
            time_constraint = semantic_slots.get("time_constraint")
            if time_constraint and time_constraint.get("mode") == TimeMode.FUZZY.value:
                return ClarificationReason.MISSING_TIME_FUZZY.value
            return ClarificationReason.MISSING_TIME.value
    
    # Generic fallbacks
    if "date" in issues:
        return ClarificationReason.MISSING_DATE.value
    if "time" in issues:
        return ClarificationReason.MISSING_TIME.value
    if "start_date" in issues:
        return ClarificationReason.MISSING_DATE.value
    if "end_date" in issues:
        return ClarificationReason.MISSING_DATE.value
    
    # Default fallback
    return ClarificationReason.INCOMPLETE_BINDING.value

