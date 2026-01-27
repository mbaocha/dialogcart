"""
Core Session Planning Edge Cases Test Suite

Tests planning edge cases without modifying Core logic.
Validates planning outputs only: stage, action, slots, missing_slots, status.

Test Philosophy:
- Tests validate planning outputs, not Core implementation
- All assertions use raw tenant values (never canonical IDs)
- Tests are isolated and deterministic
- Max 2 turns unless required for the edge case
"""

planning_edges_scenarios = [
    # 1️⃣ Ambiguity Handling (Planning-safe)
    {
        "id": 21,
        "name": "ambiguous_time_evening_normalized",
        "domain": "service",
        "aliases": {"haircut": "haircut"},
        "turns": [
            {
                "sentence": "book haircut friday evening",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "stage": "AVAILABILITY",
                    "action": "SEARCH_AVAILABILITY",
                    "missing_slots": ["time"],
                    # date normalized from "friday" to ISO date
                    # "evening" is normalized to fuzzy time_constraint (does NOT satisfy time requirement)
                    "slots": {"service_id": "haircut", "date": "2026-01-16"},
                    "time_constraint": {
                        "mode": "fuzzy",
                        "start": "17:00",
                        "end": "21:59",
                        "label": "evening"
                    }
                }
            }
        ]
    },
    {
        "id": 22,
        "name": "ambiguous_date_range_next_week",
        "domain": "service",
        "aliases": {"facial": "facial"},
        "turns": [
            {
                "sentence": "book facial next week",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "stage": "AVAILABILITY",
                    "action": "SEARCH_AVAILABILITY",
                    # Multiple dates from "next week" are not promoted to single date for service appointments
                    "missing_slots": ["date", "time"],
                    "slots": {"service_id": "facial"}
                }
            }
        ]
    },

    # 2️⃣ Correction / Override Turns
    {
        "id": 23,
        "name": "date_override_saturday_instead",
        "domain": "service",
        "aliases": {"haircut": "haircut"},
        "turns": [
            {
                "sentence": "book haircut friday",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "stage": "AVAILABILITY",
                    "action": "SEARCH_AVAILABILITY",
                    "missing_slots": ["time"],
                    "slots": {"service_id": "haircut", "date": "2026-01-16"}
                }
            },
            {
                "sentence": "no saturday instead",
                "expected": {
                    "stage": "AVAILABILITY",
                    "action": "SEARCH_AVAILABILITY",
                    "missing_slots": ["time"],
                    # service_id unchanged, date updated
                    "slots": {"service_id": "haircut", "date": "2026-01-17"}
                }
            }
        ]
    },

    # 3️⃣ Slot Conflict Resolution
    {
        "id": 24,
        "name": "time_override_4pm_instead",
        "domain": "service",
        "aliases": {"haircut": "haircut"},
        "turns": [
            {
                "sentence": "book haircut friday at 3pm",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "stage": "CONFIRM",
                    "action": "CONFIRM_APPOINTMENT",
                    "missing_slots": [],
                    "slots": {"service_id": "haircut", "date": "2026-01-16"},
                    "time_constraint": {
                        "mode": "exact",
                        "start": "15:00",
                        "end": "15:00",
                        "label": None
                    }
                }
            },
            {
                "sentence": "make it 4pm",
                "expected": {
                    "stage": "CONFIRM",
                    "action": "CONFIRM_APPOINTMENT",
                    "missing_slots": [],
                    # time_constraint overridden
                    "slots": {"service_id": "haircut", "date": "2026-01-16"},
                    "time_constraint": {
                        "mode": "exact",
                        "start": "16:00",
                        "end": "16:00",
                        "label": None
                    }
                }
            }
        ]
    },

    # 4️⃣ Cross-Intent Noise Immunity
    {
        "id": 25,
        "name": "noise_question_parking",
        "domain": "service",
        "aliases": {"haircut": "haircut"},
        "turns": [
            {
                "sentence": "book haircut",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "stage": "AVAILABILITY",
                    "action": "SEARCH_AVAILABILITY",
                    "missing_slots": ["date", "time"],
                    "slots": {"service_id": "haircut"}
                }
            },
            {
                "sentence": "do you have parking?",
                "expected": {
                    # intent remains CREATE_APPOINTMENT (no intent change)
                    "intent": "CREATE_APPOINTMENT",
                    "stage": "AVAILABILITY",
                    "action": "SEARCH_AVAILABILITY",
                    # slots preserved, missing_slots unchanged
                    "missing_slots": ["date", "time"],
                    "slots": {"service_id": "haircut"}
                }
            }
        ]
    },

    # 5️⃣ Tenant Service Canonicalization Safety
    {
        "id": 26,
        "name": "canonicalization_premium_haircut",
        "domain": "service",
        "aliases": {"premium haircut": "haircut", "presidential haircut": "haircut"},
        "turns": [
            {
                "sentence": "book premium haircut",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "stage": "AVAILABILITY",
                    "action": "SEARCH_AVAILABILITY",
                    "missing_slots": ["date", "time"],
                    # returned service_id == raw tenant value
                    "slots": {"service_id": "premium haircut"}
                }
            }
        ]
    },
    {
        "id": 27,
        "name": "canonicalization_presidential_haircut",
        "domain": "service",
        "aliases": {"premium haircut": "haircut", "presidential haircut": "haircut"},
        "turns": [
            {
                "sentence": "book presidential haircut",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "stage": "AVAILABILITY",
                    "action": "SEARCH_AVAILABILITY",
                    "missing_slots": ["date", "time"],
                    # returned service_id == raw tenant value
                    "slots": {"service_id": "presidential haircut"}
                }
            }
        ]
    },

    # 6️⃣ Empty / Weak Luma Response Recovery
    {
        "id": 28,
        "name": "empty_luma_response_recovery",
        "domain": "service",
        "aliases": {"haircut": "haircut"},
        "turns": [
            {
                "sentence": "book haircut",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "stage": "AVAILABILITY",
                    "action": "SEARCH_AVAILABILITY",
                    "missing_slots": ["date", "time"],
                    "slots": {"service_id": "haircut"}
                }
            },
            {
                "sentence": "",
                "expected": {
                    # session state preserved, missing_slots unchanged
                    # intent should not regress to UNKNOWN
                    "intent": "CREATE_APPOINTMENT",
                    "stage": "AVAILABILITY",
                    "action": "SEARCH_AVAILABILITY",
                    "missing_slots": ["date", "time"],
                    "slots": {"service_id": "haircut"}
                }
            }
        ]
    },
    {
        "id": 29,
        "name": "weak_luma_response_preserves_session",
        "domain": "service",
        "aliases": {"haircut": "haircut"},
        "turns": [
            {
                "sentence": "book haircut tomorrow",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "stage": "AVAILABILITY",
                    "action": "SEARCH_AVAILABILITY",
                    "missing_slots": ["time"],
                    "slots": {"service_id": "haircut", "date": "tomorrow"}
                }
            },
            {
                "sentence": "?",
                "expected": {
                    # Weak/noisy input should preserve session state
                    "intent": "CREATE_APPOINTMENT",
                    "stage": "AVAILABILITY",
                    "action": "SEARCH_AVAILABILITY",
                    "missing_slots": ["time"],
                    "slots": {"service_id": "haircut", "date": "tomorrow"}
                }
            }
        ]
    }
]
