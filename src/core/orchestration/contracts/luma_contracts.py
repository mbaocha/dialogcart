"""
Orchestration Layer - Luma Contract Validation

Strict contract validation for Luma API responses.

This module validates the structure and content of Luma API responses
as part of the orchestration layer's contract enforcement.
Fail fast on violations - no recovery, no fixing.
"""

from typing import Dict, Any

from core.orchestration.errors import ContractViolation


def assert_luma_contract(response: Dict[str, Any]) -> None:
    """
    Assert strict contract on Luma /resolve response.
    
    Contract rules:
    1. success=true ⇒ intent.name exists
    2. needs_clarification=true ⇒ clarification_reason exists (string)
    3. needs_clarification=false ⇒ booking block may exist (format depends on intent)
    
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
    
    # Rule 2: needs_clarification=true ⇒ clarification_reason exists
    needs_clarification = response.get("needs_clarification", False)
    
    if needs_clarification:
        clarification_reason = response.get("clarification_reason")
        if not clarification_reason:
            raise ContractViolation(
                "Contract violation: needs_clarification=true but clarification_reason is missing"
            )
        if not isinstance(clarification_reason, str):
            raise ContractViolation(
                f"Contract violation: clarification_reason must be a string, got {type(clarification_reason)}"
            )
    
    # Rule 3: For resolved bookings (needs_clarification=false), booking structure is validated
    # by downstream code. The contract only validates the top-level structure here.

