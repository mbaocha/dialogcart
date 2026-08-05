"""Booking E2E scenarios — confirmation conversation state."""

# ============================================================
# Covered
#
# ✓ Valid
# ✓ References
# ✓ Revision
# ✓ Digressions
# ✓ Recovery
#
# TODO
#
# □ Invalid
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
        "Time match exact after service selection",
        Turn(
            "book haircut tomorrow at 10am",
            Expect(
                response_status="NEEDS_CLARIFICATION",
                intent="CREATE_APPOINTMENT",
            ),
        ),
        Turn(
            "premium",
            Expect(
                response_status="succeeded",
                time_match=TIME_MATCH_EXACT,
                planner="AWAITING_CONFIRMATION",
                action=None,
                awaiting="USER_CONFIRMATION",
                response_text_present=True,
                confirmation="pending",
                slot_contains={"time": "10"},
            ),
            after=_assert_no_booking_single_search,
        ),
        fixture="scripted",
        tags=["time-resolution", "exact"],
        id="time-match-exact-same-turn",
    )
)

# ============================================================
# REFERENCE EXPRESSIONS
# ============================================================
_register(
    Scenario(
        "Mismatch then user picks alternative",
        Turn("book haircut tomorrow at 9:15am"),
        Turn(
            "premium",
            Expect(time_match=TIME_MATCH_MISMATCH),
        ),
        Turn(
            "9:30am",
            Expect(
                response_status="AWAITING_CONFIRMATION",
                planner="AWAITING_CONFIRMATION",
                awaiting="USER_CONFIRMATION",
                action=None,
                confirmation="pending",
                slot_contains={"time": "09:30"},
            ),
            after=_assert_no_booking_single_search,
        ),
        fixture="scripted_mismatch_pick",
        tags=["time-resolution", "mismatch", "bind"],
        id="mismatch-then-pick-alternative",
    )
)


_register(
    Scenario(
        "Dotted time 1.30 binds presented 1:30 PM",
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
            "1.30",
            Expect(
                response_status="AWAITING_CONFIRMATION",
                planner="AWAITING_CONFIRMATION",
                stage="CONFIRM",
                awaiting="USER_CONFIRMATION",
                action=None,
                intent="CREATE_APPOINTMENT",
                confirmation="pending",
                time_match=TIME_MATCH_EXACT,
                session_slots={"service_id": PREMIUM_SERVICE},
                slot_contains={"time": "13:30", "date": TARGET_DATE},
                missing_slots=[],
            ),
            after=_assert_dotted_time_bound("1.30"),
        ),
        fixture="scripted_dotted_time_selection",
        tags=["booking", "time-selection", "dotted-time"],
        id="dotted-time-1-30-binds-presented-130pm",
    )
)


_register(
    Scenario(
        "Dotted time 1.30pm binds presented 1:30 PM",
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
            "1.30pm",
            Expect(
                response_status="AWAITING_CONFIRMATION",
                planner="AWAITING_CONFIRMATION",
                stage="CONFIRM",
                awaiting="USER_CONFIRMATION",
                action=None,
                intent="CREATE_APPOINTMENT",
                confirmation="pending",
                time_match=TIME_MATCH_EXACT,
                session_slots={"service_id": PREMIUM_SERVICE},
                slot_contains={"time": "13:30", "date": TARGET_DATE},
                missing_slots=[],
            ),
            after=_assert_dotted_time_bound("1.30pm"),
        ),
        fixture="scripted_dotted_time_selection",
        tags=["booking", "time-selection", "dotted-time"],
        id="dotted-time-1-30pm-binds-presented-130pm",
    )
)


_register(
    Scenario(
        "Numeric hour selects unique offered time",
        Turn(
            "book me a premium haircut on July 24th",
            Expect(
                response_status="succeeded",
                planner="READY",
                stage="AVAILABILITY",
                action="SEARCH_AVAILABILITY",
                intent="CREATE_APPOINTMENT",
                session_slots={"service_id": PREMIUM_SERVICE},
                execution="availability",
                has_availability_slots=True,
                missing_slots=["time"],
                date_proposal=_JULY_24,
                confirmation=None,
                response_text_present=True,
            ),
            after=_capture_post_availability_baseline,
        ),
        Turn(
            "9",
            Expect(
                response_status="AWAITING_CONFIRMATION",
                planner="AWAITING_CONFIRMATION",
                stage="CONFIRM",
                awaiting="USER_CONFIRMATION",
                action=None,
                intent="CREATE_APPOINTMENT",
                confirmation="pending",
                time_match=TIME_MATCH_EXACT,
                session_slots={"service_id": PREMIUM_SERVICE},
                slot_contains={"time": "09:00"},
                missing_slots=[],
                response_text_present=True,
            ),
            after=_assert_numeric_hour_binds_unique_offered_time,
        ),
        fixture="scripted_confirm",
        tags=["booking", "time-selection", "numeric-hour", "regression"],
        id="numeric-time-selection-requires-clarification",
    )
)

# ============================================================
# REVISIONS
# ============================================================
_register(
    Scenario(
        "Reject then revise time",
        Turn(
            "book haircut",
            Expect(
                response_status="NEEDS_CLARIFICATION",
                planner="NEEDS_CLARIFICATION",
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
                execution="availability",
                has_availability_slots=True,
            ),
        ),
        Turn(
            "10am",
            Expect(
                response_status="AWAITING_CONFIRMATION",
                planner="AWAITING_CONFIRMATION",
                stage="CONFIRM",
                awaiting="USER_CONFIRMATION",
                action=None,
                confirmation="pending",
                slot_contains={"time": "10"},
            ),
        ),
        Turn(
            "no",
            Expect(
                intent="CREATE_APPOINTMENT",
                confirmation=None,
                session_slots={"service_id": PREMIUM_SERVICE},
            ),
            after=_assert_no_booking_and_date_kept,
        ),
        Turn(
            "11am",
            Expect(
                response_status="AWAITING_CONFIRMATION",
                planner="AWAITING_CONFIRMATION",
                stage="CONFIRM",
                awaiting="USER_CONFIRMATION",
                action=None,
                confirmation="pending",
                slot_contains={"time": "11"},
            ),
        ),
        Turn(
            "yes",
            Expect(action="CONFIRM_APPOINTMENT", slot_contains={"time": "11"}),
            after=_assert_booking_called,
        ),
        tags=["booking", "revise"],
        requires_customer_identity=True,
        id="reject-then-revise-time",
    )
)


_register(
    Scenario(
        "Service revision invalidates availability",
        Turn("book haircut"),
        Turn("premium"),
        Turn(
            "10am",
            Expect(
                response_status="AWAITING_CONFIRMATION",
                confirmation="pending",
                session_slots={"service_id": PREMIUM_SERVICE},
            ),
            after=_capture_searches,
        ),
        Turn(
            "rather book flexi haircut",
            Expect(
                intent="CREATE_APPOINTMENT",
                session_slots={"service_id": FLEXI_SERVICE},
                confirmation=None,
            ),
            before=_clear_sticky_temporal_facts,
            after=_assert_service_revision,
        ),
        fixture="scripted_service_revision",
        tags=["booking", "invalidation"],
    )
)


_register(
    Scenario(
        "Date revision invalidates availability",
        Turn("book haircut"),
        Turn("premium"),
        Turn(
            "10am",
            Expect(
                response_status="AWAITING_CONFIRMATION",
                planner="AWAITING_CONFIRMATION",
                stage="CONFIRM",
                action=None,
                confirmation="pending",
                session_slots={"service_id": PREMIUM_SERVICE},
            ),
            after=_capture_searches,
        ),
        Turn(
            "actually July 11",
            Expect(
                intent="CREATE_APPOINTMENT",
                response_status="succeeded",
                planner="READY",
                stage="AVAILABILITY",
                action="SEARCH_AVAILABILITY",
                session_slots={"service_id": PREMIUM_SERVICE},
                confirmation=None,
                execution="availability",
                has_availability_slots=True,
                date_proposal="2026-07-11",
                slot_absent=["date", "time"],
                availability_invalidated=True,
            ),
            before=_clear_sticky_temporal_facts,
            after=_assert_date_revision,
        ),
        fixture="scripted_date_revision",
        tags=["booking", "invalidation"],
    )
)

# ============================================================
# DIGRESSIONS
# ============================================================
# Confirmation interruptions / supersessions (related suite).
from core.tests.e2e.booking._confirmation_interruption import (
    SCENARIOS as _CONFIRMATION_INTERRUPTION_SCENARIOS,
)
RELATED_SCENARIOS.extend(_CONFIRMATION_INTERRUPTION_SCENARIOS)

# ============================================================
# INVALID INPUT
# ============================================================
# (no scenarios in this section yet)

# ============================================================
# RECOVERY
# ============================================================
_register(
    Scenario(
        "Identity blocked then resolved then freshly confirmed",
        Turn(
            "book haircut",
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
                execution="availability",
                has_availability_slots=True,
            ),
        ),
        Turn(
            "10am",
            Expect(
                response_status="AWAITING_CONFIRMATION",
                planner="AWAITING_CONFIRMATION",
                stage="CONFIRM",
                awaiting="USER_CONFIRMATION",
                action=None,
                confirmation="pending",
                session_slots={"service_id": PREMIUM_SERVICE},
                slot_contains={"time": "10"},
            ),
            after=_assert_no_booking,
        ),
        Turn(
            "yes",
            Expect(
                planner="NEEDS_CLARIFICATION",
                confirmation="pending",
                session_slots={"service_id": PREMIUM_SERVICE},
                slot_contains={"time": "10"},
                response_text_present=True,
            ),
            after=_assert_identity_blocked_yes,
        ),
        Turn(
            "ok",
            Expect(
                confirmation="pending",
                session_slots={"service_id": PREMIUM_SERVICE},
                slot_contains={"time": "10"},
                response_text_present=True,
            ),
            before=_attach_identity_before_resume,
            after=_assert_identity_resolved_pending,
        ),
        Turn(
            "yes",
            Expect(
                planner="READY",
                stage="CONFIRM",
                action="CONFIRM_APPOINTMENT",
                confirmation=None,
                session_slots={"service_id": PREMIUM_SERVICE},
                slot_contains={"time": "10"},
            ),
            after=_assert_booking_created,
        ),
        fixture="booking",
        tags=["booking", "identity", "confirmation", "audit"],
        id="identity-blocked-then-resolved-then-confirmed",
        # Identity attached mid-flow — do not set requires_customer_identity.
    )
)

