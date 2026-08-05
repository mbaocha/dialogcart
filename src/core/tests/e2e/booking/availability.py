"""Booking E2E scenarios — availability conversation state."""

# ============================================================
# Covered
#
# ✓ Valid
# ✓ References
# ✓ Revision
# ✓ Interruptions
# ✓ Invalid
# ✓ Recovery
#
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
# INTERRUPTIONS
# ============================================================
# (booking-specific OFF_TOPIC interruptions while selecting time)

# --- OFF_TOPIC interruptions during availability / time selection ---
from core.tests.e2e.framework.confirmation_interruption import (
    assert_availability_rendered,
    assert_no_search_since,
)
from core.tests.e2e.framework.turn_understanding import (
    assert_understanding_everywhere,
    session_fingerprint,
    session_service_id,
)

_OFF_TOPIC_STATE: Dict[str, Any] = {}
_UNDERSTOOD = "UNDERSTOOD"
_UNRECOGNIZED = "UNRECOGNIZED_INPUT"
_OFF_TOPIC_JULY_21 = "2026-07-21"
_RECOVERY_PHRASES = (
    "didn't understand",
    "did not understand",
    "i didn't understand",
    "sorry, i didn't understand",
)
_SHOW_MORE_MARKER = "show more"


def _off_topic_luma(conv) -> Any:
    luma = getattr(conv, "luma_client", None)
    conv._assert(luma is not None, f"turn {conv.turn}: luma_client missing on conversation")
    return luma


def _assert_off_topic_digression(conv, booking, _availability) -> None:
    assert_understanding_everywhere(conv, _off_topic_luma(conv), _UNDERSTOOD)
    assert_no_booking_execution(conv, booking)

    outcome = conv.outcome or {}
    conv._assert(
        outcome.get("status") == "OFF_TOPIC",
        f"turn {conv.turn}: expected OFF_TOPIC, got {outcome.get('status')!r}",
    )
    conv._assert(
        outcome.get("active_handler") in (None, "", False),
        (
            f"turn {conv.turn}: expected no active_handler for Core OFF_TOPIC, "
            f"got {outcome.get('active_handler')!r}"
        ),
    )
    conv._assert(
        outcome.get("intent_name") == "OFF_TOPIC",
        f"turn {conv.turn}: outcome intent expected OFF_TOPIC, got {outcome.get('intent_name')!r}",
    )

    text = _response_text(conv.last_body or {})
    lowered = text.lower()
    conv._assert(bool(text.strip()), f"turn {conv.turn}: expected OFF_TOPIC response text")
    for phrase in _RECOVERY_PHRASES:
        conv._assert(
            phrase not in lowered,
            f"turn {conv.turn}: recovery renderer must not run for OFF_TOPIC, got {text!r}",
        )


def _capture_booking_baseline(conv, _booking, _availability) -> None:
    sess = conv.session() or {}
    _OFF_TOPIC_STATE["intent"] = sess.get("intent_name")
    _OFF_TOPIC_STATE["slots"] = dict(sess.get("slots") or {})
    _OFF_TOPIC_STATE["confirmation"] = sess.get("confirmation_state")
    _OFF_TOPIC_STATE["date_proposal"] = sess.get("date_proposal")
    _OFF_TOPIC_STATE["time_proposal"] = sess.get("time_proposal")
    planning = sess.get("planning") if isinstance(sess.get("planning"), dict) else {}
    _OFF_TOPIC_STATE["planning_slots"] = dict(planning.get("slots") or {})
    _OFF_TOPIC_STATE["service_id"] = session_service_id(sess)


def _assert_mid_booking_off_topic_preserves(conv, booking, availability) -> None:
    _assert_off_topic_digression(conv, booking, availability)

    sess = conv.session() or {}
    conv._assert(
        sess.get("intent_name") == _OFF_TOPIC_STATE.get("intent"),
        (
            f"turn {conv.turn}: session intent must stay {_OFF_TOPIC_STATE.get('intent')!r}, "
            f"got {sess.get('intent_name')!r}"
        ),
    )
    conv._assert(
        dict(sess.get("slots") or {}) == _OFF_TOPIC_STATE.get("slots"),
        (
            f"turn {conv.turn}: session slots must be unchanged, "
            f"got {sess.get('slots')!r} vs {_OFF_TOPIC_STATE.get('slots')!r}"
        ),
    )
    conv._assert(
        sess.get("confirmation_state") == _OFF_TOPIC_STATE.get("confirmation"),
        (
            f"turn {conv.turn}: confirmation_state must stay {_OFF_TOPIC_STATE.get('confirmation')!r}, "
            f"got {sess.get('confirmation_state')!r}"
        ),
    )
    conv._assert(
        sess.get("date_proposal") == _OFF_TOPIC_STATE.get("date_proposal"),
        f"turn {conv.turn}: date_proposal must be unchanged",
    )
    conv._assert(
        sess.get("time_proposal") == _OFF_TOPIC_STATE.get("time_proposal"),
        f"turn {conv.turn}: time_proposal must be unchanged",
    )
    planning = sess.get("planning") if isinstance(sess.get("planning"), dict) else {}
    conv._assert(
        dict(planning.get("slots") or {}) == _OFF_TOPIC_STATE.get("planning_slots"),
        f"turn {conv.turn}: planning.slots must be unchanged",
    )
    if _OFF_TOPIC_STATE.get("service_id"):
        conv._assert(
            session_service_id(sess) == _OFF_TOPIC_STATE.get("service_id"),
            (
                f"turn {conv.turn}: service_id must remain {_OFF_TOPIC_STATE.get('service_id')!r}, "
                f"got {session_service_id(sess)!r}"
            ),
        )

    text = _response_text(conv.last_body or {}).lower()
    conv._assert(
        "booking" in text or "continue" in text or "time" in text or "appointment" in text
        or "service" in text or "date" in text,
        f"turn {conv.turn}: mid-booking decline should guide back to booking, got {text!r}",
    )


def _mentions_show_more(text: str) -> bool:
    return _SHOW_MORE_MARKER in (text or "").lower()


def _capture_availability_after_premium(conv, booking, availability) -> None:
    """Baseline after times are shown: search, presentation, show-more guidance."""
    _capture_booking_baseline(conv, booking, availability)
    assert_availability_rendered(conv)
    sess = conv.session() or {}
    text = _response_text(conv.last_body or {})
    _OFF_TOPIC_STATE["search_count"] = availability.get_service_availability.call_count
    _OFF_TOPIC_STATE["fingerprint"] = session_fingerprint(sess)
    _OFF_TOPIC_STATE["presented_times"] = extract_presented_times(conv.last_body or {}, sess)
    _OFF_TOPIC_STATE["availability_text"] = text
    _OFF_TOPIC_STATE["availability_mentions_show_more"] = _mentions_show_more(text)


def _assert_off_topic_resume_after_times(conv, booking, availability) -> None:
    """OFF_TOPIC with factual answer must resume time selection without re-search."""
    _assert_mid_booking_off_topic_preserves(conv, booking, availability)
    assert_no_search_since(conv, availability, _OFF_TOPIC_STATE.get("search_count", 0))

    text = _response_text(conv.last_body or {})
    lowered = text.lower()
    conv._assert(
        "lion" in lowered or "egg" in lowered or "no" in lowered or "mammal" in lowered,
        (
            f"turn {conv.turn}: expected a factual answer about the lion question, "
            f"got {text!r}"
        ),
    )
    conv._assert(
        "time" in lowered or "continue" in lowered or "booking" in lowered,
        f"turn {conv.turn}: OFF_TOPIC reply must resume time selection, got {text!r}",
    )


def _assert_resume_show_more_consistency(conv, booking, availability) -> None:
    """Resumed OFF_TOPIC prompt must match original availability show-more guidance."""
    _assert_off_topic_resume_after_times(conv, booking, availability)
    text = _response_text(conv.last_body or {})
    resume_mentions = _mentions_show_more(text)
    original_mentions = bool(_OFF_TOPIC_STATE.get("availability_mentions_show_more"))
    conv._assert(
        resume_mentions == original_mentions,
        (
            f"turn {conv.turn}: resume show-more guidance must match original "
            f"availability prompt "
            f"(original={original_mentions}, resume={resume_mentions}). "
            f"availability={_OFF_TOPIC_STATE.get('availability_text')!r} resume={text!r}"
        ),
    )


def _assert_unrecognized_after_off_topic(conv, booking, availability) -> None:
    """Unrecognized input after OFF_TOPIC must acknowledge, then resume — not discard."""
    assert_understanding_everywhere(conv, _off_topic_luma(conv), _UNRECOGNIZED)
    assert_no_booking_execution(conv, booking)
    assert_no_search_since(conv, availability, _OFF_TOPIC_STATE.get("search_count", 0))

    sess = conv.session() or {}
    conv._assert(
        sess.get("intent_name") == _OFF_TOPIC_STATE.get("intent"),
        (
            f"turn {conv.turn}: booking must not restart "
            f"(intent {_OFF_TOPIC_STATE.get('intent')!r} -> {sess.get('intent_name')!r})"
        ),
    )
    expected_service = _OFF_TOPIC_STATE.get("service_id") or PREMIUM_SERVICE
    conv._assert(
        session_service_id(sess) == expected_service,
        (
            f"turn {conv.turn}: service_id must remain {expected_service!r}, "
            f"got {session_service_id(sess)!r}"
        ),
    )
    conv.assert_date_proposal(_OFF_TOPIC_JULY_21)
    fp = session_fingerprint(sess)
    conv._assert(
        fp == _OFF_TOPIC_STATE.get("fingerprint"),
        (
            f"turn {conv.turn}: availability fingerprint must be unchanged "
            f"({_OFF_TOPIC_STATE.get('fingerprint')!r} -> {fp!r})"
        ),
    )
    presented = extract_presented_times(conv.last_body or {}, sess)
    baseline_times = _OFF_TOPIC_STATE.get("presented_times")
    if baseline_times:
        conv._assert(
            presented == baseline_times,
            (
                f"turn {conv.turn}: presented times must not be regenerated "
                f"({baseline_times!r} -> {presented!r})"
            ),
        )

    text = _response_text(conv.last_body or {})
    lowered = text.lower()
    conv._assert(
        bool(text.strip()),
        f"turn {conv.turn}: unrecognized interruption must not be silent, got {text!r}",
    )
    conv._assert(
        any(phrase in lowered for phrase in _RECOVERY_PHRASES)
        or "understand" in lowered,
        (
            f"turn {conv.turn}: must acknowledge unrecognized input first, "
            f"got {text!r}"
        ),
    )
    conv._assert(
        "time" in lowered
        or "booking" in lowered
        or "continue" in lowered
        or "returning" in lowered
        or "works best" in lowered,
        (
            f"turn {conv.turn}: after acknowledgment must resume pending time "
            f"selection, got {text!r}"
        ),
    )


_register(
    Scenario(
        "Mid-booking OFF_TOPIC preserves booking",
        Turn(
            "Book haircut",
            Expect(
                planner="NEEDS_CLARIFICATION",
                intent="CREATE_APPOINTMENT",
                response_text_present=True,
            ),
        ),
        Turn(
            "Premium",
            Expect(
                intent="CREATE_APPOINTMENT",
                session_slots={"service_id": PREMIUM_SERVICE},
                response_text_present=True,
            ),
            after=_capture_booking_baseline,
        ),
        Turn(
            "Who is the president of Nigeria?",
            Expect(
                response_status="OFF_TOPIC",
                # Session intent stays durable booking; outcome is OFF_TOPIC.
                intent="CREATE_APPOINTMENT",
                session_slots={"service_id": PREMIUM_SERVICE},
                response_text_present=True,
            ),
            after=_assert_mid_booking_off_topic_preserves,
        ),
        fixture="scripted_off_topic",
        tags=["off_topic", "booking", "regression"],
        id="off-topic-mid-booking-preserves",
    )
)


_register(
    Scenario(
        "OFF_TOPIC then unrecognized resumes booking",
        Turn(
            "book me a haircut on July 21",
            Expect(
                planner="NEEDS_CLARIFICATION",
                intent="CREATE_APPOINTMENT",
                missing_slots=["service_id", "time"],
                date_proposal=_OFF_TOPIC_JULY_21,
                response_text_present=True,
            ),
        ),
        Turn(
            "premium",
            Expect(
                response_status="succeeded",
                planner="READY",
                stage="AVAILABILITY",
                action="SEARCH_AVAILABILITY",
                intent="CREATE_APPOINTMENT",
                session_slots={"service_id": PREMIUM_SERVICE},
                execution="availability",
                has_availability_slots=True,
                date_proposal=_OFF_TOPIC_JULY_21,
                response_text_present=True,
            ),
            after=_capture_availability_after_premium,
        ),
        Turn(
            "Does a lion lay eggs?",
            Expect(
                response_status="OFF_TOPIC",
                intent="CREATE_APPOINTMENT",
                session_slots={"service_id": PREMIUM_SERVICE},
                response_text_present=True,
            ),
            after=_assert_off_topic_resume_after_times,
        ),
        Turn(
            "aaaa",
            Expect(
                planner="READY",
                intent="CREATE_APPOINTMENT",
                missing_slots=["time"],
                session_slots={"service_id": PREMIUM_SERVICE},
                date_proposal=_OFF_TOPIC_JULY_21,
                action=None,
                response_text_present=True,
            ),
            after=_assert_unrecognized_after_off_topic,
        ),
        fixture="scripted_off_topic",
        tags=["off_topic", "recovery", "interruption", "regression"],
        id="off-topic-then-unrecognized-resumes",
    )
)


_register(
    Scenario(
        "OFF_TOPIC resume matches availability show-more guidance",
        Turn(
            "book haircut on July 21",
            Expect(
                planner="NEEDS_CLARIFICATION",
                intent="CREATE_APPOINTMENT",
                missing_slots=["service_id", "time"],
                date_proposal=_OFF_TOPIC_JULY_21,
                response_text_present=True,
            ),
        ),
        Turn(
            "premium",
            Expect(
                response_status="succeeded",
                planner="READY",
                stage="AVAILABILITY",
                action="SEARCH_AVAILABILITY",
                intent="CREATE_APPOINTMENT",
                session_slots={"service_id": PREMIUM_SERVICE},
                execution="availability",
                has_availability_slots=True,
                date_proposal=_OFF_TOPIC_JULY_21,
                response_text_present=True,
            ),
            after=_capture_availability_after_premium,
        ),
        Turn(
            "Does a lion lay eggs?",
            Expect(
                response_status="OFF_TOPIC",
                intent="CREATE_APPOINTMENT",
                session_slots={"service_id": PREMIUM_SERVICE},
                response_text_present=True,
            ),
            after=_assert_resume_show_more_consistency,
        ),
        fixture="scripted_off_topic",
        tags=["off_topic", "resume", "availability", "regression"],
        id="off-topic-resume-show-more-consistency",
    )
)

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
_register(
    Scenario(
        "Invalid after times then valid time binds",
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
                slot_contains={"time": "13:30"},
                missing_slots=[],
            ),
            after=_assert_dotted_time_bound("1.30"),
        ),
        fixture="scripted_dotted_time_selection",
        tags=["booking", "availability", "recovery"],
        id="invalid-after-times-then-valid-time",
    )
)

_register(
    Scenario(
        "Off-topic after times then valid time binds",
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
            "Does a lion lay eggs?",
            Expect(
                response_status="OFF_TOPIC",
                action=None,
                intent="CREATE_APPOINTMENT",
                session_slots={"service_id": PREMIUM_SERVICE},
                confirmation=None,
                response_text_present=True,
            ),
            after=_assert_no_booking,
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
                slot_contains={"time": "13:30"},
                missing_slots=[],
            ),
            after=_assert_dotted_time_bound("1.30"),
        ),
        fixture="scripted_dotted_time_selection",
        tags=["booking", "availability", "recovery", "off_topic"],
        id="off-topic-after-times-then-valid-time",
    )
)

_register(
    Scenario(
        "FAQ after times then valid time binds",
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
            "how much does a haircut cost?",
            Expect(
                response_status="HANDLER_DELEGATED",
                action=None,
                intent="CREATE_APPOINTMENT",
                session_slots={"service_id": PREMIUM_SERVICE},
                confirmation=None,
                response_text_present=True,
            ),
            after=_assert_no_booking,
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
                slot_contains={"time": "13:30"},
                missing_slots=[],
            ),
            after=_assert_dotted_time_bound("1.30"),
        ),
        fixture="scripted_dotted_time_selection",
        tags=["booking", "availability", "recovery", "faq"],
        id="faq-after-times-then-valid-time",
    )
)
