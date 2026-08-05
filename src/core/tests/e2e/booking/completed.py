"""Booking E2E scenarios — completed conversation state."""

# ============================================================
# Covered
#
# ✓ Valid
# ✓ Revision
# ✓ Interruptions
# ✓ Invalid
#
# TODO
#
# □ References
# □ Recovery
# ============================================================

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from core.planning.time_resolution import TIME_MATCH_EXACT, TIME_MATCH_MISMATCH
from core.tests.e2e.framework.conversation import (
    Expect,
    FLEXI_SERVICE,
    FIRST_AVAILABLE_DATE,
    FROZEN_TIME,
    ORG_ID,
    PREMIUM_SERVICE,
    Scenario,
    Turn,
    _confirmation_state,
    _normalize_explicit_search_date,
    _plan_view,
    _presentation_page_index,
    _resolve_search_date,
    _response_indicates_no_more_times,
    _response_text,
    assert_no_booking_execution,
    attach_commit_customer_identity,
    extract_presented_times,
)
from core.adapters.errors import UpstreamError
from core.session.session_manager import get_session, save_session
from core.tests.e2e.booking import _helpers as _booking_helpers
from core.workflows.availability.fingerprint import compute_availability_fingerprint

globals().update(
    {
        name: getattr(_booking_helpers, name)
        for name in getattr(_booking_helpers, "__all__", dir(_booking_helpers))
        if not name.startswith("__")
    }
)

SCENARIOS: List[Scenario] = []


def _register(scenario: Scenario) -> Scenario:
    SCENARIOS.append(scenario)
    return scenario

# ============================================================
# VALID RESPONSES
# ============================================================
_register(
    Scenario(
        "Happy path create appointment",
        Turn(
            "book me a haircut",
            Expect(
                response_status="NEEDS_CLARIFICATION",
                planner="NEEDS_CLARIFICATION",
                intent="CREATE_APPOINTMENT",
                confirmation=None,
                missing_slots=["service_id", "date", "time"],
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
                confirmation=None,
                execution="availability",
                has_availability_slots=True,
                missing_slots=["date", "time"],
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
                session_slots={"service_id": PREMIUM_SERVICE},
                confirmation="pending",
                slot_contains={"time": "10"},
                missing_slots=[],
            ),
            after=_assert_no_booking,
        ),
        Turn(
            "yes",
            Expect(
                planner="READY",
                stage="CONFIRM",
                action="CONFIRM_APPOINTMENT",
                confirmation=None,
                missing_slots=[],
                slot_contains={"time": "10"},
                session_slots={"service_id": PREMIUM_SERVICE},
            ),
            after=_assert_booking_created,
        ),
        tags=["booking", "happy-path"],
        requires_customer_identity=True,
    )
)


_register(
    Scenario(
        "Tomorrow by 12pm premium then confirm",
        Turn(
            "book me haircut tomorrow by 12pm",
            Expect(
                response_status="NEEDS_CLARIFICATION",
                intent="CREATE_APPOINTMENT",
                missing_slots=["service_id"],
                date_proposal=_TOMORROW,
                time_proposal="12",
            ),
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
                confirmation="pending",
                session_slots={"service_id": PREMIUM_SERVICE},
                response_text_present=True,
                slot_contains={"time": "12"},
            ),
            after=_assert_no_booking,
        ),
        Turn(
            "yes",
            Expect(
                planner="READY",
                stage="CONFIRM",
                action="CONFIRM_APPOINTMENT",
                confirmation=None,
                missing_slots=[],
                session_slots={"service_id": PREMIUM_SERVICE},
                slot_contains={"time": "12"},
            ),
            after=_assert_booking_created,
        ),
        fixture="scripted_confirm",
        tags=["time-resolution", "exact", "confirm"],
        id="tomorrow-by-12pm-premium-yes",
        requires_customer_identity=True,
    )
)


_register(
    Scenario(
        "Book haircut premium 10am then confirm",
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
                missing_slots=[],
            ),
            after=_assert_no_booking,
        ),
        Turn(
            "yes",
            Expect(
                planner="READY",
                stage="CONFIRM",
                action="CONFIRM_APPOINTMENT",
                confirmation=None,
                missing_slots=[],
                session_slots={"service_id": PREMIUM_SERVICE},
                slot_contains={"time": "10"},
            ),
            after=_assert_booking_created,
        ),
        fixture="booking",
        tags=["booking", "happy-path", "confirm"],
        id="book-haircut-premium-10am-yes",
        requires_customer_identity=True,
    )
)


_register(
    Scenario(
        "Duplicate yes after successful commit",
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
                action="SEARCH_AVAILABILITY",
                session_slots={"service_id": PREMIUM_SERVICE},
                has_availability_slots=True,
            ),
        ),
        Turn(
            "10am",
            Expect(
                response_status="AWAITING_CONFIRMATION",
                confirmation="pending",
                slot_contains={"time": "10"},
            ),
            after=_assert_no_booking,
        ),
        Turn(
            "yes",
            Expect(
                action="CONFIRM_APPOINTMENT",
                confirmation=None,
                session_slots={"service_id": PREMIUM_SERVICE},
            ),
            after=_capture_committed_booking,
        ),
        Turn(
            "yes",
            after=_assert_duplicate_yes_idempotent,
        ),
        fixture="booking",
        tags=["booking", "idempotency", "confirmation", "audit"],
        id="duplicate-yes-after-commit",
        requires_customer_identity=True,
    )
)
# ============================================================
# REFERENCE EXPRESSIONS
# ============================================================
# (no scenarios in this section yet)
# ============================================================
# REVISIONS
# ============================================================
_register(
    Scenario(
        "Multiple successive revisions then confirm",
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
                action="SEARCH_AVAILABILITY",
                session_slots={"service_id": PREMIUM_SERVICE},
                has_availability_slots=True,
            ),
        ),
        Turn(
            "10am",
            Expect(
                response_status="AWAITING_CONFIRMATION",
                confirmation="pending",
                session_slots={"service_id": PREMIUM_SERVICE},
                slot_contains={"time": "10"},
            ),
            after=_capture_revision_search,
        ),
        Turn(
            "rather book flexi haircut",
            Expect(
                intent="CREATE_APPOINTMENT",
                session_slots={"service_id": FLEXI_SERVICE},
                confirmation=None,
            ),
            before=_clear_sticky_temporal_facts,
            after=_assert_flexi_revision,
        ),
        Turn(
            "actually July 12",
            Expect(
                intent="CREATE_APPOINTMENT",
                action="SEARCH_AVAILABILITY",
                session_slots={"service_id": FLEXI_SERVICE},
                confirmation=None,
                date_proposal=_JULY_12,
                slot_absent=["date", "time"],
                availability_invalidated=True,
            ),
            before=_clear_sticky_temporal_facts,
            after=_assert_date_revision_july12,
        ),
        Turn(
            "11am",
            Expect(
                response_status="AWAITING_CONFIRMATION",
                confirmation="pending",
                session_slots={"service_id": FLEXI_SERVICE},
                slot_contains={"time": "11"},
            ),
            after=_capture_revision_search,
        ),
        Turn(
            "rather book premium haircut",
            Expect(
                intent="CREATE_APPOINTMENT",
                session_slots={"service_id": PREMIUM_SERVICE},
                confirmation=None,
            ),
            before=_clear_sticky_temporal_facts,
            after=_assert_premium_rerevision,
        ),
        Turn(
            "10am",
            Expect(
                response_status="AWAITING_CONFIRMATION",
                confirmation="pending",
                session_slots={"service_id": PREMIUM_SERVICE},
                slot_contains={"time": "10"},
            ),
            after=_assert_no_booking,
        ),
        Turn(
            "yes",
            Expect(
                action="CONFIRM_APPOINTMENT",
                confirmation=None,
                session_slots={"service_id": PREMIUM_SERVICE},
                slot_contains={"time": "10"},
            ),
            after=_assert_final_multi_revision_booking,
        ),
        fixture="scripted_july_confirm_date_shift",
        tags=["booking", "revision", "invalidation", "audit"],
        id="multiple-successive-revisions-then-confirm",
        requires_customer_identity=True,
    )
)
# ============================================================
# INTERRUPTIONS
# ============================================================
_register(
    Scenario(
        "Hard session reload across booking lifecycle",
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
            after=_capture_reload_search,
        ),
        Turn(
            "Does a lion lay eggs?",
            Expect(
                response_status="OFF_TOPIC",
                intent="CREATE_APPOINTMENT",
                session_slots={"service_id": PREMIUM_SERVICE},
                confirmation="pending",
                response_text_present=True,
            ),
            after=_assert_digression_preserves_pending,
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
        fixture="scripted_off_topic",
        tags=["booking", "session", "reload", "audit"],
        id="hard-session-reload-booking-lifecycle",
        requires_customer_identity=True,
        force_session_reload=True,
    )
)
# ============================================================
# INVALID INPUT
# ============================================================
_register(
    Scenario(
        "Gibberish after commit does not create a second booking",
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
                action="SEARCH_AVAILABILITY",
                session_slots={"service_id": PREMIUM_SERVICE},
                has_availability_slots=True,
            ),
        ),
        Turn(
            "10am",
            Expect(
                response_status="AWAITING_CONFIRMATION",
                confirmation="pending",
                slot_contains={"time": "10"},
            ),
            after=_assert_no_booking,
        ),
        Turn(
            "yes",
            Expect(
                action="CONFIRM_APPOINTMENT",
                confirmation=None,
                session_slots={"service_id": PREMIUM_SERVICE},
            ),
            after=_capture_committed_booking,
        ),
        Turn(
            "asdfghjkl",
            Expect(
                intent="CREATE_APPOINTMENT",
                confirmation=None,
                response_text_present=True,
            ),
            after=_assert_duplicate_yes_idempotent,
        ),
        fixture="booking",
        tags=["booking", "completed", "invalid", "idempotency"],
        id="gibberish-after-commit-no-second-booking",
        requires_customer_identity=True,
    )
)
# ============================================================
# RECOVERY
# ============================================================
# (no scenarios in this section yet)
