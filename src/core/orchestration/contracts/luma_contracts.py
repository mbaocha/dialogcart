"""
Orchestration Layer - Luma Contract Validation

Strict contract validation for Luma API responses.

This module validates the structure and content of Luma API responses
as part of the orchestration layer's contract enforcement.
Fail fast on violations - no recovery, no fixing.
"""

from typing import Any, Dict

from core.orchestration.errors import ContractViolation


def assert_luma_contract(response: Dict[str, Any]) -> None:
    """
    Assert strict contract on Luma /resolve response.

    FACT-ONLY CONTRACT: Only requires intent.name to exist.
    - intent.name MUST exist (required for planning)
    - facts MAY be empty or partial (missing facts are NOT errors)
    - Missing slots are NOT errors (planner computes missing_slots)
    - Legacy fields (success, status) are NOT required

    Only treat Luma as failed if:
    - intent is missing
    - response is unparsable (not a dict)
    - explicit error field is present

    Args:
        response: Luma API response dictionary

    Raises:
        ContractViolation: If intent.name is missing, response is malformed, or explicit error field is present
    """
    if not isinstance(response, dict):
        raise ContractViolation(f"Response must be a dict, got {type(response)}")

    # Check for explicit error field - if present, this is a Luma error
    if "error" in response:
        error_msg = response.get("error")
        error_message = response.get("message", str(error_msg))
        raise ContractViolation(f"Luma API returned explicit error: {error_message}")

    # ONLY REQUIRED FIELD: intent.name must exist for planning to proceed
    intent = response.get("intent")
    if not intent:
        raise ContractViolation(
            "Contract violation: intent is missing (required for planning)"
        )
    if not isinstance(intent, dict):
        raise ContractViolation(
            f"Contract violation: intent must be a dict, got {type(intent)}"
        )
    if "name" not in intent:
        raise ContractViolation(
            "Contract violation: intent.name is missing (required for planning)"
        )

    # facts is optional - empty or partial facts are valid
    # Missing slots are NOT errors - planner will compute missing_slots
    # Legacy fields (success, status, needs_clarification) are NOT required
