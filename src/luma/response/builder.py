"""
Response Builder

Centralized response building logic for Luma API.
Handles formatting, filtering, and structuring of API responses.

This module ensures response object construction is separated from orchestration logic.
"""

import logging
from typing import Any, Dict, List, Optional

from ..config import config
from ..config.core import STATUS_NEEDS_CLARIFICATION, STATUS_READY
from ..trace import log_field_removal

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
        return {"text": resolved_alias, "canonical": service.get("canonical", "")}

    # Otherwise, use existing text field
    return {"text": service.get("text", ""), "canonical": service.get("canonical", "")}


def build_issues(
    missing_slots: List[str], time_issues: Optional[List[Dict[str, Any]]] = None
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
                    "candidates": issue.get("candidates", []),
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
        request_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Format booking payload for API response based on intent type.

        Applies intent-specific field filtering and normalization:
        - CREATE_APPOINTMENT: only datetime_range, removes date/time/date_range/time_range
        - CREATE_RESERVATION: only date_range, removes datetime_range/date/time/time_range

        HARD INVARIANT: MODIFY_BOOKING and CANCEL_BOOKING must NEVER produce booking_payload.
        This function should only be called for CREATE_APPOINTMENT and CREATE_RESERVATION.

        Args:
            booking_payload: Raw booking payload to format
            intent_name: Intent name (CREATE_APPOINTMENT or CREATE_RESERVATION)
            calendar_booking: Calendar binding result
            request_id: Optional request ID for logging

        Returns:
            Formatted booking payload with only intent-appropriate fields
        """
        # HARD INVARIANT: MODIFY_BOOKING and CANCEL_BOOKING never produce booking_payload
        if intent_name in {"MODIFY_BOOKING", "CANCEL_BOOKING"}:
            logger.error(
                f"INVARIANT VIOLATION: format_booking_payload called for {intent_name}",
                extra={"request_id": request_id, "intent_name": intent_name},
            )
            raise ValueError(
                f"format_booking_payload must not be called for {intent_name}"
            )
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
            # This comes from calendar binding (authoritative for this request)
            datetime_range_from_binder = (
                calendar_booking.get("datetime_range") if calendar_booking else None
            )
            if datetime_range_from_binder:
                formatted["datetime_range"] = datetime_range_from_binder

            # Slot tracking: log field removals for CREATE_APPOINTMENT
            fields_to_remove = [
                "date",
                "time",
                "date_range",
                "time_range",
                "start_date",
                "end_date",
            ]
            for field in fields_to_remove:
                if field in formatted:
                    if config.LOG_SLOT_TRACKING:
                        log_field_removal(
                            "response_building",
                            field,
                            formatted.get(field),
                            context={"intent": "CREATE_APPOINTMENT"},
                            request_id=request_id,
                            enabled=config.LOG_SLOT_TRACKING,
                        )
                    formatted.pop(field, None)

        # Reservation responses: only date_range (for internal booking_payload)
        elif intent_name == "CREATE_RESERVATION":
            # Build date_range from calendar_booking if available
            if "start_date" in calendar_booking and "end_date" in calendar_booking:
                formatted["date_range"] = {
                    "start": calendar_booking["start_date"],
                    "end": calendar_booking["end_date"],
                }

            # Clean up legacy fields
            # Slot tracking: log field removals for CREATE_RESERVATION
            fields_to_remove = [
                "datetime_range",
                "date",
                "time",
                "time_range",
                "start_date",
                "end_date",
            ]
            for field in fields_to_remove:
                if field in formatted:
                    if config.LOG_SLOT_TRACKING:
                        log_field_removal(
                            "response_building",
                            field,
                            formatted.get(field),
                            context={"intent": "CREATE_RESERVATION"},
                            request_id=request_id,
                            enabled=config.LOG_SLOT_TRACKING,
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
                    enabled=config.LOG_SLOT_TRACKING,
                )
            formatted.pop("booking_state", None)

        return formatted

    def build_response_body(
        self,
        intent_payload: Dict[str, Any],
        facts: Optional[Dict[str, Any]] = None,
        debug_data: Optional[Dict[str, Any]] = None,
        operation: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Build the final API response body from extracted facts and intent only.

        Response is built ONLY from:
        - intent (name + confidence)
        - operation (optional availability interaction, e.g. browse_next)
        - facts (dates, times, date_time_pairs, service_id, booking_id)

        Does NOT use:
        - status
        - issues
        - has_booking
        - has_clarification
        - decision layer output

        Args:
            intent_payload: Intent payload dict with name and confidence
            facts: Optional facts dict (dates[], times[], date_time_pairs[], service_id, booking_id)
            debug_data: Optional debug data (pipeline results)
            operation: Optional availability operation (browse_next, browse_previous)

        Returns:
            Complete API response body dict with intent and facts only
        """
        # EXTRACTION-ONLY: Only output intent, optional operation, and facts
        # Luma is a pure fact extractor - no semantic fields (status, missing_slots, clarification, booking block)
        response_body = {
            "intent": intent_payload,
        }

        if operation:
            response_body["operation"] = operation

        # Use pre-aggregated facts if provided
        # Facts should always be provided from resolve_service.py via aggregate_extraction_facts
        # If facts is None, just skip adding facts to response (empty facts dict)
        if facts is None:
            facts = {}

        if facts:
            response_body["facts"] = facts

        # Attach full internal pipeline data only in debug mode
        if debug_data:
            response_body["debug"] = debug_data

        return response_body
