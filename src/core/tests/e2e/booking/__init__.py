"""Booking E2E scenarios organized by conversation state."""

from __future__ import annotations

from typing import List

from core.tests.e2e.framework.conversation import Scenario
from core.tests.e2e.booking._helpers import (  # noqa: F401
    _assert_booking_created,
    _assert_no_booking,
)
from core.tests.e2e.booking import (
    availability,
    browse,
    completed,
    confirmation,
    service_selection,
)

SCENARIOS: List[Scenario] = []
for _mod in (
    service_selection,
    availability,
    browse,
    confirmation,
    completed,
):
    SCENARIOS.extend(getattr(_mod, "SCENARIOS", []))

__all__ = ["SCENARIOS", "_assert_booking_created", "_assert_no_booking"]
