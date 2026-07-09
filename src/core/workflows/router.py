"""WorkflowRouter — routes an execution action to the owning domain workflow.

Phase 2: maps execution client names (from intent policy) and action names
to the appropriate workflow boundary. Initially contains simple delegation
with no business logic.

Phase 3 will replace client-name routing with action-first routing and
allow the router to fully own the execution dispatch.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


class WorkflowRouter:
    """Select and return the workflow that owns a given execution action.

    Routing key: the ``client`` field set in ``intent_policy.yaml`` for
    each execution step. This mirrors the existing orchestrator dispatch
    so behaviour is identical.
    """

    _CLIENT_TO_ROUTE: Dict[str, str] = {
        "availability_client": "availability",
        "booking_client": "booking",
    }

    def get_route(self, client_name: Optional[str]) -> Optional[str]:
        """Return the route name for *client_name*, or None if unknown.

        Returns:
            ``"availability"`` — route to AvailabilityWorkflow
            ``"booking"``      — route to BookingWorkflow
            ``None``           — unrecognised client; caller should fall back
        """
        return self._CLIENT_TO_ROUTE.get(client_name or "")

    def get_availability_workflow(self) -> Any:
        """Return the AvailabilityWorkflow instance for this router."""
        from core.workflows.availability.workflow import AvailabilityWorkflow

        return AvailabilityWorkflow()

    def get_booking_workflow(self) -> Any:
        """Return the BookingWorkflow instance for this router."""
        from core.workflows.booking.workflow import BookingWorkflow

        return BookingWorkflow()
