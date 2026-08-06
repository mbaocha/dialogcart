"""Booking E2E scenarios for the car_service business category."""

from __future__ import annotations

from typing import List

from core.tests.e2e.framework.conversation import (
    Expect,
    Scenario,
    Turn,
    _response_text,
)

BUSINESS_CATEGORY = "car_service"
EXECUTIVE_OIL_CHANGE_SKU = "executive oil change"
EXECUTIVE_OIL_CHANGE_ID = 26
PREMIUM_FULL_SERVICE_SKU = "premium full service"
_RATTLE_RECOMMENDATION = (
    "For your rattling noise, the Premium Full Service would be the better choice "
    "since it includes the kind of checks that would help us pinpoint what's actually "
    "causing it. The oil change alone might resolve it if low oil is the culprit, but "
    "we wouldn't know without that fuller inspection.\n\nWould you like to book the "
    "Premium Full Service so we can diagnose that rattle for you?"
)


def _assert_recommendation_persisted(conv, _booking, _availability) -> None:
    response = _response_text(conv.last_body or {})
    conv._assert(
        response == _RATTLE_RECOMMENDATION,
        f"turn {conv.turn}: expected recorded recommendation, got {response!r}",
    )
    session = conv.session() or {}
    history = ((session.get("conversation") or {}).get("history") or [])
    conv._assert(
        {"role": "assistant", "text": _RATTLE_RECOMMENDATION} in history,
        f"turn {conv.turn}: recommendation did not follow normal history persistence",
    )
    turns = (((session.get("conversation") or {}).get("memory") or {}).get("turns") or [])
    conv._assert(
        bool(turns) and turns[-1].get("assistant") == _RATTLE_RECOMMENDATION,
        f"turn {conv.turn}: recommendation missing from conversation memory",
    )


def _assert_proposal_continuity(conv, _booking, _availability) -> None:
    session = conv.session() or {}
    slots = session.get("slots") or {}
    missing = session.get("missing_slots") or []
    response = _response_text(conv.last_body or {}).lower()
    conv._assert(
        slots.get("service_id") == PREMIUM_FULL_SERVICE_SKU,
        f"turn {conv.turn}: accepted proposal must select "
        f"{PREMIUM_FULL_SERVICE_SKU!r}, got {slots.get('service_id')!r}; "
        f"missing_slots={missing!r}; response={response!r}",
    )
    conv._assert(
        "service_id" not in missing and session.get("ask_next") != "service_id",
        f"turn {conv.turn}: accepted service must not require service clarification",
    )
    conv._assert(
        "brake pad" not in response,
        f"turn {conv.turn}: unrelated Brake Pad Change was introduced: {response!r}",
    )
    conv._assert(
        "which service" not in response,
        f"turn {conv.turn}: booking restarted service selection: {response!r}",
    )


def _assert_engine_type_not_requested_again(conv, booking, availability) -> None:
    session = conv.session() or {}
    missing_slots = session.get("missing_slots") or conv.outcome.get("missing_slots") or []
    conv._assert(
        "engine_type" not in missing_slots,
        f"turn {conv.turn}: engine_type must not be missing after petrol was accepted",
    )

    response = _response_text(conv.last_body or {}).lower()
    conv._assert(
        "engine type" not in response and "engine_type" not in response,
        f"turn {conv.turn}: assistant asked for engine_type again: {response!r}",
    )


SCENARIOS: List[Scenario] = [
    Scenario(
        "Car service accepts an assistant recommendation with next-week availability",
        Turn(
            "Hi, my car is making a weird rattling noise when I start it. Not sure what I need.",
            Expect(
                response_status="HANDLER_DELEGATED",
                intent="GENERAL_INQUIRY",
                response_text_present=True,
            ),
            after=_assert_recommendation_persisted,
        ),
        Turn(
            "Yeah, maybe next week. What have you got?",
            Expect(
                intent="CREATE_APPOINTMENT",
                stage="AVAILABILITY",
                date_proposal="2026-07-06",
                response_text_present=True,
            ),
            after=_assert_proposal_continuity,
        ),
        fixture="car_service_proposal_continuity",
        tags=["booking", "car-service", "proposal", "continuity", "regression"],
        id="car-service-proposal-acceptance-with-next-week",
    ),
    Scenario(
        "Car service engine type persists through confirmation",
        Turn(
            "Book me an Executive Oil Change",
            Expect(
                response_status="NEEDS_CLARIFICATION",
                planner="NEEDS_CLARIFICATION",
                intent="CREATE_APPOINTMENT",
                session_slots={"service_id": EXECUTIVE_OIL_CHANGE_SKU},
                response_text_present=True,
                confirmation=None,
            ),
        ),
        Turn(
            "petrol",
            Expect(
                response_status="succeeded",
                planner="READY",
                stage="AVAILABILITY",
                action="SEARCH_AVAILABILITY",
                intent="CREATE_APPOINTMENT",
                session_slots={
                    "service_id": EXECUTIVE_OIL_CHANGE_SKU,
                    "engine_type": "petrol",
                },
                execution="availability",
                has_availability_slots=True,
                availability_request={"service_id": EXECUTIVE_OIL_CHANGE_ID},
                availability_extra_params={"engine_type": "petrol"},
                response_text_present=True,
                confirmation=None,
            ),
            after=_assert_engine_type_not_requested_again,
        ),
        Turn(
            "10am",
            Expect(
                response_status="NEEDS_CLARIFICATION",
                planner="NEEDS_CLARIFICATION",
                stage="AVAILABILITY",
                action=None,
                intent="CREATE_APPOINTMENT",
                session_slots={
                    "service_id": EXECUTIVE_OIL_CHANGE_SKU,
                    "engine_type": "petrol",
                },
                slot_contains={"time": "10"},
                missing_slots=["registration_number"],
                confirmation=None,
                response_text_present=True,
            ),
            after=_assert_engine_type_not_requested_again,
        ),
        Turn(
            "AB12 CDE",
            Expect(
                response_status="AWAITING_CONFIRMATION",
                planner="AWAITING_CONFIRMATION",
                stage="CONFIRM",
                awaiting="USER_CONFIRMATION",
                action=None,
                intent="CREATE_APPOINTMENT",
                session_slots={
                    "service_id": EXECUTIVE_OIL_CHANGE_SKU,
                    "engine_type": "petrol",
                    "registration_number": "AB12 CDE",
                },
                slot_contains={"time": "10"},
                missing_slots=[],
                confirmation="pending",
                response_text_present=True,
            ),
            after=_assert_engine_type_not_requested_again,
        ),
        fixture="scripted_confirm",
        tags=["booking", "car-service", "engine-type", "regression"],
        id="car-service-engine-type-persists-through-confirmation",
    ),
    Scenario(
        "Car service date correction after empty availability",
        Turn(
            "Book me an Executive Oil Change tomorrow",
            Expect(
                response_status="NEEDS_CLARIFICATION",
                planner="NEEDS_CLARIFICATION",
                intent="CREATE_APPOINTMENT",
                session_slots={"service_id": EXECUTIVE_OIL_CHANGE_SKU},
                date_proposal="2026-07-02",
                response_text_present=True,
                confirmation=None,
            ),
        ),
        Turn(
            "petrol",
            Expect(
                response_status="succeeded",
                intent="CREATE_APPOINTMENT",
                session_slots={
                    "service_id": EXECUTIVE_OIL_CHANGE_SKU,
                    "engine_type": "petrol",
                },
                execution="availability",
                has_availability_slots=False,
                date_proposal="2026-07-02",
                availability_request={
                    "service_id": EXECUTIVE_OIL_CHANGE_ID,
                    "date": "2026-07-02",
                },
                availability_extra_params={"engine_type": "petrol"},
                confirmation=None,
            ),
        ),
        Turn(
            "what about next Monday instead",
            Expect(
                action="SEARCH_AVAILABILITY",
                intent="CREATE_APPOINTMENT",
                session_slots={
                    "service_id": EXECUTIVE_OIL_CHANGE_SKU,
                    "engine_type": "petrol",
                },
                execution="availability",
                has_availability_slots=False,
                date_proposal="2026-07-06",
                availability_request={
                    "service_id": EXECUTIVE_OIL_CHANGE_ID,
                    "date": "2026-07-06",
                },
                availability_extra_params={"engine_type": "petrol"},
                confirmation=None,
            ),
        ),
        fixture="scripted_empty",
        tags=[
            "booking",
            "car-service",
            "availability",
            "date-correction",
            "regression",
        ],
        id="car-service-date-correction-after-empty-availability",
    ),
]
