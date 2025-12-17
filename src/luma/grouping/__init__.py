"""
Stage 3: Entity Grouping & Alignment

Appointment/reservation booking grouping for service-based businesses.

Groups extracted entities into a single BOOK_APPOINTMENT intent.
Resolves user intent using rule-based logic.
"""

from luma.grouping.appointment_grouper import (
    group_appointment,
    BOOK_APPOINTMENT_INTENT,
    STATUS_OK,
    STATUS_NEEDS_CLARIFICATION,
)

from luma.grouping.reservation_intent_resolver import (
    ReservationIntentResolver,
    resolve_intent,
    DISCOVERY,
    DETAILS,
    AVAILABILITY,
    QUOTE,
    RECOMMENDATION,
    CREATE_BOOKING,
    BOOKING_INQUIRY,
    MODIFY_BOOKING,
    CANCEL_BOOKING,
    PAYMENT,
    UNKNOWN,
)

__all__ = [
    # Appointment grouping
    "group_appointment",
    "BOOK_APPOINTMENT_INTENT",
    "STATUS_OK",
    "STATUS_NEEDS_CLARIFICATION",
    # Intent resolution (10 production intents)
    "ReservationIntentResolver",
    "resolve_intent",
    "DISCOVERY",
    "DETAILS",
    "AVAILABILITY",
    "QUOTE",
    "RECOMMENDATION",
    "CREATE_BOOKING",
    "BOOKING_INQUIRY",
    "MODIFY_BOOKING",
    "CANCEL_BOOKING",
    "PAYMENT",
    "UNKNOWN",
]

