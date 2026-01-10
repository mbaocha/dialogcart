"""
Core E2E Followup Scenarios

Multi-turn conversations with shared user_id to test conversational continuity.
Each scenario is a batch of related turns that share the same user_id.

These scenarios test:
- Multi-turn slot filling
- Context preservation across turns
- Progressive clarification resolution
- Confirmation flow

Domain mapping:
- "service" = appointments (CREATE_APPOINTMENT)
- "reservation" = reservations (CREATE_RESERVATION)
"""

from core.tests.integration.booking_scenarios import (
    STATUS_EXECUTED,
    STATUS_AWAITING_CONFIRMATION,
    STATUS_NEEDS_CLARIFICATION
)

core_followup_scenarios = [
    {
        "name": "service_to_date_to_time",
        "domain": "service",
        "aliases": {
            "haircut": "haircut",
        },
        "turns": [
            {
                "sentence": "book me a haircut",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "clarification_reason": "MISSING_TIME"
                }
            },
            {
                "sentence": "tomorrow",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "clarification_reason": "MISSING_TIME"
                }
            },
            {
                "sentence": "at 3pm",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_EXECUTED  # Or AWAITING_CONFIRMATION
                }
            }
        ]
    },
    {
        "name": "reservation_service_to_dates",
        "domain": "reservation",
        "aliases": {
            "room": "room",
        },
        "turns": [
            {
                "sentence": "reserve a room",
                "expected": {
                    "intent": "CREATE_RESERVATION",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "clarification_reason": "MISSING_DATE_RANGE"
                }
            },
            {
                "sentence": "from october 5th",
                "expected": {
                    "intent": "CREATE_RESERVATION",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "clarification_reason": "MISSING_END_DATE"
                }
            },
            {
                "sentence": "to october 9th",
                "expected": {
                    "intent": "CREATE_RESERVATION",
                    "status": STATUS_EXECUTED  # Or AWAITING_CONFIRMATION
                }
            }
        ]
    },
    {
        "name": "time_to_date_appointment",
        "domain": "service",
        "aliases": {
            "massage": "massage",
        },
        "turns": [
            {
                "sentence": "book massage at 2pm",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "clarification_reason": "MISSING_DATE"
                }
            },
            {
                "sentence": "tomorrow",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_EXECUTED  # Or AWAITING_CONFIRMATION
                }
            }
        ]
    },
    {
        "name": "service_to_time_appointment",
        "domain": "service",
        "aliases": {
            "massage": "massage",
        },
        "turns": [
            {
                "sentence": "schedule massage",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "clarification_reason": "MISSING_TIME"
                }
            },
            {
                "sentence": "friday at 11am",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_EXECUTED  # Or AWAITING_CONFIRMATION
                }
            }
        ]
    },
    {
        "name": "reservation_date_range_followup",
        "domain": "reservation",
        "aliases": {
            "delux": "room",
            "room": "room",
        },
        "turns": [
            {
                "sentence": "book deluxe room",
                "expected": {
                    "intent": "CREATE_RESERVATION",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "clarification_reason": "MISSING_DATE_RANGE"
                }
            },
            {
                "sentence": "november 1st to 3rd",
                "expected": {
                    "intent": "CREATE_RESERVATION",
                    "status": STATUS_EXECUTED  # Or AWAITING_CONFIRMATION
                }
            }
        ]
    },
    {
        "name": "standard_room_reservation_followup",
        "domain": "reservation",
        "aliases": {
            "standard": "room",
        },
        "turns": [
            {
                "sentence": "i need a standard room",
                "expected": {
                    "intent": "CREATE_RESERVATION",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "clarification_reason": "MISSING_DATE_RANGE"
                }
            },
            {
                "sentence": "from november 1st",
                "expected": {
                    "intent": "CREATE_RESERVATION",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "clarification_reason": "MISSING_END_DATE"
                }
            },
            {
                "sentence": "through november 5th",
                "expected": {
                    "intent": "CREATE_RESERVATION",
                    "status": STATUS_EXECUTED  # Or AWAITING_CONFIRMATION
                }
            }
        ]
    },
    {
        "name": "haircut_date_to_time_followup",
        "domain": "service",
        "aliases": {
            "haircut": "haircut",
        },
        "turns": [
            {
                "sentence": "i need a haircut",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "clarification_reason": "MISSING_TIME"
                }
            },
            {
                "sentence": "december 15th",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "clarification_reason": "MISSING_TIME"
                }
            },
            {
                "sentence": "at 4pm",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_EXECUTED  # Or AWAITING_CONFIRMATION
                }
            }
        ]
    },

    {
        "name": "fuzzy_match_massage_followup",
        "domain": "service",
        "aliases": {
            "massage": "massage",
        },
        "turns": [
            {
                "sentence": "book me a massge",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "clarification_reason": "MISSING_TIME"
                }
            },
            {
                "sentence": "tomorrow",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "clarification_reason": "MISSING_TIME"
                }
            },
            {
                "sentence": "at 3pm",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_EXECUTED  # Or AWAITING_CONFIRMATION
                }
            }
        ]
    },
    {
        "name": "deluxe_room_fuzzy_followup",
        "domain": "reservation",
        "aliases": {
            "delux": "room",  # Only "delux" alias - "room" removed to test fuzzy matching
        },
        "turns": [
            {
                "sentence": "book deluxe room",
                "expected": {
                    "intent": "CREATE_RESERVATION",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "clarification_reason": "MISSING_DATE_RANGE"
                }
            },
            {
                "sentence": "december 1st",
                "expected": {
                    "intent": "CREATE_RESERVATION",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "clarification_reason": "MISSING_END_DATE"
                }
            },
            {
                "sentence": "to december 5th",
                "expected": {
                    "intent": "CREATE_RESERVATION",
                    "status": STATUS_EXECUTED  # Or AWAITING_CONFIRMATION
                }
            }
        ]
    },
    {
        "name": "hair_cut_multiword_followup",
        "domain": "service",
        "aliases": {
            "hair cut": "haircut",
        },
        "turns": [
            {
                "sentence": "schedule a hair cut",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "clarification_reason": "MISSING_TIME"
                }
            },
            {
                "sentence": "friday",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "clarification_reason": "MISSING_TIME"
                }
            },
            {
                "sentence": "morning",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_EXECUTED  # Or AWAITING_CONFIRMATION
                }
            }
        ]
    },
    {
        "name": "suite_reservation_followup",
        "domain": "reservation",
        "aliases": {
            "suite": "room",
        },
        "turns": [
            {
                "sentence": "reserve a suite",
                "expected": {
                    "intent": "CREATE_RESERVATION",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "clarification_reason": "MISSING_DATE_RANGE"
                }
            },
            {
                "sentence": "from february 10th",
                "expected": {
                    "intent": "CREATE_RESERVATION",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "clarification_reason": "MISSING_END_DATE"
                }
            },
            {
                "sentence": "to february 14th",
                "expected": {
                    "intent": "CREATE_RESERVATION",
                    "status": STATUS_EXECUTED  # Or AWAITING_CONFIRMATION
                }
            }
        ]
    },
    {
        "name": "beard_grooming_followup",
        "domain": "service",
        "aliases": {
            "beard": "beard grooming",
        },
        "turns": [
            {
                "sentence": "book beard",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "clarification_reason": "MISSING_TIME"
                }
            },
            {
                "sentence": "tomorrow",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "clarification_reason": "MISSING_TIME"
                }
            },
            {
                "sentence": "at 2pm",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_EXECUTED  # Or AWAITING_CONFIRMATION
                }
            }
        ]
    },
    {
        "name": "presidential_room_reservation_followup",
        "domain": "reservation",
        "aliases": {
            "presidential room": "room",
        },
        "turns": [
            {
                "sentence": "book presidential room",
                "expected": {
                    "intent": "CREATE_RESERVATION",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "clarification_reason": "MISSING_DATE_RANGE"
                }
            },
            {
                "sentence": "january 1st",
                "expected": {
                    "intent": "CREATE_RESERVATION",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "clarification_reason": "MISSING_END_DATE"
                }
            },
            {
                "sentence": "through january 7th",
                "expected": {
                    "intent": "CREATE_RESERVATION",
                    "status": STATUS_EXECUTED  # Or AWAITING_CONFIRMATION
                }
            }
        ]
    },
    {
        "name": "massage_time_to_date_followup",
        "domain": "service",
        "aliases": {
            "massage": "massage",
        },
        "turns": [
            {
                "sentence": "book massage",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "clarification_reason": "MISSING_TIME"
                }
            },
            {
                "sentence": "next friday",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "clarification_reason": "MISSING_TIME"
                }
            },
            {
                "sentence": "at 10am",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_EXECUTED  # Or AWAITING_CONFIRMATION
                }
            }
        ]
    },
    {
        "name": "premium_suite_fuzzy_followup",
        "domain": "reservation",
        "aliases": {
            "premum suite": "room",  # Tenant typo for fuzzy matching
        },
        "turns": [
            {
                "sentence": "book premium suite",
                "expected": {
                    "intent": "CREATE_RESERVATION",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "clarification_reason": "MISSING_DATE_RANGE"
                }
            },
            {
                "sentence": "march 1st",
                "expected": {
                    "intent": "CREATE_RESERVATION",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "clarification_reason": "MISSING_END_DATE"
                }
            },
            {
                "sentence": "to march 5th",
                "expected": {
                    "intent": "CREATE_RESERVATION",
                    "status": STATUS_EXECUTED  # Or AWAITING_CONFIRMATION
                }
            }
        ]
    },
    {
        "name": "beerd_typo_followup",
        "domain": "service",
        "aliases": {
            "beerd": "beard grooming",  # Tenant typo
        },
        "turns": [
            {
                "sentence": "book beerd",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "clarification_reason": "MISSING_TIME"
                }
            },
            {
                "sentence": "saturday",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "clarification_reason": "MISSING_TIME"
                }
            },
            {
                "sentence": "afternoon",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_EXECUTED  # Or AWAITING_CONFIRMATION
                }
            }
        ]
    },
]
