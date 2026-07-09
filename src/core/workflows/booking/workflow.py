"""BookingWorkflow — Phase 1 architectural boundary for booking operations.

Owns the booking domain: appointment creation and cancellation execution.
Initially delegates to the existing booking client via the dispatcher.
Phase 2 will consolidate booking state management here.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class BookingWorkflow:
    """Facade for all booking-domain execution operations.

    Phase 1: thin delegation to
             core.orchestration.execution.dispatcher.execute
             and core.orchestration.execution.clients.booking_client.
    """

    def confirm(
        self,
        plan: Dict[str, Any],
        client: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Execute the booking commit action in *plan*.

        Handles CONFIRM_APPOINTMENT and CONFIRM_CANCELLATION actions.
        *client* defaults to a fresh BookingClient when not supplied.
        """
        from core.orchestration.execution.clients.booking_client import BookingClient
        from core.orchestration.execution.dispatcher import execute

        return execute(
            plan=plan,
            booking_client=client or BookingClient(),
        )

    def process_result(
        self,
        execution_result: Dict[str, Any],
        plan: Dict[str, Any],
        slots: Dict[str, Any],
        action: str,
    ) -> Dict[str, Any]:
        """Propagate booking execution fields back to slots.

        Handles CONFIRM_APPOINTMENT, FETCH_BOOKING, and CREATE_BOOKING_HOLD.
        Mutates plan["slots"] in-place and returns the updated slots dict.
        """
        if execution_result.get("status") != "EXECUTED":
            return slots

        if action == "CONFIRM_APPOINTMENT":
            plan["action"] = "CONFIRM_APPOINTMENT"
            booking_id = execution_result.get("booking_id")
            if booking_id:
                slots["booking_id"] = booking_id
                plan["slots"] = slots
                logger.debug(
                    f"Persisted booking_id={booking_id} to slots for idempotency"
                )

        elif action == "FETCH_BOOKING":
            booking_id = execution_result.get("booking_id")
            if booking_id:
                slots["booking_id"] = booking_id
                plan["slots"] = slots
                logger.debug(
                    f"Persisted booking_id={booking_id} to slots from FETCH_BOOKING"
                )

        elif action == "CREATE_BOOKING_HOLD":
            booking_id = execution_result.get("booking_id")
            booking_code = execution_result.get("booking_code")
            total_amount = execution_result.get("total_amount")
            currency = execution_result.get("currency")
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
