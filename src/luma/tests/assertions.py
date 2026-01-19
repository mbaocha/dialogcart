"""
Invariant-based test assertions for Luma API responses.

These helpers assert invariants (properties that should always be true)
without depending on specific clarification reasons or internal implementation details.

These are additive safety nets - they should be called in addition to existing
test assertions, not as replacements.
"""
from typing import Dict, Any, Optional
from luma.config.core import STATUS_READY, STATUS_NEEDS_CLARIFICATION


def assert_no_partial_binding(response: Dict[str, Any]) -> None:
    """
    Assert that ready responses don't have incomplete binding (missing required temporal fields).
    
    INVARIANT: If status is READY, the response must have complete temporal binding:
    - CREATE_APPOINTMENT must have datetime_range (or has_datetime=True implies datetime_range)
    - CREATE_RESERVATION must have date_range
    
    This helper only checks that required fields are present if status is READY.
    It does NOT check specific values, only presence/absence.
    
    Args:
        response: API response dictionary
        
    Raises:
        AssertionError: If ready response lacks required bound fields
    """
    status = response.get("status")
    if status != STATUS_READY:
        # Only check for ready status
        return
    
    intent_name = response.get("intent", {}).get("name", "")
    slots = response.get("slots", {})
    
    if intent_name == "CREATE_APPOINTMENT":
        # Appointments require datetime_range if has_datetime is True
        has_datetime = slots.get("has_datetime", False)
        if has_datetime:
            datetime_range = slots.get("datetime_range")
            assert datetime_range is not None, (
                f"INVARIANT VIOLATION: READY appointment with has_datetime=True "
                f"must have datetime_range in slots, but it's missing"
            )
            assert "start" in datetime_range, (
                f"INVARIANT VIOLATION: datetime_range must have 'start' field"
            )
            assert "end" in datetime_range, (
                f"INVARIANT VIOLATION: datetime_range must have 'end' field"
            )
    
    elif intent_name == "CREATE_RESERVATION":
        # Reservations require date_range
        date_range = slots.get("date_range")
        assert date_range is not None, (
            f"INVARIANT VIOLATION: READY reservation must have date_range in slots, "
            f"but it's missing"
        )
        assert "start" in date_range, (
            f"INVARIANT VIOLATION: date_range must have 'start' field"
        )
        assert "end" in date_range, (
            f"INVARIANT VIOLATION: date_range must have 'end' field"
        )


def assert_clarification_has_missing_slots(response: Dict[str, Any]) -> None:
    """
    Assert that clarification responses have missing slots consistent with issues.
    
    INVARIANT: If status is NEEDS_CLARIFICATION, the response should have:
    - issues dict with at least one missing slot, OR
    - clarification_data indicating what's missing, OR
    - clarification_reason (which indicates some form of clarification needed)
    
    This helper checks that clarification responses aren't completely empty - they must
    indicate what's missing. It derives missing slots from issues structure.
    
    Args:
        response: API response dictionary
        
    Raises:
        AssertionError: If clarification response lacks any clarification information
    """
    status = response.get("status")
    if status != STATUS_NEEDS_CLARIFICATION:
        # Only check for clarification status
        return
    
    issues = response.get("issues", {})
    
    # Derive missing slots from issues (consistent with test_luma.py logic)
    missing_slots = [
        slot for slot, issue in issues.items()
        if issue == "missing" or (isinstance(issue, dict) and issue.get("type") == "missing")
    ]
    
    # INVARIANT: Clarification responses should indicate what's missing
    # However, some clarifications (like MULTIPLE_MATCHES) might have clarification_data
    # instead of issues. Allow either missing slots, clarification_data, or any issues.
    has_missing_slots = len(missing_slots) > 0
    has_clarification_data = response.get("clarification_data") is not None
    has_any_issue = len(issues) > 0  # Even non-missing issues (like ambiguous_meridiem)
    
    # At least one form of clarification information should be present
    # Note: We don't check clarification_reason to avoid depending on specific values
    assert has_missing_slots or has_clarification_data or has_any_issue, (
        f"INVARIANT VIOLATION: NEEDS_CLARIFICATION response should have some form of "
        f"clarification information (missing slots, clarification_data, or issues), "
        f"but found none. Issues: {issues}"
    )


def assert_ready_has_required_bound_fields(response: Dict[str, Any], intent_name: Optional[str] = None) -> None:
    """
    Assert that ready responses have required bound fields based on intent.
    
    INVARIANT: READY responses must have required bound fields:
    - CREATE_APPOINTMENT: datetime_range (if has_datetime=True) or slots indicate completion
    - CREATE_RESERVATION: date_range
    
    This is similar to assert_no_partial_binding but more explicit about intent.
    
    Args:
        response: API response dictionary
        intent_name: Optional intent name (if not provided, extracted from response)
        
    Raises:
        AssertionError: If ready response lacks required bound fields
    """
    status = response.get("status")
    if status != STATUS_READY:
        return
    
    if intent_name is None:
        intent_name = response.get("intent", {}).get("name", "")
    
    slots = response.get("slots", {})
    
    if intent_name == "CREATE_APPOINTMENT":
        # Appointments with has_datetime=True must have datetime_range
        has_datetime = slots.get("has_datetime", False)
        if has_datetime:
            datetime_range = slots.get("datetime_range")
            assert datetime_range is not None, (
                f"INVARIANT: READY appointment with has_datetime=True must have datetime_range, "
                f"but got: {datetime_range}"
            )
    
    elif intent_name == "CREATE_RESERVATION":
        # Reservations must have date_range
        date_range = slots.get("date_range")
        assert date_range is not None, (
            f"INVARIANT: READY reservation must have date_range, but got: {date_range}"
        )


def assert_status_missing_slots_consistency(response: Dict[str, Any]) -> None:
    """
    Assert consistency between status and missing slots derived from issues.
    
    INVARIANT: 
    - READY status should have no missing slots (issues should be empty or only non-missing issues)
    - NEEDS_CLARIFICATION status should have at least one missing slot or clarification_data
    
    This helper ensures status and missing slots are consistent with each other.
    
    Args:
        response: API response dictionary
        
    Raises:
        AssertionError: If status and missing slots are inconsistent
    """
    status = response.get("status")
    issues = response.get("issues", {})
    
    # Derive missing slots from issues (consistent with test_luma.py logic)
    missing_slots = [
        slot for slot, issue in issues.items()
        if issue == "missing" or (isinstance(issue, dict) and issue.get("type") == "missing")
    ]
    
    if status == STATUS_READY:
        # READY should have no missing slots (or only non-missing issues like ambiguous_meridiem)
        # Allow ambiguous_meridiem issues (rich objects) but not "missing" string issues
        has_missing_string_issues = any(
            issue == "missing" for issue in issues.values()
        )
        assert not has_missing_string_issues, (
            f"INVARIANT VIOLATION: READY status should not have 'missing' string issues, "
            f"but found: {issues}"
        )
    
    elif status == STATUS_NEEDS_CLARIFICATION:
        # NEEDS_CLARIFICATION should have missing slots OR clarification_data OR any issues
        has_clarification_data = response.get("clarification_data") is not None
        has_missing_slots = len(missing_slots) > 0
        has_any_issue = len(issues) > 0  # Even non-missing issues (like ambiguous_meridiem)
        
        # Allow any form of clarification information
        # Note: We don't check clarification_reason to avoid depending on specific values
        assert has_missing_slots or has_clarification_data or has_any_issue, (
            f"INVARIANT VIOLATION: NEEDS_CLARIFICATION status should have some form of "
            f"clarification information (missing slots, clarification_data, or issues), "
            f"but got: issues={issues}, clarification_data={response.get('clarification_data')}"
        )


def assert_booking_block_consistency(response: Dict[str, Any], intent_name: Optional[str] = None) -> None:
    """
    Assert consistency of booking block presence/absence with status.
    
    INVARIANT (intent-specific):
    - READY status must have booking block ONLY for intents that produce_booking_payload
    - For MODIFY_BOOKING/CANCEL_BOOKING (produces_booking_payload=false), booking block is optional
    - NEEDS_CLARIFICATION status must NOT have booking block
    
    Args:
        response: API response dictionary
        intent_name: Optional intent name to check intent-specific semantics
        
    Raises:
        AssertionError: If booking block presence is inconsistent with status
    """
    status = response.get("status")
    booking = response.get("booking")
    
    # Determine if intent produces booking_payload (intent-specific semantics)
    produces_booking = False
    if intent_name:
        from luma.config.intent_meta import get_intent_registry
        registry = get_intent_registry()
        intent_meta = registry.get(intent_name)
        if intent_meta:
            produces_booking = intent_meta.produces_booking_payload is True
    
    if status == STATUS_READY:
        # Only require booking block for intents that produce it
        if produces_booking:
            assert booking is not None, (
                f"INVARIANT VIOLATION: READY status for {intent_name} (produces_booking_payload=true) "
                f"must have booking block, but got: {booking}"
            )
            # Booking should be minimal - only confirmation_state
            # (temporal/service data is in slots)
            assert "confirmation_state" in booking, (
                f"INVARIANT VIOLATION: booking block must contain confirmation_state, "
                f"but got: {booking}"
            )
        # For intents that don't produce booking_payload (MODIFY_BOOKING, CANCEL_BOOKING),
        # booking block is optional - no assertion needed
    
    elif status == STATUS_NEEDS_CLARIFICATION:
        assert booking is None, (
            f"INVARIANT VIOLATION: NEEDS_CLARIFICATION status must NOT have booking block, "
            f"but got: {booking}"
        )


def assert_invariants(response: Dict[str, Any], intent_name: Optional[str] = None) -> None:
    """
    Assert all invariants for a response.
    
    This is a convenience function that calls all invariant helpers.
    Use this when you want to check all invariants at once.
    
    Args:
        response: API response dictionary
        intent_name: Optional intent name (if not provided, extracted from response)
        
    Raises:
        AssertionError: If any invariant is violated
    """
    assert_no_partial_binding(response)
    assert_clarification_has_missing_slots(response)
    assert_ready_has_required_bound_fields(response, intent_name)
    assert_status_missing_slots_consistency(response)
    assert_booking_block_consistency(response, intent_name)

