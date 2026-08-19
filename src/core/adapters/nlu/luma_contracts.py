"""
Luma NLU Contract Validation

Strict contract validation for Luma API responses.

This module validates the structure and content of Luma API responses
as part of the orchestration layer's contract enforcement.
Fail fast on violations - no recovery, no fixing.
"""

from typing import Any, Dict, Mapping, Optional

from core.adapters.errors import ContractViolation


def assert_luma_contract(
    response: Dict[str, Any],
    *,
    entity_schema: Optional[Mapping[str, Any]] = None,
) -> None:
    """
    Assert strict contract on Luma /resolve response.

    Base contract requires intent.name. Schema turns additionally require and
    strictly validate authoritative entity_resolutions.
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

    if entity_schema is not None:
        from core.adapters.nlu.entity_resolution_contract import (
            parse_entity_resolutions,
        )

        parse_entity_resolutions(response, entity_schema=entity_schema)

    category = response.get("service_category")
    if category is not None:
        if not isinstance(category, dict) or category.get("resolution") not in {
            "RESOLVED", "AMBIGUOUS", "UNRESOLVED"
        }:
            raise ContractViolation("Contract violation: malformed service_category")
        if category.get("resolution") == "RESOLVED":
            name = category.get("name")
            if not isinstance(name, str) or not name.strip():
                raise ContractViolation(
                    "Contract violation: resolved service_category requires name"
                )
        if category.get("resolution") == "AMBIGUOUS":
            candidates = category.get("candidate_values")
            if not isinstance(candidates, list) or len(candidates) < 2:
                raise ContractViolation(
                    "Contract violation: ambiguous service_category requires candidates"
                )
    selection = response.get("catalog_selection")
    if selection is not None:
        if not isinstance(selection, dict):
            raise ContractViolation("Contract violation: catalog_selection must be an object")
        if selection.get("kind") not in {"category", "service"}:
            raise ContractViolation("Contract violation: invalid catalog_selection.kind")
        if not isinstance(selection.get("presentation_ref"), str):
            raise ContractViolation(
                "Contract violation: catalog_selection requires presentation_ref"
            )
        option = selection.get("option")
        if isinstance(option, bool) or not isinstance(option, int) or option < 1:
            raise ContractViolation("Contract violation: catalog_selection.option must be positive integer")

    temporal = response.get("temporal")
    if isinstance(temporal, dict) and temporal.get("resolution") is not None:
        resolution = temporal.get("resolution")
        if not isinstance(resolution, dict):
            raise ContractViolation("Contract violation: temporal.resolution must be an object")
        kind = resolution.get("kind")
        allowed = {"explicit", "ambiguous_meridiem", "presented_option", "invalid_option_reference"}
        if kind not in allowed:
            raise ContractViolation("Contract violation: invalid temporal.resolution.kind")
        start_time = temporal.get("start_time")
        if kind == "explicit":
            if not start_time or resolution.get("presentation_ref") or resolution.get("option") is not None:
                raise ContractViolation("Contract violation: malformed explicit temporal resolution")
        elif start_time is not None:
            raise ContractViolation("Contract violation: non-explicit temporal resolution cannot carry start_time")
        if kind == "presented_option":
            if not resolution.get("presentation_ref") or isinstance(resolution.get("option"), bool):
                raise ContractViolation("Contract violation: malformed presented option reference")
            try:
                option = int(resolution.get("option"))
            except (TypeError, ValueError) as exc:
                raise ContractViolation("Contract violation: presented option must be an integer") from exc
            if option < 1:
                raise ContractViolation("Contract violation: presented option must be positive")

    # facts is optional - empty or partial facts are valid
    # Missing slots are NOT errors - planner will compute missing_slots
    # Legacy fields (success, status, needs_clarification) are NOT required
