"""
Core E2E Booking Scenarios

Scenarios tailored for core orchestrator E2E testing.
These scenarios are independent of Luma's test scenarios and focus on
core orchestration behavior.

Domain mapping:
- "service" = appointments (CREATE_APPOINTMENT)
- "reservation" = reservations (CREATE_RESERVATION)
"""

# Core outcome statuses
STATUS_EXECUTED = "EXECUTED"
STATUS_AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"
STATUS_NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"

# Luma statuses (for reference)
LUMA_STATUS_READY = "ready"
LUMA_STATUS_NEEDS_CLARIFICATION = "needs_clarification"

core_booking_scenarios = [
    # ────────────────
    # RESERVATIONS — Should execute or await confirmation
    # ────────────────
    {
        "sentence": "reserve standard room october 12 to 14",
        "domain": "reservation",  # Core domain (not booking_mode)
        "aliases": {
            "standard": "room"
        },
        "expected": {
            "intent": "CREATE_RESERVATION",
            "status": STATUS_EXECUTED,  # Core outcome status (after execution)
            "slots": {
                "service_id": "standard",
                "date_range": {
                    "start": "2026-10-12",
                    "end": "2026-10-14"
                }
            }
        }
    },
    {
        "sentence": "book delux room from nov 1st to 3rd",
        "domain": "reservation",
        "aliases": {
            "delux": "room"
        },
        "expected": {
            "intent": "CREATE_RESERVATION",
            "status": STATUS_EXECUTED,
            "slots": {
                "service_id": "delux",
                "date_range": {
                    "start": "2026-11-01",
                    "end": "2026-11-03"
                }
            }
        }
    },
    {
        "sentence": "book me a room dec 20 2026 to dec 25 2026",
        "domain": "reservation",
        "aliases": {
            "room": "room"
        },
        "expected": {
            "intent": "CREATE_RESERVATION",
            "status": STATUS_EXECUTED,
            "slots": {
                "service_id": "room",
                "date_range": {
                    "start": "2026-12-20",
                    "end": "2026-12-25"
                }
            }
        }
    },
    {
        "sentence": "reserve a suite from feb 1st to feb 5th",
        "domain": "reservation",
        "aliases": {
            "suite": "room"
        },
        "expected": {
            "intent": "CREATE_RESERVATION",
            "status": STATUS_EXECUTED,
            "slots": {
                "service_id": "suite",
                "date_range": {
                    "start": "2026-02-01",
                    "end": "2026-02-05"
                }
            }
        }
    },
    # ────────────────
    # APPOINTMENTS — Should execute or await confirmation
    # ────────────────
    {
        "sentence": "book haircut tomorrow at 3pm",
        "domain": "service",  # Core domain for appointments
        "aliases": {
            "haircut": "haircut"
        },
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "status": STATUS_EXECUTED,  # Or AWAITING_CONFIRMATION
            "slots": {
                "service_id": "haircut",
                "has_datetime": True
            }
        }
    },
    {
        "sentence": "schedule massage next monday at 10am",
        "domain": "service",
        "aliases": {
            "massage": "massage"
        },
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "status": STATUS_EXECUTED,  # Or AWAITING_CONFIRMATION
            "slots": {
                "service_id": "massage",
                "has_datetime": True
            }
        }
    },
    {
        "sentence": "book premium haircut tomorrow at 2pm",
        "domain": "service",
        "aliases": {
            "premium haircut": "beauty_and_wellness.haircut",
            "standard haircut": "beauty_and_wellness.haircut",
            "express haircut": "beauty_and_wellness.haircut"
        },
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "status": STATUS_EXECUTED,  # Or AWAITING_CONFIRMATION
            "slots": {
                "service_id": "premium haircut",
                "has_datetime": True
            }
        }
    },
    {
        "sentence": "book beard trim on monday at 11am",
        "domain": "service",
        "aliases": {
            "beard": "beard grooming"
        },
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "status": STATUS_EXECUTED,  # Or AWAITING_CONFIRMATION
            "slots": {
                "service_id": "beard",
                "has_datetime": True
            }
        }
    },
    # ────────────────
    # NEEDS_CLARIFICATION — Missing required slots
    # ────────────────
    {
        "sentence": "book a room",
        "domain": "reservation",
        "aliases": {
            "room": "room"
        },
        "expected": {
            "intent": "CREATE_RESERVATION",
            "status": STATUS_NEEDS_CLARIFICATION,
            "clarification_reason": "MISSING_DATE_RANGE"  # Or similar
        }
    },
    {
        "sentence": "book a haircut",
        "domain": "service",
        "aliases": {
            "haircut": "haircut"
        },
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "status": STATUS_NEEDS_CLARIFICATION,
            "clarification_reason": "MISSING_TIME"
            # rendered_response validation is automatic:
            # - Checks no {{placeholders}} remain (all template variables interpolated)
            # - Auto-derives keywords from clarification_reason (e.g., "time" for MISSING_TIME)
            # - Optionally specify "rendered_response": {"contains_keywords": [...]} for custom keywords
        }
    },
    {
        "sentence": "schedule massage",
        "domain": "service",
        "aliases": {
            "massage": "massage"
        },
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "status": STATUS_NEEDS_CLARIFICATION,
            "clarification_reason": "MISSING_TIME"
        }
    },
    {
        "sentence": "book haircut tomorrow",
        "domain": "service",
        "aliases": {
            "haircut": "haircut"
        },
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "status": STATUS_NEEDS_CLARIFICATION,
            "clarification_reason": "MISSING_TIME"
        }
    },
    {
        "sentence": "reserve room for tomorrow",
        "domain": "reservation",
        "aliases": {
            "room": "room"
        },
        "expected": {
            "intent": "CREATE_RESERVATION",
            "status": STATUS_NEEDS_CLARIFICATION,
            "clarification_reason": "MISSING_END_DATE"  # Or similar
        }
    },
]
