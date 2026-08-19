"""Booking E2E scenarios — completed conversation state."""

# ============================================================
# Covered
#
# ✓ Valid
# ✓ Revision
# ✓ Interruptions
# ✓ Invalid
# ✓ Recovery
#
# TODO
#
# □ References
# ============================================================

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from core.planning.time_resolution import TIME_MATCH_EXACT, TIME_MATCH_MISMATCH
from core.tests.e2e.framework.conversation import (
    Expect,
    FLEXI_SERVICE,
    FLEXI_SERVICE_ITEM_ID,
    FIRST_AVAILABLE_DATE,
    FROZEN_TIME,
    ORG_ID,
    PREMIUM_SERVICE,
    PREMIUM_SERVICE_ITEM_ID,
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
            after=_assert_no_search_yet,
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
            after=_assert_booking_created_with_exact_payload(
                expected_item_id=PREMIUM_SERVICE_ITEM_ID,
                expected_service_id=PREMIUM_SERVICE,
                expected_date=FIRST_AVAILABLE_DATE,
                expected_time="10:00",
                abandoned_values=(FLEXI_SERVICE,),
            ),
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
            ),
            after=_assert_final_multi_revision_booking,
        ),
        fixture="scripted_july_confirm_date_shift",
        tags=["booking", "revision", "invalidation", "audit"],
        id="multiple-successive-revisions-then-confirm",
        requires_customer_identity=True,
    )
)


# Next Monday / Saturday relative to FROZEN_TIME 2026-07-01.
_NEXT_MONDAY = "2026-07-06"
_SATURDAY = "2026-07-04"


_register(
    Scenario(
        "Service and date revision to Flexi next Monday then confirm",
        Turn(
            "book me a premium haircut",
            Expect(
                response_status="succeeded",
                action="SEARCH_AVAILABILITY",
                session_slots={"service_id": PREMIUM_SERVICE},
                has_availability_slots=True,
            ),
            after=_capture_revision_search,
        ),
        Turn(
            "No, do Flexi next Monday.",
            Expect(
                intent="CREATE_APPOINTMENT",
                action="SEARCH_AVAILABILITY",
                session_slots={"service_id": FLEXI_SERVICE},
                confirmation=None,
                date_proposal=_NEXT_MONDAY,
                slot_absent=["date", "time"],
                availability_invalidated=True,
            ),
            after=_assert_flexi_revision,
        ),
        Turn(
            "10am",
            Expect(
                response_status="AWAITING_CONFIRMATION",
                confirmation="pending",
                session_slots={"service_id": FLEXI_SERVICE},
                slot_contains={"time": "10"},
                date_proposal=_NEXT_MONDAY,
            ),
            after=_assert_no_booking,
        ),
        Turn(
            "yes",
            Expect(
                action="CONFIRM_APPOINTMENT",
                confirmation=None,
                session_slots={"service_id": FLEXI_SERVICE},
                slot_contains={"time": "10"},
                date_proposal=_NEXT_MONDAY,
            ),
            after=_assert_booking_created_with_exact_payload(
                expected_item_id=FLEXI_SERVICE_ITEM_ID,
                expected_service_id=FLEXI_SERVICE,
                expected_date=_NEXT_MONDAY,
                expected_time="10:00",
            ),
        ),
        fixture="scripted_confirm",
        tags=["booking", "revision", "invalidation"],
        id="service-and-date-revision-flexi-next-monday",
        requires_customer_identity=True,
    )
)


_register(
    Scenario(
        "Informal language and self-correction to Saturday then confirm",
        Turn(
            "Can you fit me in for a premium trim tomorrow?",
            Expect(
                response_status="succeeded",
                action="SEARCH_AVAILABILITY",
                session_slots={"service_id": PREMIUM_SERVICE},
                has_availability_slots=True,
                confirmation=None,
                date_proposal=_TOMORROW,
            ),
        ),
        Turn(
            "10am pelase",
            Expect(
                response_status="AWAITING_CONFIRMATION",
                confirmation="pending",
                session_slots={"service_id": PREMIUM_SERVICE},
                slot_contains={"time": "10"},
            ),
            after=_capture_searches,
        ),
        Turn(
            "Friday—sorry, Saturday.",
            Expect(
                intent="CREATE_APPOINTMENT",
                action="SEARCH_AVAILABILITY",
                session_slots={"service_id": PREMIUM_SERVICE},
                confirmation=None,
                date_proposal=_SATURDAY,
                slot_absent=["date", "time"],
                availability_invalidated=True,
            ),
            after=_assert_date_revision_without_stale_time,
        ),
        Turn(
            "10am",
            Expect(
                response_status="AWAITING_CONFIRMATION",
                confirmation="pending",
                session_slots={"service_id": PREMIUM_SERVICE},
                slot_contains={"time": "10"},
                date_proposal=_SATURDAY,
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
                date_proposal=_SATURDAY,
            ),
            after=_assert_booking_created_with_exact_payload(
                expected_item_id=PREMIUM_SERVICE_ITEM_ID,
                expected_service_id=PREMIUM_SERVICE,
                expected_date=_SATURDAY,
                expected_time="10:00",
                abandoned_values=(_TOMORROW, "2026-07-03T10:00"),
            ),
        ),
        fixture="scripted_confirm",
        tags=["booking", "revision", "invalidation"],
        id="informal-language-self-correction-saturday",
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
            ),
            after=_capture_committed_booking,
        ),
        Turn(
            "asdfghjkl",
            Expect(
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
_SERVICE_RETAIN_STATE: Dict[str, Any] = {}


def _effective_service_id_completed(sess: Dict[str, Any]) -> Any:
    slots = sess.get("slots") if isinstance(sess.get("slots"), dict) else {}
    planning = sess.get("planning") if isinstance(sess.get("planning"), dict) else {}
    planning_slots = (
        planning.get("slots") if isinstance(planning.get("slots"), dict) else {}
    )
    return planning_slots.get("service_id") or slots.get("service_id")


def _capture_service_before_recovery(conv, booking, availability) -> None:
    """Service is established; availability has no slots."""
    _assert_no_booking(conv, booking)
    assert availability.get_service_availability.call_count >= 1
    sess = conv.session() or {}
    service_id = _effective_service_id_completed(sess)
    assert service_id == PREMIUM_SERVICE, (
        f"turn {conv.turn}: expected premium before recovery continue, got {service_id!r}"
    )
    text = _response_text(conv.last_body or {})
    assert isinstance(text, str) and text.strip(), (
        f"turn {conv.turn}: expected non-empty no-availability reply, got {text!r}"
    )
    _SERVICE_RETAIN_STATE["service_id"] = service_id
    _SERVICE_RETAIN_STATE["text"] = text


def _assert_service_retained_across_recovery(conv, booking, _availability=None) -> None:
    """'different week' / 'continue' must keep service — never re-ask which service."""
    _assert_no_booking(conv, booking)
    text = _response_text(conv.last_body or {})
    assert isinstance(text, str) and text.strip(), (
        f"turn {conv.turn}: expected non-empty recovery reply, got {text!r}"
    )
    lowered = text.lower().replace("\u2019", "'").replace("\u2018", "'")
    assert "which service" not in lowered, (
        f"turn {conv.turn}: must not re-ask 'Which service?' after service was "
        f"already selected, got {text!r}"
    )
    # Choice-list re-prompt is also a service re-ask.
    lists_services = (
        "premium haircut" in lowered
        and "flexi" in lowered
        and ("which" in lowered or "would you like" in lowered)
    )
    assert not lists_services, (
        f"turn {conv.turn}: must not re-present service picker after service was "
        f"selected, got {text!r}"
    )

    sess = conv.session() or {}
    expected = _SERVICE_RETAIN_STATE.get("service_id") or PREMIUM_SERVICE
    service_id = _effective_service_id_completed(sess)
    assert service_id == expected, (
        f"turn {conv.turn}: previously selected service must remain {expected!r}, "
        f"got {service_id!r}"
    )
    missing = (
        (conv.plan or {}).get("missing_slots")
        or (conv.outcome or {}).get("missing_slots")
        or sess.get("missing_slots")
        or []
    )
    if isinstance(missing, list):
        assert "service_id" not in missing, (
            f"turn {conv.turn}: service_id must not be missing after recovery, "
            f"got missing_slots={missing!r}"
        )


_register(
    Scenario(
        "Service retained across different-week recovery",
        Turn(
            "book me a premium haircut tomorrow",
            Expect(
                response_status="succeeded",
                execution="availability",
                has_availability_slots=False,
                intent="CREATE_APPOINTMENT",
                session_slots={"service_id": PREMIUM_SERVICE},
                confirmation=None,
                response_text_present=True,
            ),
            after=_capture_service_before_recovery,
        ),
        Turn(
            "different week",
            Expect(
                intent="CREATE_APPOINTMENT",
                session_slots={"service_id": PREMIUM_SERVICE},
                confirmation=None,
                response_text_present=True,
            ),
            after=_assert_service_retained_across_recovery,
        ),
        fixture="scripted_empty",
        tags=["booking", "completed", "recovery", "service-retention", "regression"],
        id="service-retained-across-different-week-recovery",
    )
)

_register(
    Scenario(
        "Service retained across continue recovery",
        Turn(
            "book me a premium haircut tomorrow",
            Expect(
                response_status="succeeded",
                execution="availability",
                has_availability_slots=False,
                intent="CREATE_APPOINTMENT",
                session_slots={"service_id": PREMIUM_SERVICE},
                confirmation=None,
                response_text_present=True,
            ),
            after=_capture_service_before_recovery,
        ),
        Turn(
            "continue",
            Expect(
                intent="CREATE_APPOINTMENT",
                session_slots={"service_id": PREMIUM_SERVICE},
                confirmation=None,
                response_text_present=True,
            ),
            after=_assert_service_retained_across_recovery,
        ),
        fixture="scripted_empty",
        tags=["booking", "completed", "recovery", "service-retention", "regression"],
        id="service-retained-across-continue-recovery",
    )
)
