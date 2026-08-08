"""Action → ExecutionAdapter registration."""

from __future__ import annotations

from typing import Dict, Optional

from core.execution.adapters.availability_adapter import AvailabilityAdapter
from core.execution.adapters.base import ExecutionAdapter
from core.execution.adapters.booking_adapter import BookingAdapter

_BOOKING = BookingAdapter()
_AVAILABILITY = AvailabilityAdapter()

_REGISTRY: Dict[str, ExecutionAdapter] = {
    "SEARCH_AVAILABILITY": _AVAILABILITY,
    "CONFIRM_APPOINTMENT": _BOOKING,
    "CREATE_BOOKING_HOLD": _BOOKING,
    "FINALIZE_RESERVATION": _BOOKING,
    "FETCH_BOOKING": _BOOKING,
    "APPLY_MODIFICATION": _BOOKING,
    "CONFIRM_CANCELLATION": _BOOKING,
}


def get_execution_adapter(action: str) -> Optional[ExecutionAdapter]:
    """Return the registered adapter for ``action``, or ``None`` if unknown."""
    return _REGISTRY.get(action)
