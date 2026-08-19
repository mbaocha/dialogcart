"""Booking E2E scenarios organized by conversation state."""

from __future__ import annotations

from typing import List

from core.tests.e2e.framework.conversation import Scenario
from core.tests.e2e.framework.fixtures import DEFAULT_BUSINESS_CATEGORY
from core.tests.e2e.booking._helpers import (  # noqa: F401
    _assert_booking_created,
    _assert_no_booking,
)
from core.tests.e2e.booking import (
    availability,
    browse,
    car_service,
    category_discovery,
    completed,
    confirmation,
    customer_identification,
    service_selection,
)

SCENARIOS: List[Scenario] = []
for _mod in (
    service_selection,
    availability,
    browse,
    car_service,
    category_discovery,
    confirmation,
    customer_identification,
    completed,
):
    # Module-level BUSINESS_CATEGORY (default beauty_salon). Not per-Scenario.
    _category = getattr(_mod, "BUSINESS_CATEGORY", DEFAULT_BUSINESS_CATEGORY)
    for _scenario in getattr(_mod, "SCENARIOS", []):
        _scenario.business_category = _category
        SCENARIOS.append(_scenario)

__all__ = ["SCENARIOS", "_assert_booking_created", "_assert_no_booking"]
