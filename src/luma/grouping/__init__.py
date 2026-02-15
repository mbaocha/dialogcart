"""
Stage 3: Entity Grouping & Alignment

Appointment/reservation booking grouping for service-based businesses.

Groups extracted entities into a single BOOK_APPOINTMENT intent.
Resolves user intent using rule-based logic.
"""

from luma.grouping.appointment_grouper import (
    BOOK_APPOINTMENT_INTENT,
    STATUS_NEEDS_CLARIFICATION,
    STATUS_OK,
    group_appointment,
)
from luma.grouping.reservation_intent_resolver import (
    AVAILABILITY,
    BOOKING_INQUIRY,
    CANCEL_BOOKING,
    CREATE_BOOKING,
    DETAILS,
    DISCOVERY,
    MODIFY_BOOKING,
    PAYMENT,
    QUOTE,
    RECOMMENDATION,
    UNKNOWN,
    ReservationIntentResolver,
    resolve_intent,
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
