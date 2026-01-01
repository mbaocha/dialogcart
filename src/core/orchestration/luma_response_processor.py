"""
Luma Response Processor

Interprets Luma API responses and decides CLARIFY vs EXECUTE.

This module is pure and side-effect free:
- No external API calls
- No state mutation
- Deterministic interpretation of Luma responses

Responsibilities:
- Clarification interpretation (reason, issues, context)
- CLARIFY outcome construction
- Intent extraction and validation
- Building structured execution instructions
"""

import logging
from typing import Dict, Any, Optional

from core.routing import get_template_key, get_action_name
from core.orchestration.errors import UnsupportedIntentError
from luma.clarification.reasons import ClarificationReason

logger = logging.getLogger(__name__)


def _extract_clarification_data(
    clarification_reason: Optional[str],
    issues: Dict[str, Any],
    clarification_data: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Extract structured clarification data from Luma response.

    Builds a structured data object with:
    - reason: Stable enum-like value (e.g., "MISSING_TIME")
    - missing: Explicit list of required fields (e.g., ["time"])
    - Additional fields from clarification_data (e.g., "options" for MULTIPLE_MATCHES)

    This function extracts semantic cause of clarification and populates
    data.reason and data.missing. This is the single, authoritative source
    for clarification semantics.

    Args:
        clarification_reason: Clarification reason string from Luma
        issues: Issues dict from Luma (contains missing slots, time issues, etc.)
        clarification_data: Optional structured clarification data from Luma (e.g., options for MULTIPLE_MATCHES)

    Returns:
        Dictionary with 'reason' and 'missing' fields (both always present), plus any additional fields from clarification_data
    """
    # Extract reason (use clarification_reason if present and non-empty, otherwise infer from issues)
    reason = clarification_reason if clarification_reason and clarification_reason.strip() else None

    # Extract missing fields from issues dict
    # Issues structure: {slot_name: "missing"} or {slot_name: "ambiguous"} or {slot_name: {...}} for rich issues
    missing = []
    if isinstance(issues, dict):
        for slot_name, slot_value in issues.items():
            # If value is "missing", add to missing list
            if slot_value == "missing":
                missing.append(slot_name)
            # If value is "ambiguous", also add to missing list (needs clarification)
            elif slot_value == "ambiguous":
                missing.append(slot_name)
            # For rich time issues, still consider "time" as missing if present
            elif slot_name == "time" and isinstance(slot_value, dict):
                # Time has ambiguity but is still missing resolution
                missing.append("time")

    # For UNSUPPORTED_SERVICE, ensure "service" is in missing if not already there
    # Note: MULTIPLE_MATCHES is now handled in Luma (issues.service = "ambiguous"),
    # so it will be extracted from issues above. Only UNSUPPORTED_SERVICE needs fallback handling here.
    if reason == ClarificationReason.UNSUPPORTED_SERVICE.value:
        if "service" not in missing and "service_id" not in missing:
            missing.append("service")

    # If no reason provided but we have missing slots, infer reason
    if not reason and missing:
        if "time" in missing:
            reason = ClarificationReason.MISSING_TIME.value
        elif "date" in missing:
            reason = ClarificationReason.MISSING_DATE.value
        elif "service" in missing or "service_id" in missing:
            reason = ClarificationReason.MISSING_SERVICE.value
        else:
            reason = ClarificationReason.MISSING_CONTEXT.value  # Generic fallback

    # Build structured data object - always include both fields
    # reason defaults to MISSING_CONTEXT if not provided and cannot be inferred
    if not reason:
        reason = ClarificationReason.MISSING_CONTEXT.value

    # missing defaults to empty list if none found
    if not missing:
        missing = []

    # Start with reason and missing (always present)
    data = {
        "reason": reason,
        "missing": missing
    }

    # Merge additional fields from clarification_data (e.g., options for MULTIPLE_MATCHES)
    if clarification_data and isinstance(clarification_data, dict):
        # Merge clarification_data into data (e.g., options for MULTIPLE_MATCHES)
        # But preserve reason and missing as authoritative
        # Note: service_family is not included here - use context.services[0].canonical instead
        for key, value in clarification_data.items():
            if key not in ("reason", "missing"):  # Don't override reason/missing
                data[key] = value

    return data


def _build_clarify_outcome(
    clarification_reason: str,
    issues: Dict[str, Any],
    context: Dict[str, Any],
    booking: Optional[Dict[str, Any]],
    domain: str,
    clarification_data: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Build a CLARIFY outcome from Luma response data.

    Args:
        clarification_reason: Clarification reason string from Luma
        issues: Issues dict from Luma
        context: Context dict from Luma
        booking: Optional booking payload from Luma
        domain: Domain for template key routing
        clarification_data: Optional structured clarification data from Luma (e.g., options for MULTIPLE_MATCHES)

    Returns:
        Complete CLARIFY outcome dictionary ready to return
    """
    template_key = get_template_key(clarification_reason, domain)

    logger.info(
        f"Clarification needed: {clarification_reason} -> {template_key}"
    )

    # Extract structured clarification data (reason and missing fields)
    # This is the single, authoritative source for clarification semantics
    data = _extract_clarification_data(
        clarification_reason, issues, clarification_data)

    return {
        "success": True,
        "outcome": {
            "type": "CLARIFY",
            "template_key": template_key,
            # data contains reason, missing, and any additional fields from clarification_data (e.g., options)
            "data": data,
            "context": context,  # Include context if present
            "booking": booking
        }
    }


def process_luma_response(
    luma_response: Dict[str, Any],
    domain: str,
    user_id: str
) -> Dict[str, Any]:
    """
    Process Luma response and decide CLARIFY vs EXECUTE.

    This function interprets the Luma response and returns either:
    - A ready-to-return CLARIFY outcome, or
    - A structured execution instruction (intent, action, booking payload)

    Args:
        luma_response: Validated Luma API response
        domain: Domain for template key routing
        user_id: User identifier for logging

    Returns:
        Either:
        - {"type": "CLARIFY", "outcome": {...}} - ready to return
        - {"type": "EXECUTE", "intent_name": str, "action_name": str, "booking": dict}
        - {"type": "ERROR", "error": str, "message": str} - unsupported intent

    Raises:
        UnsupportedIntentError: If intent is not supported (wrapped in error response)
    """
    # Check if Luma indicates clarification is needed
    if luma_response.get("needs_clarification", False):
        reason = luma_response.get("clarification_reason", "")
        issues = luma_response.get("issues", {})
        context = luma_response.get("context", {})
        booking = luma_response.get("booking")
        clarification_data = luma_response.get("clarification_data")

        return {
            "type": "CLARIFY",
            "outcome": _build_clarify_outcome(
                clarification_reason=reason,
                issues=issues,
                context=context,
                booking=booking,
                domain=domain,
                clarification_data=clarification_data
            )
        }

    # Extract intent and validate
    intent = luma_response.get("intent", {})
    intent_name = intent.get("name", "")
    action_name = get_action_name(intent_name)

    if not action_name:
        logger.warning(f"Unsupported intent for user {user_id}: {intent_name}")
        return {
            "type": "ERROR",
            "error": "unsupported_intent",
            "message": f"Intent {intent_name} is not supported"
        }

    # Return execution instruction
    booking = luma_response.get("booking", {})
    return {
        "type": "EXECUTE",
        "intent_name": intent_name,
        "action_name": action_name,
        "booking": booking
    }


def build_clarify_outcome_from_reason(
    reason: str,
    issues: Dict[str, Any],
    booking: Optional[Dict[str, Any]],
    domain: str
) -> Dict[str, Any]:
    """
    Build a CLARIFY outcome from a core-initiated clarification reason.

    Used when orchestrator detects clarification needs during service/room/extras resolution.

    Args:
        reason: Clarification reason (e.g., "MISSING_SERVICE")
        issues: Issues dict (e.g., {"service": "missing"})
        booking: Optional booking payload
        domain: Domain for template key routing

    Returns:
        Complete CLARIFY outcome dictionary ready to return
    """
    template_key = get_template_key(reason, domain)

    # Extract structured clarification data
    clarification_data = _extract_clarification_data(reason, issues)

    return {
        "success": True,
        "outcome": {
            "type": "CLARIFY",
            "template_key": template_key,
            "data": clarification_data,
            "booking": booking
        }
    }
