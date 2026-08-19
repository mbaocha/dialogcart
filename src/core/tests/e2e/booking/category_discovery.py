"""Booking E2Es for optional service-category discovery."""

from __future__ import annotations

from typing import Any, List

from core.tests.e2e.framework.conversation import Expect, Scenario, Turn


PREMIUM_HAIRCUT_ID = 1001
HAIR_COLOURING_ID = 1002

CATEGORIZED_SERVICES = [
    {
        "id": PREMIUM_HAIRCUT_ID,
        "name": "Premium Haircut",
        "description": "A haircut with wash and styling.",
        "category": "Hair",
        "duration": 60,
        "is_active": True,
    },
    {
        "id": HAIR_COLOURING_ID,
        "name": "Hair Colouring",
        "description": "Professional colour treatment.",
        "category": "Hair",
        "duration": 60,
        "is_active": True,
    },
    {
        "id": 2001,
        "name": "Manicure",
        "description": "Nail shaping and polish.",
        "category": "Nails",
        "duration": 60,
        "is_active": True,
    },
]


def _presentation(conv: Any) -> dict[str, Any]:
    session = conv.session()
    presentation = session.get("catalogue_presentation")
    if not isinstance(presentation, dict):
        planning = session.get("planning")
        if isinstance(planning, dict):
            presentation = planning.get("catalogue_presentation")
    conv._assert(
        isinstance(presentation, dict),
        f"turn {conv.turn}: expected a persisted catalogue presentation, got {presentation!r}",
    )
    return presentation


def _assert_browsing(
    conv: Any,
    booking: Any,
    availability: Any,
    *,
    kind: str,
    labels: list[str],
) -> None:
    presentation = _presentation(conv)
    actual_labels = [option.get("label") for option in presentation.get("options", [])]
    conv._assert(
        presentation.get("kind") == kind,
        f"turn {conv.turn}: expected {kind!r} presentation, got {presentation!r}",
    )
    conv._assert(
        actual_labels == labels,
        f"turn {conv.turn}: expected options {labels!r}, got {actual_labels!r}",
    )
    session_slots = conv.session().get("slots") or {}
    conv._assert(
        not session_slots.get("service_id"),
        f"turn {conv.turn}: browsing must not fill service_id, got {session_slots!r}",
    )
    conv._assert(
        availability.get_service_availability.call_count == 0,
        f"turn {conv.turn}: availability ran before service selection",
    )
    conv._assert(
        booking.create_booking.call_count == 0,
        f"turn {conv.turn}: booking ran before confirmation",
    )


def _assert_categories(conv: Any, booking: Any, availability: Any) -> None:
    _assert_browsing(
        conv,
        booking,
        availability,
        kind="category",
        labels=["Hair", "Nails"],
    )


def _assert_hair_services(conv: Any, booking: Any, availability: Any) -> None:
    _assert_browsing(
        conv,
        booking,
        availability,
        kind="service",
        labels=["Premium Haircut", "Hair Colouring"],
    )


def _assert_single_availability_search(conv: Any, booking: Any, availability: Any) -> None:
    conv._assert(
        availability.get_service_availability.call_count == 1,
        f"turn {conv.turn}: expected one availability search after service selection",
    )
    conv._assert(
        booking.create_booking.call_count == 0,
        f"turn {conv.turn}: service selection must not bypass confirmation",
    )


SCENARIOS: List[Scenario] = [
    Scenario(
        "Service category selected by name before booking",
        Turn(
            "Book an appointment tomorrow at 10am",
            Expect(slot_absent=["service_id"], response_text_present=True),
            after=_assert_categories,
        ),
        Turn(
            "Hair",
            Expect(slot_absent=["service_id"], response_text_present=True),
            after=_assert_hair_services,
        ),
        Turn(
            "Premium Haircut",
            Expect(
                session_slots={"service_id": PREMIUM_HAIRCUT_ID},
                execution="availability",
                availability_request={"service_id": PREMIUM_HAIRCUT_ID},
                response_text_present=True,
            ),
            after=_assert_single_availability_search,
        ),
        fixture="scripted",
        tags=["booking", "category-discovery", "name-selection", "regression"],
        id="service-category-name-selection",
        catalog_service_records=CATEGORIZED_SERVICES,
    ),
    Scenario(
        "Service category and service selected by ordinal",
        Turn(
            "Book an appointment tomorrow at 10am",
            Expect(slot_absent=["service_id"], response_text_present=True),
            after=_assert_categories,
        ),
        Turn(
            "the first one",
            Expect(slot_absent=["service_id"], response_text_present=True),
            after=_assert_hair_services,
        ),
        Turn(
            "the second one",
            Expect(
                session_slots={"service_id": HAIR_COLOURING_ID},
                execution="availability",
                availability_request={"service_id": HAIR_COLOURING_ID},
                response_text_present=True,
            ),
            after=_assert_single_availability_search,
        ),
        fixture="scripted",
        tags=["booking", "category-discovery", "ordinal-selection", "regression"],
        id="service-category-ordinal-selection",
        catalog_service_records=CATEGORIZED_SERVICES,
    ),
]
