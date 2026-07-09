"""BookingWorkflow — Phase 1 architectural boundary for booking operations.

Owns the booking domain: appointment creation and cancellation execution.
Initially delegates to the existing booking client via the dispatcher.
Phase 2 will consolidate booking state management here.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


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
