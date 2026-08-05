"""Booking E2E scenarios — availability conversation state."""

# ============================================================
# Covered
#
# ✓ Valid
# ✓ References
# ✓ Revision
# ✓ Invalid
#
# TODO
#
# □ Digressions
# □ Recovery
# ============================================================

from __future__ import annotations

from typing import List

from core.planning.time_resolution import TIME_MATCH_EXACT, TIME_MATCH_MISMATCH
from core.tests.e2e.framework.conversation import (
    Expect,
    FLEXI_SERVICE,
    FROZEN_TIME,
    PREMIUM_SERVICE,
    Scenario,
    Turn,
    _confirmation_state,
    _resolve_search_date,
    _response_text,
    attach_commit_customer_identity,
)
from core.adapters.errors import UpstreamError
from core.session.session_manager import get_session, save_session
from core.tests.e2e.booking import _helpers as _booking_helpers

globals().update(
    {
        name: getattr(_booking_helpers, name)
        for name in getattr(_booking_helpers, "__all__", dir(_booking_helpers))
        if not name.startswith("__")
    }
)

SCENARIOS: List[Scenario] = []
RELATED_SCENARIOS: List[Scenario] = []


def _register(scenario: Scenario) -> Scenario:
    SCENARIOS.append(scenario)
    return scenario


def _register_related(scenario: Scenario) -> Scenario:
    RELATED_SCENARIOS.append(scenario)
    return scenario


# ============================================================
# VALID RESPONSES
# ============================================================
_register(
    Scenario(
        "Tomorrow by 9am then premium exact match",
        Turn(
            "book haircut tomorrow by 9am",
            Expect(
                response_status="NEEDS_CLARIFICATION",
                intent="CREATE_APPOINTMENT",
                missing_slots=["service_id"],
                date_proposal=_TOMORROW,
                time_proposal="09",
            ),
            after=_assert_no_search_yet,
        ),
        Turn(
            "premium",
            Expect(
                response_status="succeeded",
                execution="availability",
                has_availability_slots=True,
                time_match=TIME_MATCH_EXACT,
                planner="AWAITING_CONFIRMATION",
                action=None,
                awaiting="USER_CONFIRMATION",
                session_slots={"service_id": PREMIUM_SERVICE},
                response_text_present=True,
                slot_contains={"time": "09"},
            ),
            after=_assert_exact_search_recorded_tomorrow,
        ),
        fixture="scripted",
        tags=["time-resolution", "exact"],
        id="tomorrow-by-9am-premium-exact",
    )
)

# ============================================================
# REFERENCE EXPRESSIONS
# ============================================================
_register(
    Scenario(
        "Time resolution persists proposals on mismatch",
        Turn("book haircut tomorrow at 9:15am"),
        Turn(
            "premium",
            Expect(time_match=TIME_MATCH_MISMATCH),
            after=_assert_proposals_persisted,
        ),
        fixture="scripted_mismatch",
        tags=["time-resolution", "persistence"],
        id="time-resolution-persists-across-turns",
    )
)

# ============================================================
# REVISIONS
# ============================================================
_register(
    Scenario(
        "Availability service revision searches Flexi not Premium",
        Turn("book haircut"),
        Turn(
            "premium",
            Expect(
                response_status="succeeded",
                planner="READY",
                stage="AVAILABILITY",
                action="SEARCH_AVAILABILITY",
                session_slots={"service_id": PREMIUM_SERVICE},
                execution="availability",
                has_availability_slots=True,
            ),
            after=_capture_searches_before_flexi,
        ),
        Turn(
            "show availability for flexi",
            Expect(
                response_status="succeeded",
                planner="READY",
                stage="AVAILABILITY",
                action="SEARCH_AVAILABILITY",
                intent="CREATE_APPOINTMENT",
                session_slots={"service_id": FLEXI_SERVICE},
                execution="availability",
                has_availability_slots=True,
                availability_invalidated=True,
            ),
            after=_assert_availability_searched_flexi,
        ),
        fixture="scripted_availability_service_revision",
        tags=["booking", "availability", "service-revision", "bug2"],
        id="availability-service-revision-flexi",
    )
)


_register(
    Scenario(
        "Date request after availability creates a new search",
        Turn(
            "Book me a premium haircut",
            Expect(
                response_status="succeeded",
                planner="READY",
                stage="AVAILABILITY",
                action="SEARCH_AVAILABILITY",
                intent="CREATE_APPOINTMENT",
                session_slots={"service_id": PREMIUM_SERVICE},
                execution="availability",
                has_availability_slots=True,
                confirmation=None,
                response_text_present=True,
            ),
            after=_assert_july23_availability_presented,
        ),
        Turn(
            "July 25",
            Expect(
                action="SEARCH_AVAILABILITY",
                intent="CREATE_APPOINTMENT",
                session_slots={"service_id": PREMIUM_SERVICE},
                confirmation=None,
                response_text_present=True,
            ),
            after=_assert_july25_searches,
        ),
        fixture="scripted_multi_day_july23",
        tags=["booking", "availability", "search", "date-revision", "regression"],
        id="date-request-after-availability-searches",
    )
)

# ============================================================
# DIGRESSIONS
# ============================================================
# (no scenarios in this section yet)

# ============================================================
# INVALID INPUT
# ============================================================
_register(
    Scenario(
        "Unavailable time keeps booking flow",
        Turn(
            "book me a haircut",
            Expect(
                response_status="NEEDS_CLARIFICATION",
                intent="CREATE_APPOINTMENT",
            ),
        ),
        Turn(
            "premium",
            Expect(
                response_status="succeeded",
                planner="READY",
                stage="AVAILABILITY",
                action="SEARCH_AVAILABILITY",
                session_slots={"service_id": PREMIUM_SERVICE},
                has_availability_slots=True,
            ),
            after=_capture_searches,
        ),
        Turn(
            "12pm",
            Expect(
                intent="CREATE_APPOINTMENT",
                time_match="TIME_MATCH_MISMATCH",
                planner="NEEDS_CLARIFICATION",
                action=None,
                awaiting="TIME_SELECTION",
                response_text_present=True,
                session_slots={"service_id": PREMIUM_SERVICE},
                time_proposal="12",
            ),
            after=_assert_unavailable_time_mismatch,
        ),
        fixture="scripted_unavailable_time",
        tags=["booking", "time-mismatch"],
    )
)


_register(
    Scenario(
        "Time match mismatch conversational response",
        Turn(
            "book haircut tomorrow at 9:15am",
            Expect(response_status="NEEDS_CLARIFICATION"),
            after=_capture_search_count,
        ),
        Turn(
            "premium",
            Expect(
                response_status="succeeded",
                time_match=TIME_MATCH_MISMATCH,
                planner="NEEDS_CLARIFICATION",
                action=None,
                awaiting="TIME_SELECTION",
                response_text_present=True,
                has_availability_slots=True,
                time_proposal="09:15",
            ),
            after=_assert_mismatch_side_effects,
        ),
        fixture="scripted_mismatch",
        tags=["time-resolution", "mismatch"],
        id="time-match-mismatch-conversational",
    )
)


_register(
    Scenario(
        "Empty availability search records no slots",
        Turn("book haircut tomorrow by 9am"),
        Turn(
            "premium",
            Expect(
                response_status="succeeded",
                execution="availability",
                has_availability_slots=False,
                time_match=TIME_MATCH_MISMATCH,
                planner="NEEDS_CLARIFICATION",
                action=None,
                time_proposal="09",
            ),
            after=_assert_empty_slots,
        ),
        fixture="scripted_empty",
        tags=["time-resolution", "empty"],
        id="empty-availability-no-slots",
    )
)


_register(
    Scenario(
        "Invalid time input explains and re-shows availability",
        Turn(
            "book me a premium haircut",
            Expect(
                response_status="succeeded",
                planner="READY",
                stage="AVAILABILITY",
                action="SEARCH_AVAILABILITY",
                intent="CREATE_APPOINTMENT",
                session_slots={"service_id": PREMIUM_SERVICE},
                execution="availability",
                has_availability_slots=True,
                confirmation=None,
            ),
            after=_capture_post_availability_baseline,
        ),
        Turn(
            "xxxxx",
            Expect(
                planner="READY",
                action=None,
                intent="CREATE_APPOINTMENT",
                confirmation=None,
                session_slots={"service_id": PREMIUM_SERVICE},
                missing_slots=["time"],
                response_text_present=True,
            ),
            after=_assert_invalid_time_explains_and_reshows,
        ),
        fixture="scripted_dotted_time_selection",
        tags=["booking", "time-selection", "invalid-time", "recording"],
        id="invalid-time-explains-and-reshows-availability",
    )
)


_register(
    Scenario(
        "Malformed clock 5.xyz is unparseable not unavailable",
        Turn(
            "book me a premium haircut",
            Expect(
                response_status="succeeded",
                planner="READY",
                stage="AVAILABILITY",
                action="SEARCH_AVAILABILITY",
                intent="CREATE_APPOINTMENT",
                session_slots={"service_id": PREMIUM_SERVICE},
                execution="availability",
                has_availability_slots=True,
                confirmation=None,
            ),
            after=_capture_post_availability_baseline,
        ),
        Turn(
            "5.xyz",
            Expect(
                planner="READY",
                action=None,
                intent="CREATE_APPOINTMENT",
                confirmation=None,
                session_slots={"service_id": PREMIUM_SERVICE},
                missing_slots=["time"],
                response_text_present=True,
            ),
            after=_assert_malformed_clock_not_mismatch,
        ),
        fixture="scripted_dotted_time_selection",
        tags=["booking", "time-selection", "malformed-clock", "recovery"],
        id="malformed-clock-5-xyz-unparseable-not-unavailable",
    )
)


_register(
    Scenario(
        "Unavailable 5pm uses mismatch wording not unparseable",
        Turn(
            "book me a premium haircut",
            Expect(
                response_status="succeeded",
                planner="READY",
                stage="AVAILABILITY",
                action="SEARCH_AVAILABILITY",
                intent="CREATE_APPOINTMENT",
                session_slots={"service_id": PREMIUM_SERVICE},
                execution="availability",
                has_availability_slots=True,
                confirmation=None,
            ),
            after=_capture_post_availability_baseline,
        ),
        Turn(
            "5pm",
            Expect(
                planner="NEEDS_CLARIFICATION",
                awaiting="TIME_SELECTION",
                action=None,
                confirmation=None,
                time_match=TIME_MATCH_MISMATCH,
                session_slots={"service_id": PREMIUM_SERVICE},
                response_text_present=True,
            ),
            after=_assert_unavailable_5pm_mismatch_wording,
        ),
        fixture="scripted_dotted_time_selection",
        tags=["booking", "time-selection", "time-mismatch"],
        id="unavailable-5pm-mismatch-not-unparseable",
    )
)

# ============================================================
# RECOVERY
# ============================================================
# (no scenarios in this section yet)
