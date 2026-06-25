"""
Core Session Follow-up Tests - Planner-Only Architecture

Test Philosophy:
- Luma provides facts only (no missing_slots, needs_clarification, or semantic flags).
- Core derives missing_slots strictly from intent_execution.yaml stage requirements.
- Core output contract: {intent, status, stage, action, slots, missing_slots}.
- Core does NOT perform: awaiting_slot routing, temporal inference, or semantic clarification.
- Planning NEVER confirms appointments - always routes to SEARCH_AVAILABILITY.
- Slot values must be explicitly present to count (no inference).

What We Test:
- Multi-turn fact accumulation (slots persist across turns).
- Intent switching resets context (new intent starts fresh).
- No slot inference guarantees (Core never infers missing slots).
- Domain isolation (service vs reservation slot requirements differ).
- Planning always routes CREATE_APPOINTMENT → SEARCH_AVAILABILITY (never CONFIRM).

What We Don't Test:
- awaiting_slot behavior (removed from Core).
- Time-of-day inference (morning/evening -> time).
- start_date/end_date inference (date -> start_date or end_date).
- Confirmation logic (execution concern, not planning).
- Execution outcomes or side effects.

All expectations are derived strictly from intent_execution.yaml stages.
"""

followup_scenarios = [
    # Multi-turn fact accumulation: service → date → time (IDs 1-10)
    {
        "id": 1,
        "name": "service_to_date_to_time",
        "domain": "service",
        "aliases": {"haircut": "haircut"},
        "turns": [
            {
                "sentence": "book a haircut",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": "NEEDS_CLARIFICATION",
                    "plan": {"stage": "AVAILABILITY", "action": "SEARCH_AVAILABILITY"},
                    "missing_slots": ["date", "time"],
                    "slots": {"service_id": "haircut"},
                },
            },
            {
                "sentence": "tomorrow",
                "expected": {
                    "status": "NEEDS_CLARIFICATION",
                    "plan": {"stage": "AVAILABILITY", "action": "SEARCH_AVAILABILITY"},
                    "missing_slots": ["time"],
                    "slots": {"service_id": "haircut"},
                    "date_proposal": {"mode": "single_day", "start": "2026-01-14"},
                },
            },
            {
                "sentence": "11am",
                "expected": {
                    "status": "READY",
                    "plan": {"stage": "AVAILABILITY", "action": "SEARCH_AVAILABILITY"},
                    "missing_slots": [],
                    "slots": {"service_id": "haircut"},
                    "date_proposal": {"mode": "single_day", "start": "2026-01-14"},
                    "time_proposal": {"mode": "exact", "value": "11:00"},
                },
            },
        ],
    },
    {
        "id": 2,
        "name": "service_to_date_to_time_massage",
        "domain": "service",
        "aliases": {"massage": "massage"},
        "turns": [
            {
                "sentence": "book massage",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": "NEEDS_CLARIFICATION",
                    "plan": {"stage": "AVAILABILITY", "action": "SEARCH_AVAILABILITY"},
                    "missing_slots": ["date", "time"],
                    "slots": {"service_id": "massage"},
                },
            },
            {
                "sentence": "this friday",
                "expected": {
                    "status": "NEEDS_CLARIFICATION",
                    "plan": {"stage": "AVAILABILITY", "action": "SEARCH_AVAILABILITY"},
                    "missing_slots": ["time"],
                    "slots": {"service_id": "massage"},
                    "date_proposal": {"mode": "single_day", "start": "2026-01-16"},
                },
            },
            {
                "sentence": "3pm",
                "expected": {
                    "status": "READY",
                    "plan": {"stage": "AVAILABILITY", "action": "SEARCH_AVAILABILITY"},
                    "missing_slots": [],
                    "slots": {"service_id": "massage"},
                    "date_proposal": {"mode": "single_day", "start": "2026-01-16"},
                    "time_proposal": {"mode": "exact", "value": "15:00"},
                },
            },
        ],
    },
    {
        "id": 3,
        "name": "service_to_date_to_time_facial",
        "domain": "service",
        "aliases": {"facial": "facial"},
        "turns": [
            {
                "sentence": "schedule facial",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": "NEEDS_CLARIFICATION",
                    "plan": {"stage": "AVAILABILITY", "action": "SEARCH_AVAILABILITY"},
                    "missing_slots": ["date", "time"],
                    # service_id present allows partial execution
                    "slots": {"service_id": "facial"},
                },
            },
            {
                "sentence": "next monday",
                "expected": {
                    "status": "NEEDS_CLARIFICATION",
                    "plan": {"stage": "AVAILABILITY", "action": "SEARCH_AVAILABILITY"},
                    "missing_slots": ["time"],
                    "slots": {"service_id": "facial"},
                    "date_proposal": {"mode": "single_day", "start": "2026-01-19"},
                },
            },
            {
                "sentence": "10am",
                "expected": {
                    "status": "READY",
                    "plan": {"stage": "AVAILABILITY", "action": "SEARCH_AVAILABILITY"},
                    "missing_slots": [],
                    "slots": {"service_id": "facial"},
                    "date_proposal": {"mode": "single_day", "start": "2026-01-19"},
                    "time_proposal": {"mode": "exact", "value": "10:00"},
                },
            },
        ],
    },
    # Multi-turn fact accumulation: service → time → date (IDs 11-15)
    # Tests that slots can be provided in any order
    {
        "id": 11,
        "name": "service_to_time_to_date",
        "domain": "service",
        "aliases": {"massage": "massage"},
        "turns": [
            {
                "sentence": "book massage at 2pm",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": "NEEDS_CLARIFICATION",
                    "plan": {"stage": "AVAILABILITY", "action": "SEARCH_AVAILABILITY"},
                    "missing_slots": ["date"],
                    "slots": {"service_id": "massage"},
                    "time_proposal": {"mode": "exact", "value": "14:00"},
                },
            },
            {
                "sentence": "tomorrow",
                "expected": {
                    "status": "READY",
                    "plan": {"stage": "AVAILABILITY", "action": "SEARCH_AVAILABILITY"},
                    "missing_slots": [],
                    "slots": {"service_id": "massage"},
                    "date_proposal": {"mode": "single_day", "start": "2026-01-14"},
                    "time_proposal": {"mode": "exact", "value": "14:00"},
                },
            },
        ],
    },
    {
        "id": 12,
        "name": "service_to_time_to_date_haircut",
        "domain": "service",
        "aliases": {"haircut": "haircut"},
        "turns": [
            {
                "sentence": "book haircut at 10am",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": "NEEDS_CLARIFICATION",
                    "plan": {"stage": "AVAILABILITY", "action": "SEARCH_AVAILABILITY"},
                    "missing_slots": ["date"],
                    "slots": {"service_id": "haircut"},
                    "time_proposal": {"mode": "exact", "value": "10:00"},
                },
            },
            {
                "sentence": "friday",
                "expected": {
                    "status": "READY",
                    "plan": {"stage": "AVAILABILITY", "action": "SEARCH_AVAILABILITY"},
                    "missing_slots": [],
                    "slots": {"service_id": "haircut"},
                    "date_proposal": {"mode": "single_day", "start": "2026-01-16"},
                    "time_proposal": {"mode": "exact", "value": "10:00"},
                },
            },
        ],
    },
    # Test slots in different order: date → time (IDs 13-14)
    {
        "id": 13,
        "name": "service_to_date_to_time_reverse_order",
        "domain": "service",
        "aliases": {"facial": "facial"},
        "turns": [
            {
                "sentence": "book facial",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": "NEEDS_CLARIFICATION",
                    "plan": {"stage": "AVAILABILITY", "action": "SEARCH_AVAILABILITY"},
                    "missing_slots": ["date", "time"],
                    # service_id present allows partial execution
                    "slots": {"service_id": "facial"},
                },
            },
            {
                "sentence": "next week",
                "expected": {
                    "status": "NEEDS_CLARIFICATION",
                    "plan": {"stage": "AVAILABILITY", "action": "SEARCH_AVAILABILITY"},
                    "missing_slots": ["time"],
                    "slots": {"service_id": "facial"},
                    "date_proposal": {
                        "mode": "flexible",
                        "start": "2026-01-19",
                        "end": "2026-01-25",
                    },
                },
            },
            {
                "sentence": "afternoon",
                "expected": {
                    "status": "READY",
                    "plan": {"stage": "AVAILABILITY", "action": "SEARCH_AVAILABILITY"},
                    "missing_slots": [],
                    "slots": {"service_id": "facial"},
                    "date_proposal": {
                        "mode": "flexible",
                        "start": "2026-01-19",
                        "end": "2026-01-25",
                    },
                    "time_proposal": {
                        "mode": "fuzzy",
                        "label": "afternoon",
                        "start": "12:00",
                        "end": "16:59",
                    },
                },
            },
        ],
    },
    # Test all slots provided in one turn (ID 14)
    {
        "id": 14,
        "name": "all_slots_in_one_turn",
        "domain": "service",
        "aliases": {"massage": "massage"},
        "turns": [
            {
                "sentence": "book massage tomorrow at 3pm",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": "READY",
                    "plan": {"stage": "AVAILABILITY", "action": "SEARCH_AVAILABILITY"},
                    "missing_slots": [],
                    "slots": {"service_id": "massage"},
                    "date_proposal": {"mode": "single_day", "start": "2026-01-14"},
                    "time_proposal": {"mode": "exact", "value": "15:00"},
                },
            }
        ],
    },
    # Multi-turn fact accumulation: reservation check-in → check-out (IDs 21-30)
    # NOTE: Core requires explicit start_date and end_date slots (no inference from date).
    {
        "id": 21,
        "name": "reservation_checkin_to_checkout",
        "domain": "reservation",
        "aliases": {"room": "room"},
        "turns": [
            {
                "sentence": "reserve a room",
                "expected": {
                    "intent": "CREATE_RESERVATION",
                    "status": "NEEDS_CLARIFICATION",
                    "plan": {"stage": "AVAILABILITY", "action": "SEARCH_AVAILABILITY"},
                    "missing_slots": ["date_range"],
                    "slots": {"service_id": "room"},
                },
            },
            {
                "sentence": "from october 5th",
                "expected": {
                    "status": "NEEDS_CLARIFICATION",
                    "plan": {"stage": "AVAILABILITY", "action": "SEARCH_AVAILABILITY"},
                    "missing_slots": ["date_range"],
                    "slots": {"service_id": "room"},
                },
            },
            {
                "sentence": "to october 9th",
                "expected": {
                    "status": "NEEDS_CLARIFICATION",
                    "plan": {"stage": "AVAILABILITY", "action": "SEARCH_AVAILABILITY"},
                    "missing_slots": ["date_range"],
                    "slots": {"service_id": "room"},
                },
            },
        ],
    },
    {
        "id": 26,
        "name": "reservation_range_followup",
        "domain": "reservation",
        "aliases": {"room": "room"},
        "turns": [
            {
                "sentence": "book room",
                "expected": {
                    "intent": "CREATE_RESERVATION",
                    "status": "NEEDS_CLARIFICATION",
                    "plan": {"stage": "AVAILABILITY", "action": "SEARCH_AVAILABILITY"},
                    "missing_slots": ["date_range"],
                    "slots": {"service_id": "room"},
                },
            },
            {
                "sentence": "march 10 to 15",
                "expected": {
                    "status": "READY",
                    "plan": {"stage": "AVAILABILITY", "action": "SEARCH_AVAILABILITY"},
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
    # Intent switching resets context (IDs 31-35)
    {
        "id": 31,
        "name": "intent_switch_resets_session",
        "domain": "service",
        "aliases": {"haircut": "haircut"},
        "turns": [
            {
                "sentence": "book haircut",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": "NEEDS_CLARIFICATION",
                    "plan": {"stage": "AVAILABILITY", "action": "SEARCH_AVAILABILITY"},
                    "missing_slots": ["date", "time"],
                    "slots": {"service_id": "haircut"},
                },
            },
            {
                "sentence": "cancel my booking",
                "expected": {
                    "intent": "CANCEL_BOOKING",
                    "status": "NEEDS_CLARIFICATION",
                    "plan": {"stage": "IDENTIFY", "action": "FETCH_BOOKING"},
                    "missing_slots": ["booking_id"],
                    "slots": {},
                },
            },
        ],
    },
    {
        "id": 32,
        "name": "intent_switch_create_to_cancel",
        "domain": "service",
        "aliases": {"facial": "facial"},
        "turns": [
            {
                "sentence": "book facial",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": "NEEDS_CLARIFICATION",
                    "plan": {"stage": "AVAILABILITY", "action": "SEARCH_AVAILABILITY"},
                    "missing_slots": ["date", "time"],
                    "slots": {"service_id": "facial"},
                },
            },
            {
                "sentence": "nevermind cancel",
                "expected": {
                    "intent": "CANCEL_BOOKING",
                    "status": "NEEDS_CLARIFICATION",
                    "plan": {"stage": "IDENTIFY", "action": "FETCH_BOOKING"},
                    "missing_slots": ["booking_id"],
                    "slots": {},
                },
            },
        ],
    },
    {
        "id": 33,
        "name": "intent_switch_modify_to_cancel",
        "domain": "service",
        "aliases": {"massage": "massage"},
        "turns": [
            {
                "sentence": "change booking",
                "expected": {
                    "intent": "MODIFY_BOOKING",
                    "status": "NEEDS_CLARIFICATION",
                    "plan": {"stage": "IDENTIFY", "action": "FETCH_BOOKING"},
                    "missing_slots": ["booking_id", "date"],
                    "slots": {},
                },
            },
            {
                "sentence": "actually cancel it",
                "expected": {
                    "intent": "CANCEL_BOOKING",
                    "status": "NEEDS_CLARIFICATION",
                    "plan": {"stage": "IDENTIFY", "action": "FETCH_BOOKING"},
                    "missing_slots": ["booking_id"],
                    "slots": {},
                },
            },
        ],
    },
    # Booking ID format extraction (IDs 36-45)
    {
        "id": 36,
        "name": "booking_id_format_extracted",
        "domain": "service",
        "aliases": {"haircut": "haircut"},
        "turns": [
            {
                "sentence": "cancel my booking",
                "expected": {
                    "intent": "CANCEL_BOOKING",
                    "status": "NEEDS_CLARIFICATION",
                    "plan": {"stage": "IDENTIFY", "action": "FETCH_BOOKING"},
                    "missing_slots": ["booking_id"],
                    "slots": {},
                },
            },
            {
                "sentence": "booking abc123",
                "expected": {
                    "status": "READY",
                    "plan": {"stage": "CONFIRM", "action": "CONFIRM_CANCELLATION"},
                    "missing_slots": [],
                    "slots": {"booking_id": "abc123"},
                },
            },
        ],
    },
    {
        "id": 37,
        "name": "end_date_not_inferred_from_second_date",
        "domain": "reservation",
        "aliases": {"suite": "room"},
        "turns": [
            {
                "sentence": "reserve suite",
                "expected": {
                    "intent": "CREATE_RESERVATION",
                    "status": "NEEDS_CLARIFICATION",
                    "plan": {"stage": "AVAILABILITY", "action": "SEARCH_AVAILABILITY"},
                    "missing_slots": ["date_range"],
                    "slots": {"service_id": "suite"},
                },
            },
            {
                "sentence": "from nov 1st",
                "expected": {
                    "status": "NEEDS_CLARIFICATION",
                    "plan": {"stage": "AVAILABILITY", "action": "SEARCH_AVAILABILITY"},
                    "missing_slots": ["date_range"],
                    "slots": {"service_id": "suite"},
                },
            },
            {
                "sentence": "nov 5th",
                "expected": {
                    "status": "NEEDS_CLARIFICATION",
                    "plan": {"stage": "AVAILABILITY", "action": "SEARCH_AVAILABILITY"},
                    "missing_slots": ["date_range"],
                    "slots": {"service_id": "suite"},
                },
            },
        ],
    },
    {
        "id": 38,
        "name": "date_range_not_inferred_from_single_date",
        "domain": "reservation",
        "aliases": {"deluxe": "room"},
        "turns": [
            {
                "sentence": "reserve deluxe",
                "expected": {
                    "intent": "CREATE_RESERVATION",
                    "status": "NEEDS_CLARIFICATION",
                    "plan": {"stage": "AVAILABILITY", "action": "SEARCH_AVAILABILITY"},
                    "missing_slots": ["date_range"],
                    "slots": {"service_id": "deluxe"},
                },
            },
            {
                "sentence": "jan 5th",
                "expected": {
                    "status": "NEEDS_CLARIFICATION",
                    "plan": {"stage": "AVAILABILITY", "action": "SEARCH_AVAILABILITY"},
                    "missing_slots": ["date_range"],
                    "slots": {"service_id": "deluxe"},
                },
            },
        ],
    },
    # Domain isolation: service vs reservation (IDs 46-50)
    {
        "id": 46,
        "name": "service_date_time_not_applied_to_reservation",
        "domain": "reservation",
        "aliases": {"room": "room"},
        "turns": [
            {
                "sentence": "book room",
                "expected": {
                    "intent": "CREATE_RESERVATION",
                    "status": "NEEDS_CLARIFICATION",
                    "plan": {"stage": "AVAILABILITY", "action": "SEARCH_AVAILABILITY"},
                    "missing_slots": ["date_range"],
                    "slots": {"service_id": "room"},
                },
            },
            {
                "sentence": "tomorrow at 3pm",
                "expected": {
                    "status": "NEEDS_CLARIFICATION",
                    "plan": {"stage": "AVAILABILITY", "action": "SEARCH_AVAILABILITY"},
                    "missing_slots": ["date_range"],
                    "slots": {"service_id": "room"},
                },
            },
        ],
    },
    {
        "id": 47,
        "name": "reservation_date_range_not_applied_to_service",
        "domain": "service",
        "aliases": {"haircut": "haircut"},
        "turns": [
            {
                "sentence": "book haircut",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": "NEEDS_CLARIFICATION",
                    "plan": {"stage": "AVAILABILITY", "action": "SEARCH_AVAILABILITY"},
                    "missing_slots": ["date", "time"],
                    "slots": {"service_id": "haircut"},
                },
            },
            {
                "sentence": "from nov 1st to nov 5th",
                "expected": {
                    "status": "NEEDS_CLARIFICATION",
                    "plan": {"stage": "AVAILABILITY", "action": "SEARCH_AVAILABILITY"},
                    "missing_slots": ["time"],
                    "slots": {"service_id": "haircut"},
                    "date_proposal": {
                        "mode": "range",
                        "start": "2026-11-01",
                        "end": "2026-11-05",
                    },
                },
            },
        ],
    },
    {
        "id": 48,
        "name": "service_requires_date_and_time",
        "domain": "service",
        "aliases": {"massage": "massage"},
        "turns": [
            {
                "sentence": "book massage at 3pm",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": "NEEDS_CLARIFICATION",
                    "plan": {"stage": "AVAILABILITY", "action": "SEARCH_AVAILABILITY"},
                    "missing_slots": ["date"],
                    "slots": {"service_id": "massage"},
                },
            }
        ],
    },
    {
        "id": 49,
        "name": "reservation_requires_date_range",
        "domain": "reservation",
        "aliases": {"room": "room"},
        "turns": [
            {
                "sentence": "book room",
                "expected": {
                    "intent": "CREATE_RESERVATION",
                    "status": "NEEDS_CLARIFICATION",
                    "plan": {"stage": "AVAILABILITY", "action": "SEARCH_AVAILABILITY"},
                    "missing_slots": ["date_range"],
                    "slots": {"service_id": "room"},
                },
            }
        ],
    },
    {
        "id": 50,
        "name": "modify_booking_requires_booking_id",
        "domain": "service",
        "aliases": {"haircut": "haircut"},
        "turns": [
            {
                "sentence": "change booking",
                "expected": {
                    "intent": "MODIFY_BOOKING",
                    "status": "NEEDS_CLARIFICATION",
                    "plan": {"stage": "IDENTIFY", "action": "FETCH_BOOKING"},
                    "missing_slots": ["booking_id", "date"],
                    "slots": {},
                },
            }
        ],
    },
]
