"""
EXECUTION_TRACE Field Classification

This module classifies fields in the EXECUTION_TRACE log as either:
- STABLE: API contract fields that must never change without versioning
- DEBUG: Internal diagnostic fields that may evolve or be removed

This classification is for documentation and future contract enforcement only.
It does not alter runtime behavior or log output.

IMPORTANT:
- Stable fields require versioning to change
- Debug fields may change without notice

The EXECUTION_TRACE log is emitted by the /resolve API endpoint and contains:
- Top-level fields: request_id, input, sentence_trace, trace, final_response, processing_time_ms
- Nested trace fields: entity, semantic, decision, binder, response

See the FIELD_CLASSIFICATION dictionary for complete field-by-field classification.
"""

from typing import Set, Dict, Any, Optional

from ..config.core import STATUS_READY, STATUS_NEEDS_CLARIFICATION

# Trace version - increment when stable fields change
TRACE_VERSION = "1.0"

# Top-level EXECUTION_TRACE fields
STABLE_TOP_LEVEL_FIELDS: Set[str] = {
    "request_id",      # Used for request correlation across systems
    "input",           # Original request payload (audit trail)
    "final_response",  # Final API response shape (matches /resolve response)
}

DEBUG_TOP_LEVEL_FIELDS: Set[str] = {
    "sentence_trace",      # Internal: sentence evolution through pipeline
    "processing_time_ms",  # Internal: performance metrics
    "trace",               # Internal: detailed execution trace (see below)
}

# Fields inside trace.entity
DEBUG_ENTITY_FIELDS: Set[str] = {
    "service_ids",  # Internal: extracted service identifiers
    "dates",        # Internal: extracted date strings
    "times",        # Internal: extracted time strings
}

# Fields inside trace.semantic
STABLE_SEMANTIC_FIELDS: Set[str] = {
    "service_ids",        # Stable: resolved service identifiers
    "date_mode",         # Stable: date resolution mode
    "date_refs",         # Stable: resolved date references
    "time_mode",         # Stable: time resolution mode
    "time_refs",         # Stable: resolved time references
    "time_constraint",   # Stable: time constraint if present
    "needs_clarification",  # Stable: clarification flag
    "clarification_reason",  # Stable: reason for clarification
}

DEBUG_SEMANTIC_FIELDS: Set[str] = {
    "time_extraction_trace",  # Internal: detailed time extraction diagnostics
    "time_issues",            # Internal: time parsing issues (may be in resolved_booking)
}

# Fields inside trace.decision
STABLE_DECISION_FIELDS: Set[str] = {
    "state",                    # Stable: RESOLVED or NEEDS_CLARIFICATION
    "reason",                    # Stable: clarification reason if applicable
    "missing_slots",             # Stable: list of missing required slots
    "expected_temporal_shape",   # Stable: required temporal shape for intent
    "actual_temporal_shape",    # Stable: actual temporal shape derived
    "temporal_shape_satisfied",  # Stable: whether temporal requirements met
}

DEBUG_DECISION_FIELDS: Set[str] = {
    "rule_enforced",              # Internal: which rule was applied
    "temporal_shape_derivation",  # Internal: how temporal shape was computed
}

# Fields inside trace.binder
DEBUG_BINDER_FIELDS: Set[str] = {
    "called",          # Internal: whether calendar binding was executed
    "input",           # Internal: input to calendar binder
    "output",          # Internal: output from calendar binder
    "decision_reason", # Internal: why binder was called or skipped
}

# Fields inside trace.response
STABLE_RESPONSE_FIELDS: Set[str] = {
    "status",           # Stable: ready or needs_clarification
    "intent",           # Stable: resolved intent name
    "issues",           # Stable: issues requiring clarification
    "has_booking",      # Stable: whether booking payload exists
    "has_clarification",  # Stable: whether clarification is needed
}

# Complete field classification by path
# Format: "path.to.field" -> classification
FIELD_CLASSIFICATION: Dict[str, str] = {
    # Top-level
    "request_id": "STABLE",
    "input": "STABLE",
    "final_response": "STABLE",
    "sentence_trace": "DEBUG",
    "processing_time_ms": "DEBUG",
    "trace": "DEBUG",
    
    # trace.entity.*
    "trace.entity": "DEBUG",
    "trace.entity.service_ids": "DEBUG",
    "trace.entity.dates": "DEBUG",
    "trace.entity.times": "DEBUG",
    
    # trace.semantic.*
    "trace.semantic": "DEBUG",
    "trace.semantic.service_ids": "STABLE",
    "trace.semantic.date_mode": "STABLE",
    "trace.semantic.date_refs": "STABLE",
    "trace.semantic.time_mode": "STABLE",
    "trace.semantic.time_refs": "STABLE",
    "trace.semantic.time_constraint": "STABLE",
    "trace.semantic.needs_clarification": "STABLE",
    "trace.semantic.clarification_reason": "STABLE",
    "trace.semantic.time_extraction_trace": "DEBUG",
    "trace.semantic.time_issues": "DEBUG",
    
    # trace.decision.*
    "trace.decision": "DEBUG",
    "trace.decision.state": "STABLE",
    "trace.decision.reason": "STABLE",
    "trace.decision.missing_slots": "STABLE",
    "trace.decision.expected_temporal_shape": "STABLE",
    "trace.decision.actual_temporal_shape": "STABLE",
    "trace.decision.temporal_shape_satisfied": "STABLE",
    "trace.decision.rule_enforced": "DEBUG",
    "trace.decision.temporal_shape_derivation": "DEBUG",
    
    # trace.binder.*
    "trace.binder": "DEBUG",
    "trace.binder.called": "DEBUG",
    "trace.binder.input": "DEBUG",
    "trace.binder.output": "DEBUG",
    "trace.binder.decision_reason": "DEBUG",
    
    # trace.response.*
    "trace.response": "DEBUG",
    "trace.response.status": "STABLE",
    "trace.response.intent": "STABLE",
    "trace.response.issues": "STABLE",
    "trace.response.has_booking": "STABLE",
    "trace.response.has_clarification": "STABLE",
}

# Documentation strings
STABLE_FIELDS_DOC = """
Stable fields are part of the API contract and must never change without versioning.
These fields are relied upon by downstream systems for:
- Request correlation (request_id)
- Audit trails (input)
- Response processing (final_response, trace.response.*)
- Business logic (trace.semantic.*, trace.decision.state/reason/missing_slots)
"""

DEBUG_FIELDS_DOC = """
Debug fields are intended only for internal diagnostics and may change without notice.
These fields help with:
- Performance monitoring (processing_time_ms)
- Pipeline debugging (sentence_trace, trace.entity.*, trace.binder.*)
- Internal diagnostics (trace.*.rule_enforced, trace.*.temporal_shape_derivation)
- Detailed extraction traces (trace.semantic.time_extraction_trace)
"""


def validate_stable_fields(
    execution_trace_data: Dict[str, Any],
    debug_mode: bool = False
) -> Optional[str]:
    """
    Validate that all stable fields are present and have expected types.
    
    This function only validates STABLE fields. DEBUG fields are not validated
    and may change freely.
    
    Args:
        execution_trace_data: Dictionary containing the EXECUTION_TRACE log data
                            Must contain: request_id, input, trace, final_response
        debug_mode: If True, raises AssertionError on validation failure.
                   If False, returns error message string or None.
    
    Returns:
        None if validation passes, error message string if validation fails (debug_mode=False)
    
    Raises:
        AssertionError: If validation fails and debug_mode=True
    """
    errors = []
    
    # Validate top-level stable fields
    if "request_id" not in execution_trace_data:
        errors.append("Missing stable field: request_id")
    elif not isinstance(execution_trace_data["request_id"], str):
        errors.append("Stable field 'request_id' must be a string")
    
    if "input" not in execution_trace_data:
        errors.append("Missing stable field: input")
    elif not isinstance(execution_trace_data["input"], dict):
        errors.append("Stable field 'input' must be a dict")
    
    if "final_response" not in execution_trace_data:
        errors.append("Missing stable field: final_response")
    elif not isinstance(execution_trace_data["final_response"], dict):
        errors.append("Stable field 'final_response' must be a dict")
    
    # Validate trace structure
    if "trace" not in execution_trace_data:
        errors.append("Missing stable field: trace")
        return _format_errors(errors, debug_mode)
    
    trace = execution_trace_data["trace"]
    if not isinstance(trace, dict):
        errors.append("Stable field 'trace' must be a dict")
        return _format_errors(errors, debug_mode)
    
    # Validate trace.response stable fields
    if "response" in trace:
        response = trace["response"]
        if isinstance(response, dict):
            for field in STABLE_RESPONSE_FIELDS:
                if field not in response:
                    errors.append(f"Missing stable field: trace.response.{field}")
                else:
                    # Type validation for specific fields
                    value = response[field]
                    if field == "status" and value not in (STATUS_READY, STATUS_NEEDS_CLARIFICATION):
                        errors.append(f"Stable field 'trace.response.status' must be '{STATUS_READY}' or '{STATUS_NEEDS_CLARIFICATION}', got '{value}'")
                    elif field == "intent" and not isinstance(value, str):
                        errors.append(f"Stable field 'trace.response.intent' must be a string")
                    elif field == "issues" and not isinstance(value, dict):
                        errors.append(f"Stable field 'trace.response.issues' must be a dict")
                    elif field in ("has_booking", "has_clarification") and not isinstance(value, bool):
                        errors.append(f"Stable field 'trace.response.{field}' must be a bool")
    
    # Validate trace.semantic stable fields
    if "semantic" in trace:
        semantic = trace["semantic"]
        if isinstance(semantic, dict):
            for field in STABLE_SEMANTIC_FIELDS:
                if field not in semantic:
                    errors.append(f"Missing stable field: trace.semantic.{field}")
                else:
                    # Type validation for specific fields
                    value = semantic[field]
                    if field == "service_ids" and not isinstance(value, list):
                        errors.append(f"Stable field 'trace.semantic.service_ids' must be a list")
                    elif field in ("date_mode", "time_mode") and not isinstance(value, str):
                        errors.append(f"Stable field 'trace.semantic.{field}' must be a string")
                    elif field in ("date_refs", "time_refs") and not isinstance(value, list):
                        errors.append(f"Stable field 'trace.semantic.{field}' must be a list")
                    elif field == "needs_clarification" and not isinstance(value, bool):
                        errors.append(f"Stable field 'trace.semantic.needs_clarification' must be a bool")
                    elif field == "clarification_reason" and value is not None and not isinstance(value, str):
                        errors.append(f"Stable field 'trace.semantic.clarification_reason' must be a string or None")
                    # time_constraint is optional (may be None)
                    elif field == "time_constraint" and value is not None and not isinstance(value, dict):
                        errors.append(f"Stable field 'trace.semantic.time_constraint' must be a dict or None")
    
    # Validate trace.decision stable fields
    if "decision" in trace:
        decision = trace["decision"]
        if isinstance(decision, dict):
            for field in STABLE_DECISION_FIELDS:
                if field not in decision:
                    errors.append(f"Missing stable field: trace.decision.{field}")
                else:
                    # Type validation for specific fields
                    value = decision[field]
                    if field == "state" and value not in ("RESOLVED", "NEEDS_CLARIFICATION"):
                        errors.append(f"Stable field 'trace.decision.state' must be 'RESOLVED' or 'NEEDS_CLARIFICATION', got '{value}'")
                    elif field == "reason" and value is not None and not isinstance(value, str):
                        errors.append(f"Stable field 'trace.decision.reason' must be a string or None")
                    elif field == "missing_slots" and not isinstance(value, list):
                        errors.append(f"Stable field 'trace.decision.missing_slots' must be a list")
                    elif field in ("expected_temporal_shape", "actual_temporal_shape") and value is not None and not isinstance(value, str):
                        errors.append(f"Stable field 'trace.decision.{field}' must be a string or None")
                    elif field == "temporal_shape_satisfied" and not isinstance(value, bool):
                        errors.append(f"Stable field 'trace.decision.temporal_shape_satisfied' must be a bool")
    
    return _format_errors(errors, debug_mode)


def _format_errors(errors: list, debug_mode: bool) -> Optional[str]:
    """Format errors and either raise or return error message."""
    if not errors:
        return None
    
    error_msg = f"EXECUTION_TRACE stable field validation failed (version {TRACE_VERSION}):\n" + "\n".join(f"  - {e}" for e in errors)
    
    if debug_mode:
        raise AssertionError(error_msg)
    
    return error_msg

