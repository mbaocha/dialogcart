"""
Response Builder

Centralized response building logic for Luma API.
Handles formatting, filtering, and structuring of API responses.

This module ensures response object construction is separated from orchestration logic.
"""

import logging
from typing import Dict, Any, Optional, List

from ..trace import log_field_removal
from ..config import config

logger = logging.getLogger(__name__)


def format_service_for_response(service: Dict[str, Any]) -> Dict[str, Any]:
    """
    Format a service dict for API response, preserving resolved alias if present.

    When a tenant alias was explicitly matched, use it for the text field.
    Otherwise, use the original text field.

    Args:
        service: Service dict with text, canonical, and optionally resolved_alias

    Returns:
        Formatted service dict with text and canonical
    """
    # If resolved_alias exists (from explicit tenant alias match), use it
    resolved_alias = service.get("resolved_alias")
    if resolved_alias:
        return {
            "text": resolved_alias,
            "canonical": service.get("canonical", "")
        }

    # Otherwise, use existing text field
    return {
        "text": service.get("text", ""),
        "canonical": service.get("canonical", "")
    }


def build_issues(
    missing_slots: List[str],
    time_issues: Optional[List[Dict[str, Any]]] = None
) -> Dict[str, Any]:
    """
    Build issues object from missing_slots and time_issues.

    Args:
        missing_slots: List of missing slot names (e.g., ["time", "date"])
        time_issues: Optional list of time-related issues from semantic resolution

    Returns:
        Issues object with structure:
        {
            "time": {"raw": "...", "start_hour": 2, "end_hour": 5, "candidates": ["am", "pm"]} or "missing",
            "date": "missing"
        }
    """
    issues: Dict[str, Any] = {}

    # Handle missing slots (simple string)
    for slot in missing_slots:
        if slot not in issues:
            issues[slot] = "missing"

    # Handle time issues (rich object with details - simplified, no type/kind)
    if time_issues:
        for issue in time_issues:
            issue_kind = issue.get("kind")
            if issue_kind == "ambiguous_meridiem":
                # Override "missing" with rich ambiguous object (data only, no classification)
                issues["time"] = {
                    "raw": issue.get("raw"),
                    "start_hour": issue.get("start_hour"),
                    "end_hour": issue.get("end_hour"),
                    "candidates": issue.get("candidates", [])
                }

    return issues


class ResponseBuilder:
    """
    Response builder for Luma API responses.

    Centralizes all response formatting and construction logic to separate
    concerns from orchestration code.
    """

    def format_booking_payload(
        self,
        booking_payload: Dict[str, Any],
        intent_name: str,
        calendar_booking: Dict[str, Any],
        request_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Format booking payload for API response based on intent type.

        Applies intent-specific field filtering and normalization:
        - CREATE_APPOINTMENT: only datetime_range, removes date/time/date_range/time_range
        - CREATE_RESERVATION: only date_range, removes datetime_range/date/time/time_range

        Args:
            booking_payload: Raw booking payload to format
            intent_name: Intent name (CREATE_APPOINTMENT or CREATE_RESERVATION)
            calendar_booking: Calendar binding result
            request_id: Optional request ID for logging

        Returns:
            Formatted booking payload with only intent-appropriate fields
        """
        # Normalize services to public canonical form
        if booking_payload.get("services"):
            booking_payload["services"] = [
                format_service_for_response(s)
                for s in booking_payload.get("services", [])
                if isinstance(s, dict)
            ]

        formatted = booking_payload.copy()

        # Appointment responses: only datetime_range
        if intent_name == "CREATE_APPOINTMENT":
            # Always copy datetime_range from calendar_booking if present
            # This comes from calendar binding and should override any memory values
            datetime_range_from_binder = calendar_booking.get(
                "datetime_range") if calendar_booking else None
            if datetime_range_from_binder:
                formatted["datetime_range"] = datetime_range_from_binder

            # Slot tracking: log field removals for CREATE_APPOINTMENT
            fields_to_remove = ["date", "time", "date_range",
                                "time_range", "start_date", "end_date"]
            for field in fields_to_remove:
                if field in formatted:
                    if config.LOG_SLOT_TRACKING:
                        log_field_removal(
                            "response_building",
                            field,
                            formatted.get(field),
                            context={"intent": "CREATE_APPOINTMENT"},
                            request_id=request_id,
                            enabled=config.LOG_SLOT_TRACKING
                        )
                    formatted.pop(field, None)

        # Reservation responses: only date_range (for internal booking_payload)
        elif intent_name == "CREATE_RESERVATION":
            # Build date_range from calendar_booking if available
            if "start_date" in calendar_booking and "end_date" in calendar_booking:
                formatted["date_range"] = {
                    "start": calendar_booking["start_date"],
                    "end": calendar_booking["end_date"]
                }

            # Clean up legacy fields
            # Slot tracking: log field removals for CREATE_RESERVATION
            fields_to_remove = ["datetime_range", "date",
                                "time", "time_range", "start_date", "end_date"]
            for field in fields_to_remove:
                if field in formatted:
                    if config.LOG_SLOT_TRACKING:
                        log_field_removal(
                            "response_building",
                            field,
                            formatted.get(field),
                            context={"intent": "CREATE_RESERVATION"},
                            request_id=request_id,
                            enabled=config.LOG_SLOT_TRACKING
                        )
                    formatted.pop(field, None)

        # Remove legacy booking_state from response payloads
        if "booking_state" in formatted:
            if config.LOG_SLOT_TRACKING:
                log_field_removal(
                    "response_building",
                    "booking_state",
                    formatted.get("booking_state"),
                    context={"intent": intent_name},
                    request_id=request_id,
                    enabled=config.LOG_SLOT_TRACKING
                )
            formatted.pop("booking_state", None)

        return formatted

    def build_response_body(
        self,
        intent_payload: Dict[str, Any],
        needs_clarification: bool,
        clarification_reason: Optional[str],
        issues: Dict[str, Any],
        booking_payload: Optional[Dict[str, Any]] = None,
        entities_payload: Optional[Dict[str, Any]] = None,
        slots: Optional[Dict[str, Any]] = None,
        context_payload: Optional[Dict[str, Any]] = None,
        debug_data: Optional[Dict[str, Any]] = None,
        request_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Build the final API response body.

        Args:
            intent_payload: Intent payload dict
            needs_clarification: Whether clarification is needed
            clarification_reason: Reason for clarification (if needed)
            issues: Issues dict (missing slots, time issues, etc.)
            booking_payload: Optional formatted booking payload
            entities_payload: Optional entities payload (for non-booking intents)
            slots: Optional slots dict
            context_payload: Optional context payload (for clarification cases)
            debug_data: Optional debug data (pipeline results)
            request_id: Optional request ID for logging

        Returns:
            Complete API response body dict
        """
        response_body = {
            "success": True,
            "intent": intent_payload,
            "status": "needs_clarification" if needs_clarification else "ready",
            "issues": issues if issues else {},
            "clarification_reason": clarification_reason if needs_clarification else None,
            "needs_clarification": needs_clarification,
        }

        # Include slots if it has any content (for both ready and clarification)
        if slots:
            response_body["slots"] = slots

        # Handle clarification vs ready cases
        if needs_clarification:
            # For needs_clarification: include context but NO booking block
            if context_payload:
                response_body["context"] = context_payload
        else:
            # For ready status: include booking block (minimal, only confirmation_state)
            if booking_payload is not None:
                # Attach confirmation_state for ready bookings
                booking_payload["confirmation_state"] = "pending"

                # Build minimal booking block (temporal and service data is in slots)
                # Remove all fields that are exposed via slots
                booking_payload_copy = booking_payload.copy()
                # Exposed via slots.service_id
                booking_payload_copy.pop("services", None)
                # Exposed via slots.date_range
                booking_payload_copy.pop("date_range", None)
                # Exposed via slots.datetime_range
                booking_payload_copy.pop("datetime_range", None)
                booking_payload_copy.pop("duration", None)  # Removed entirely
                booking_payload_copy.pop("start_date", None)  # Legacy field
                booking_payload_copy.pop("end_date", None)  # Legacy field

                response_body["booking"] = booking_payload_copy

        # Add entities field for non-booking intents (always include, even if empty)
        if entities_payload is not None:
            response_body["entities"] = entities_payload

        # Attach full internal pipeline data only in debug mode
        if debug_data:
            response_body["debug"] = debug_data

        return response_body
