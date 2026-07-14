"""ActionRunner — Phase 1 architectural boundary for action execution.

Dispatches a planning result to the appropriate execution handler.
Initially wraps core.execution.dispatcher.execute.
Phase 2 will own client lifecycle and execution coordination here.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


class ActionRunner:
    """Execute the action selected by the planner.

    Phase 1: thin facade over core.execution.dispatcher.execute.
    Accepts optional pre-built clients; constructs defaults when not supplied.
    """

    def __init__(
        self,
        availability_client: Optional[Any] = None,
        booking_client: Optional[Any] = None,
    ) -> None:
        self._availability_client = availability_client
        self._booking_client = booking_client

    def run(
        self,
        plan: Dict[str, Any],
        availability_client: Optional[Any] = None,
        booking_client: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Dispatch *plan* to the matching execution handler.

        Keyword clients override instance-level clients (useful for per-request
        injection in tests).
        """
        from core.execution.dispatcher import execute

        return execute(
            plan=plan,
            availability_client=availability_client or self._availability_client,
            booking_client=booking_client or self._booking_client,
        )
