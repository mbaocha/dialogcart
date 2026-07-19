"""WorkflowRouter — domain selection for execution post-processing.

Maps the policy ``client`` field from ``intent_policy.yaml`` to a domain
route name (``availability`` / ``booking``). Does not construct workflows
or initiate tool dispatch.
"""

from __future__ import annotations

from typing import Dict, Optional


class WorkflowRouter:
    """Select the domain that owns post-processing for an execution client.

    Routing key: the ``client`` field set in ``intent_policy.yaml`` for
    each execution step. Engine constructs workflow instances separately.
    """

    _CLIENT_TO_ROUTE: Dict[str, str] = {
        "availability_client": "availability",
        "booking_client": "booking",
    }

    def get_route(self, client_name: Optional[str]) -> Optional[str]:
        """Return the route name for *client_name*, or None if unknown.

        Returns:
            ``"availability"`` — AvailabilityWorkflow post-processing
            ``"booking"``      — BookingWorkflow post-processing
            ``None``           — unrecognised client; caller should fall back
        """
        return self._CLIENT_TO_ROUTE.get(client_name or "")
