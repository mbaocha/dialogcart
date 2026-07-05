"""
E2E planning scenarios (live NLU on localhost:9002).

PRODUCT CONTRACT:
- Multi-turn while service_id is unresolved: disambiguation, FAQ/quote detours, then pick service.
- Once service_id resolves → READY → SEARCH_AVAILABILITY (present options; no date/time dribbling).
- Single-turn may include date/time proposals when the user states them upfront.

Removed: post-READY slot dribbling (service then "tomorrow"/"11am"), reservation date follow-ups
after service locked, date/time override turns, empty/"?" weak-input recovery on READY sessions.
"""

_AVAIL = {"stage": "AVAILABILITY", "action": "SEARCH_AVAILABILITY"}
_IDENTIFY = {"stage": "IDENTIFY", "action": "FETCH_BOOKING"}
_CONFIRM_CANCEL = {"stage": "CONFIRM", "action": "CONFIRM_CANCELLATION"}

_HAIRCUT_CATALOG = {
    "premium haircut": "haircut",
    "flexi haircut + prunning": "haircut",
}

_SPA_CATALOG = {
    "integration spa treatment": "spa.integration",
    "premium spa treatment": "spa.premium",
    "premium haircut": "haircut",
    "flexi haircut + prunning": "haircut",
}

planning_scenarios = [
    # --- CREATE_APPOINTMENT: single-turn (service → availability) ---
    {
        "id": 1,
        "name": "appointment_service_only",
        "domain": "service",
        "aliases": {"haircut": "haircut"},
        "turns": [
            {
                "sentence": "book a haircut",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": "READY",
                    "plan": _AVAIL,
                    "missing_slots": ["date", "time"],
                    "slots": {"service_id": "haircut"},
                },
            },
        ],
    },
    {
        "id": 2,
        "name": "appointment_service_and_date",
        "domain": "service",
        "aliases": {"haircut": "haircut"},
        "turns": [
            {
                "sentence": "book haircut tomorrow",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": "READY",
                    "plan": _AVAIL,
                    "missing_slots": ["time"],
                    "slots": {"service_id": "haircut"},
                    "date_proposal": {"mode": "single_day", "start": "2026-01-14"},
                },
            },
        ],
    },
    {
        "id": 3,
        "name": "appointment_service_date_and_time",
        "domain": "service",
        "aliases": {"massage": "massage"},
        "turns": [
            {
                "sentence": "book massage tomorrow at 3pm",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": "READY",
                    "plan": _AVAIL,
                    "missing_slots": [],
                    "slots": {"service_id": "massage"},
                    "date_proposal": {"mode": "single_day", "start": "2026-01-14"},
                    "time_proposal": {"mode": "exact", "value": "15:00"},
                },
            },
        ],
    },
    {
        "id": 4,
        "name": "single_turn_fuzzy_evening",
        "domain": "service",
        "aliases": {"haircut": "haircut"},
        "turns": [
            {
                "sentence": "book haircut friday evening",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": "READY",
                    "plan": _AVAIL,
                    "missing_slots": [],
                    "slots": {"service_id": "haircut"},
                    "date_proposal": {"mode": "single_day", "start": "2026-01-16"},
                    "time_proposal": {
                        "mode": "fuzzy",
                        "label": "evening",
                        "start": "17:00",
                        "end": "21:59",
                    },
                },
            },
        ],
    },
    {
        "id": 5,
        "name": "single_turn_ambiguous_week_no_single_date",
        "domain": "service",
        "aliases": {"facial": "facial"},
        "turns": [
            {
                "sentence": "book facial next week",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": "READY",
                    "plan": _AVAIL,
                    "missing_slots": ["date", "time"],
                    "slots": {"service_id": "facial"},
                },
            },
        ],
    },
    # --- CREATE_APPOINTMENT: multi-turn until service_id resolved ---
    {
        "id": 6,
        "name": "ambiguous_haircut_then_pick_service",
        "domain": "service",
        "aliases": _HAIRCUT_CATALOG,
        "turns": [
            {
                "sentence": "i want to book for a haircut",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": "NEEDS_CLARIFICATION",
                    "missing_slots": ["service_id", "date", "time"],
                    "slots": {},
                },
            },
            {
                "sentence": "book me premium haircut",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": "READY",
                    "plan": _AVAIL,
                    "missing_slots": ["date", "time"],
                    "slots": {"service_id": "premium haircut"},
                },
            },
        ],
    },
    {
        "id": 7,
        "name": "vague_appointment_then_pick_spa",
        "domain": "service",
        "aliases": _SPA_CATALOG,
        "turns": [
            {
                "sentence": "i want to book an appointment",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": "NEEDS_CLARIFICATION",
                    "missing_slots": ["service_id", "date", "time"],
                    "slots": {},
                },
            },
            {
                "sentence": "premium spa treatment",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": "READY",
                    "plan": _AVAIL,
                    "missing_slots": ["date", "time"],
                    "slots": {"service_id": "premium spa treatment"},
                },
            },
        ],
    },
    {
        "id": 8,
        "name": "quote_detour_then_book_service",
        "domain": "service",
        "aliases": _HAIRCUT_CATALOG,
        "turns": [
            {
                "sentence": "i want to book for a haircut",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": "NEEDS_CLARIFICATION",
                    "missing_slots": ["service_id", "date", "time"],
                    "slots": {},
                },
            },
            {
                "sentence": "how much is premium haircut",
                "expected": {
                    "intent": "QUOTE",
                    "status": "HANDLER_DELEGATED",
                    "missing_slots": [],
                },
            },
            {
                "sentence": "book me premium haircut",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": "READY",
                    "plan": _AVAIL,
                    "missing_slots": ["date", "time"],
                    "slots": {"service_id": "premium haircut"},
                },
            },
        ],
    },
    {
        "id": 9,
        "name": "parking_detour_during_booking",
        "domain": "service",
        "aliases": {"haircut": "haircut"},
        "turns": [
            {
                "sentence": "book haircut",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": "READY",
                    "plan": _AVAIL,
                    "missing_slots": ["date", "time"],
                    "slots": {"service_id": "haircut"},
                },
            },
            {
                "sentence": "do you have parking?",
                "expected": {
                    "intent": "GENERAL_INQUIRY",
                    "status": "HANDLER_DELEGATED",
                    "missing_slots": [],
                },
            },
        ],
    },
    # --- CREATE_RESERVATION: single-turn ---
    {
        "id": 10,
        "name": "reservation_service_only",
        "domain": "reservation",
        "aliases": {"room": "room"},
        "turns": [
            {
                "sentence": "book room",
                "expected": {
                    "intent": "CREATE_RESERVATION",
                    "status": "READY",
                    "plan": _AVAIL,
                    "missing_slots": ["date_range"],
                    "slots": {"service_id": "room"},
                },
            },
        ],
    },
    {
        "id": 11,
        "name": "reservation_service_and_range",
        "domain": "reservation",
        "aliases": {"room": "room"},
        "turns": [
            {
                "sentence": "book room march 10 to 15",
                "expected": {
                    "intent": "CREATE_RESERVATION",
                    "status": "READY",
                    "plan": _AVAIL,
                    "missing_slots": [],
                    "slots": {"service_id": "room"},
                    "date_proposal": {
                        "mode": "range",
                        "start": "2026-03-10",
                        "end": "2026-03-15",
                    },
                },
            },
        ],
    },
    # --- Cancel / modify / intent switch ---
    {
        "id": 12,
        "name": "intent_switch_create_to_cancel",
        "domain": "service",
        "aliases": {"haircut": "haircut"},
        "turns": [
            {
                "sentence": "book haircut",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": "READY",
                    "plan": _AVAIL,
                    "missing_slots": ["date", "time"],
                    "slots": {"service_id": "haircut"},
                },
            },
            {
                "sentence": "cancel my booking",
                "expected": {
                    "intent": "CANCEL_BOOKING",
                    "status": "NEEDS_CLARIFICATION",
                    "plan": _IDENTIFY,
                    "missing_slots": ["booking_id"],
                    "slots": {},
                },
            },
        ],
    },
    {
        "id": 13,
        "name": "cancel_booking_id_extracted",
        "domain": "service",
        "aliases": {"haircut": "haircut"},
        "turns": [
            {
                "sentence": "cancel my booking",
                "expected": {
                    "intent": "CANCEL_BOOKING",
                    "status": "NEEDS_CLARIFICATION",
                    "plan": _IDENTIFY,
                    "missing_slots": ["booking_id"],
                    "slots": {},
                },
            },
            {
                "sentence": "booking abc123",
                "expected": {
                    "status": "READY",
                    "plan": _CONFIRM_CANCEL,
                    "missing_slots": [],
                    "slots": {"booking_id": "abc123"},
                },
            },
        ],
    },
    {
        "id": 14,
        "name": "modify_booking_requires_booking_id",
        "domain": "service",
        "aliases": {"haircut": "haircut"},
        "turns": [
            {
                "sentence": "change booking",
                "expected": {
                    "intent": "MODIFY_BOOKING",
                    "status": "NEEDS_CLARIFICATION",
                    "plan": _IDENTIFY,
                    "missing_slots": ["booking_id", "date"],
                    "slots": {},
                },
            },
        ],
    },
]
