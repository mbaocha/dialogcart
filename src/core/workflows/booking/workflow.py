"""BookingWorkflow — booking domain boundary.

Owns post-commit slot propagation after tool dispatch.
Tool dispatch (CONFIRM_APPOINTMENT, etc.) is owned by the execution
dispatcher; this workflow must not initiate execution.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class BookingWorkflow:
    """Booking-domain post-processing after successful tool execution."""

    def process_result(
        self,
        execution_result: Dict[str, Any],
        plan: Dict[str, Any],
        slots: Dict[str, Any],
        action: str,
        session_state: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Propagate booking execution fields back to slots.

        Handles CONFIRM_APPOINTMENT, FETCH_BOOKING, and CREATE_BOOKING_HOLD.
        Mutates plan["slots"] in-place and returns the updated slots dict.
        """
        if execution_result.get("status") != "succeeded":
            return slots

        refs = execution_result.get("refs")
        refs = refs if isinstance(refs, dict) else {}
        subject = execution_result.get("subject")
        subject = subject if isinstance(subject, dict) else {}

        if action == "CONFIRM_APPOINTMENT":
            plan["action"] = "CONFIRM_APPOINTMENT"
            booking_id = refs.get("booking_id")
            booking_code = refs.get("booking_code")
            if booking_id:
                slots["booking_id"] = booking_id
            if booking_code:
                slots["booking_code"] = booking_code
            if booking_id or booking_code:
                plan["slots"] = slots
                merged = plan.get("_merged_luma_response")
                if isinstance(merged, dict):
                    merged_slots = dict(merged.get("slots") or {})
                    if booking_id:
                        merged_slots["booking_id"] = booking_id
                    if booking_code:
                        merged_slots["booking_code"] = booking_code
                    merged["slots"] = merged_slots
                    plan["_merged_luma_response"] = merged
                from core.session.confirmation_gate import (
                    consume_create_appointment_confirmation,
                )

                consume_create_appointment_confirmation(
                    session_state or {},
                    plan.get("_merged_luma_response"),
                    reason="create_appointment_committed",
                )
                logger.debug(
                    "Persisted booking_id=%s booking_code=%s to slots for idempotency",
                    booking_id,
                    booking_code,
                )

        elif action == "FETCH_BOOKING":
            booking_id = refs.get("booking_id")
            if booking_id:
                slots["booking_id"] = booking_id
                plan["slots"] = slots
                logger.debug(
                    f"Persisted booking_id={booking_id} to slots from FETCH_BOOKING"
                )

        elif action == "CREATE_BOOKING_HOLD":
            booking_id = refs.get("booking_id")
            booking_code = refs.get("booking_code")
            total_amount = subject.get("total_amount")
            currency = subject.get("currency")
            if booking_id:
                slots["booking_id"] = booking_id
                if booking_code:
                    slots["booking_code"] = booking_code
                if total_amount:
                    slots["total_amount"] = total_amount
                if currency:
                    slots["currency"] = currency
                plan["slots"] = slots
                logger.debug(
                    f"Persisted booking_id={booking_id}, booking_code={booking_code}, "
                    f"total_amount={total_amount}, currency={currency} to slots from CREATE_BOOKING_HOLD"
                )

        return slots
