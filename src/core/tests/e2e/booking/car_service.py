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
