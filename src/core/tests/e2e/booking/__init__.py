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

# Dedicated-runner partitions (same Scenario objects; no duplicate definitions).
CONFIRMATION_INTERRUPTION_IDS = {
    "reshow-availability-during-confirmation",
    "change-date-during-confirmation",
    "switch-service-during-confirmation",
    "availability-supersedes-confirmation",
    "availability-with-new-date",
    "service-change-during-confirmation",
    "correction-time-revision-during-confirmation",
    "interruption-new-availability-new-booking",
    "availability-july-24-interrupts-confirmation",
    "july21-must-not-revert-after-july20-browse-exhaustion",
    "correction-service-switch-no-rematch-9am",
    "service-change-must-preserve-current-search-date",
    "relative-tomorrow-availability-must-resolve",
    "service-change-after-confirmation-to-flexi",
}
BROWSE_EXHAUSTION_IDS = {
    "browse-exhaustion-must-not-poison-subsequent-search",
    "undated-first-available-then-browse-exhaustion",
}
COLD_START_CLARIFICATION_IDS = {
    "cold-start-availability-asks-for-service",
}
DATE_SURVIVES_IDS = {
    "explicit-date-survives-service-clarification",
    "july-23-survives-service-clarification",
}
HANDLER_DIGRESSION_IDS = {
    "handler-delegated-faq-resume-commit",
}

_DEDICATED_IDS = (
    CONFIRMATION_INTERRUPTION_IDS
    | BROWSE_EXHAUSTION_IDS
    | COLD_START_CLARIFICATION_IDS
    | DATE_SURVIVES_IDS
    | HANDLER_DIGRESSION_IDS
)

# Legacy booking runner keeps the non-dedicated partition (fixture-compatible set).
BOOKING_RUNNER_SCENARIOS: List[Scenario] = [
    s for s in SCENARIOS if s.pytest_id() not in _DEDICATED_IDS
]


def scenarios_with_ids(ids) -> List[Scenario]:
    wanted = set(ids)
    return [s for s in SCENARIOS if s.pytest_id() in wanted]


__all__ = [
    "SCENARIOS",
    "BOOKING_RUNNER_SCENARIOS",
    "scenarios_with_ids",
    "CONFIRMATION_INTERRUPTION_IDS",
    "BROWSE_EXHAUSTION_IDS",
    "COLD_START_CLARIFICATION_IDS",
    "DATE_SURVIVES_IDS",
    "HANDLER_DIGRESSION_IDS",
    "_assert_booking_created",
    "_assert_no_booking",
]
