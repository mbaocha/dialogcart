"""
Luma Service Contract Assertions

Strict contract validation for Luma API responses.
Fail fast on violations - no recovery, no fixing.
"""

from typing import Dict, Any

from core.errors.exceptions import ContractViolation


def assert_luma_contract(response: Dict[str, Any]) -> None:
    """
    Assert strict contract on Luma /resolve response.
    
    Contract rules:
    1. success=true ⇒ intent.name exists
    2. needs_clarification=true ⇒ clarification.reason exists
    3. needs_clarification=false ⇒ booking.booking_state == RESOLVED
    4. booking.booking_state == RESOLVED ⇒ booking.datetime_range.start exists
    
    Args:
        response: Luma API response dictionary
        
    Raises:
        ContractViolation: If any contract rule is violated
    """
    if not isinstance(response, dict):
        raise ContractViolation(
            f"Response must be a dict, got {type(response)}"
        )
    
    success = response.get("success", False)
    
    # Rule 1: success=true ⇒ intent.name exists
    if success:
        intent = response.get("intent")
        if not intent:
            raise ContractViolation(
                "Contract violation: success=true but intent is missing"
            )
        if not isinstance(intent, dict):
            raise ContractViolation(
                f"Contract violation: intent must be a dict, got {type(intent)}"
            )
        if "name" not in intent:
            raise ContractViolation(
                "Contract violation: success=true but intent.name is missing"
            )
    
    # Rule 2: needs_clarification=true ⇒ clarification.reason exists
    needs_clarification = response.get("needs_clarification", False)
    
    if needs_clarification:
        clarification = response.get("clarification")
        if not clarification:
            raise ContractViolation(
                "Contract violation: needs_clarification=true but clarification is missing"
            )
        if not isinstance(clarification, dict):
            raise ContractViolation(
                f"Contract violation: clarification must be a dict, got {type(clarification)}"
            )
        if "reason" not in clarification:
            raise ContractViolation(
                "Contract violation: needs_clarification=true but clarification.reason is missing"
            )
    
    # Rule 3: needs_clarification=false ⇒ booking.booking_state == RESOLVED
    booking = response.get("booking")
    if not needs_clarification and booking is not None:
        booking_state = booking.get("booking_state")
        if booking_state != "RESOLVED":
            raise ContractViolation(
                f"Contract violation: needs_clarification=false but "
                f"booking.booking_state={booking_state} (expected RESOLVED)"
            )
    
    # Rule 4: booking.booking_state == RESOLVED ⇒ booking.datetime_range.start exists
    if booking is not None:
        booking_state = booking.get("booking_state")
        if booking_state == "RESOLVED":
            datetime_range = booking.get("datetime_range")
            if datetime_range is None:
                raise ContractViolation(
                    "Contract violation: booking_state=RESOLVED but datetime_range is None"
                )
            if not isinstance(datetime_range, dict):
                raise ContractViolation(
                    f"Contract violation: datetime_range must be a dict, got {type(datetime_range)}"
                )
            if "start" not in datetime_range:
                raise ContractViolation(
                    "Contract violation: booking_state=RESOLVED but datetime_range.start is missing"
                )

