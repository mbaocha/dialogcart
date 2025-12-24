scenarios = [

    # ────────────────
    # RESERVATIONS — READY (DATE RANGE)
    # ────────────────
    {
        "sentence": "book me in for delux rom from oct 5th to 9th",
        "booking_mode": "reservation",
        "expected": {
            "intent": "CREATE_RESERVATION",
            "status": "ready",
            "slots": {
                "service_id": "room",
                "start_date": "2026-10-05",
                "end_date": "2026-10-09"
            }
        }
    },
    {
        "sentence": "reserve standard room october 12 to 14",
        "booking_mode": "reservation",
        "expected": {
            "intent": "CREATE_RESERVATION",
            "status": "ready",
            "slots": {
                "service_id": "room",
                "start_date": "2026-10-12",
                "end_date": "2026-10-14"
            }
        }
    },
    {
        "sentence": "book deluxe room from nov 1st to 3rd",
        "booking_mode": "reservation",
        "expected": {
            "intent": "CREATE_RESERVATION",
            "status": "ready",
            "slots": {
                "service_id": "room",
                "start_date": "2026-11-01",
                "end_date": "2026-11-03"
            }
        }
    },
    {
        "sentence": "book me a room dec 20 2026 to dec 25 2026",
        "booking_mode": "reservation",
        "expected": {
            "intent": "CREATE_RESERVATION",
            "status": "ready",
            "slots": {
                "service_id": "room",
                "start_date": "2026-12-20",
                "end_date": "2026-12-25"
            }
        }
    },

    # ────────────────
    # RESERVATIONS — MISSING END DATE
    # ────────────────
    {
        "sentence": "book room from oct 5th",
        "booking_mode": "reservation",
        "expected": {
            "intent": "CREATE_RESERVATION",
            "status": "needs_clarification",
            "missing_slots": ["end_date"]
        }
    },
    {
        "sentence": "reserve delux room starting november 2nd",
        "booking_mode": "reservation",
        "expected": {
            "intent": "CREATE_RESERVATION",
            "status": "needs_clarification",
            "missing_slots": ["end_date"]
        }
    },
    {
        "sentence": "book standard rom from dec 10",
        "booking_mode": "reservation",
        "expected": {
            "intent": "CREATE_RESERVATION",
            "status": "needs_clarification",
            "missing_slots": ["end_date"]
        }
    },

    # ────────────────
    # RESERVATIONS — AMBIGUOUS RANGE
    # ────────────────
    {
        "sentence": "reserve deluxe room oct 29th to 2nd",
        "booking_mode": "reservation",
        "expected": {
            "intent": "CREATE_RESERVATION",
            "status": "needs_clarification",
            "missing_slots": ["end_date"]
        }
    },
    {
        "sentence": "book room jan 31 to 2nd",
        "booking_mode": "reservation",
        "expected": {
            "intent": "CREATE_RESERVATION",
            "status": "needs_clarification",
            "missing_slots": ["end_date"]
        }
    },

    # ────────────────
    # APPOINTMENTS — READY
    # ────────────────
    {
        "sentence": "book hair cut tomorrow at 3pm",
        "booking_mode": "service",
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "status": "ready",
            "slots": {
                "service_id": "haircut",
                "has_datetime": True
            }
        }
    },
    {
        "sentence": "schedule beerd trim friday at noon",
        "booking_mode": "service",
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "status": "ready",
            "slots": {
                "service_id": "beard grooming",
                "has_datetime": True
            }
        }
    },
    {
        "sentence": "book massage next monday at 10am",
        "booking_mode": "service",
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "status": "ready",
            "slots": {
                "service_id": "massage",
                "has_datetime": True
            }
        }
    },
    {
        "sentence": "schedule facial tomorrow evening",
        "booking_mode": "service",
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "status": "ready",
            "slots": {
                "service_id": "facial",
                "has_datetime": True
            }
        }
    },

    # ────────────────
    # APPOINTMENTS — MISSING TIME
    # ────────────────
    {
        "sentence": "schedule hair cut tomorrow",
        "booking_mode": "service",
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "status": "needs_clarification",
            "missing_slots": ["time"]
        }
    },
    {
        "sentence": "book beard trim next friday",
        "booking_mode": "service",
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "status": "needs_clarification",
            "missing_slots": ["time"]
        }
    },
    {
        "sentence": "schedule massage monday",
        "booking_mode": "service",
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "status": "needs_clarification",
            "missing_slots": ["time"]
        }
    },

    # ────────────────
    # APPOINTMENTS — MISSING DATE
    # ────────────────
    {
        "sentence": "book hair cut at 3pm",
        "booking_mode": "service",
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "status": "needs_clarification",
            "missing_slots": ["date"]
        }
    },
    {
        "sentence": "schedule facial in the evening",
        "booking_mode": "service",
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "status": "needs_clarification",
            "missing_slots": ["date"]
        }
    },

    # ────────────────
    # MODIFY / CANCEL / PAYMENT
    # ────────────────
    {
        "sentence": "change my booking",
        "booking_mode": "service",
        "expected": {
            "intent": "MODIFY_BOOKING",
            "status": "needs_clarification",
            "missing_slots": ["booking_id"]
        }
    },
    {
        "sentence": "reschedule my appointment",
        "booking_mode": "service",
        "expected": {
            "intent": "MODIFY_BOOKING",
            "status": "needs_clarification",
            "missing_slots": ["booking_id"]
        }
    },
    {
        "sentence": "cancel my reservation",
        "booking_mode": "reservation",
        "expected": {
            "intent": "CANCEL_BOOKING",
            "status": "needs_clarification",
            "missing_slots": ["booking_id"]
        }
    },
    {
        "sentence": "pay for my booking",
        "booking_mode": "service",
        "expected": {
            "intent": "PAYMENT",
            "status": "needs_clarification",
            "missing_slots": ["booking_id"]
        }
    },

    # ────────────────
    # BULK VARIANTS (typos / phrasing)
    # ────────────────
    *[
        {
            "sentence": f"book {svc} tomorrow at {time}",
            "booking_mode": "service",
            "expected": {
                "intent": "CREATE_APPOINTMENT",
                "status": "ready",
                "slots": {
                    "service_id": canonical,
                    "has_datetime": True
                }
            }
        }
        for svc, canonical, time in [
            ("hair cut", "haircut", "2pm"),
            ("beerd trim", "beard grooming", "11am"),
            ("massage", "massage", "4pm"),
            ("facial", "facial", "1pm"),
        ]
    ],

]


scenarios += [

    # ────────────────
    # APPOINTMENTS — TIME WINDOWS (EXPLICIT)
    # ────────────────
    {
        "sentence": "book haircut tomorrow between 2 and 5",
        "booking_mode": "service",
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "status": "needs_clarification",
            "issues": {
                "time": {
                    "raw": "between 2 and 5",
                    "start_hour": 2,
                    "end_hour": 5,
                    "candidates": ["am", "pm"]
                }
            },
            "clarification_reason": "AMBIGUOUS_TIME_MERIDIEM"
        }
    },
    {
        "sentence": "schedule massage friday after 3",
        "booking_mode": "service",
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "status": "needs_clarification",
            "issues": {"time": "missing"}
        }
    },
    {
        "sentence": "schedule massage friday after 3pm",
        "booking_mode": "service",
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "status": "ready",
            "slots": {"service_id": "massage", "has_datetime": True}
        }
    },
    {
        "sentence": "book facial monday before 11am",
        "booking_mode": "service",
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "status": "ready",
            "slots": {"service_id": "facial", "has_datetime": True}
        }
    },

    # ────────────────
    # APPOINTMENTS — FUZZY TIME (MUST CLARIFY)
    # ────────────────
    {
        "sentence": "book haircut tomorrow evening",
        "booking_mode": "service",
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "status": "ready",
            "slots": {"service_id": "haircut", "has_datetime": True}
        }
    },
    {
        "sentence": "schedule massage friday morning",
        "booking_mode": "service",
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "status": "ready",
            "slots": {"service_id": "massage", "has_datetime": True}
        }
    },
    {
        "sentence": "book facial at night",
        "booking_mode": "service",
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "status": "needs_clarification",
            "missing_slots": ["date"]
        }
    },

    # ────────────────
    # RESERVATIONS — FUZZY TIME (ALLOWED, NO BINDING)
    # ────────────────
    {
        "sentence": "reserve room oct 10th evening",
        "booking_mode": "reservation",
        "expected": {
            "intent": "CREATE_RESERVATION",
            "status": "needs_clarification",
            "issues": {"end_date": "missing"}
        }
    },
    {
        "sentence": "book deluxe room nov 5 to 7 at night",
        "booking_mode": "reservation",
        "expected": {
            "intent": "CREATE_RESERVATION",
            "status": "ready",
            "slots": {
                "service_id": "room",
                "start_date": "2026-11-05",
                "end_date": "2026-11-07"
            }
        }
    },

    # ────────────────
    # APPOINTMENTS — INVALID / CONTRADICTORY WINDOWS
    # ────────────────
    {
        "sentence": "book haircut tomorrow after 6 before 3",
        "booking_mode": "service",
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "status": "needs_clarification",
            "missing_slots": ["time"]
        }
    },
    {
        "sentence": "schedule massage friday between 5 and 5",
        "booking_mode": "service",
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "status": "needs_clarification",
            "issues": {
                "time": {
                    "raw": "between 5 and 5",
                    "start_hour": 5,
                    "end_hour": 5,
                    "candidates": ["am", "pm"]
                }
            },
            "clarification_reason": "AMBIGUOUS_TIME_MERIDIEM"
        }
    },

    # ────────────────
    # DATE ONLY (NO TIME) — APPOINTMENT
    # ────────────────
    {
        "sentence": "book haircut on monday",
        "booking_mode": "service",
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "status": "needs_clarification",
            "missing_slots": ["time"]
        }
    },
    {
        "sentence": "schedule beard trim tomorrow",
        "booking_mode": "service",
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "status": "needs_clarification",
            "missing_slots": ["time"]
        }
    },

    # ────────────────
    # TIME ONLY (NO DATE) — APPOINTMENT
    # ────────────────
    {
        "sentence": "book haircut at 4pm",
        "booking_mode": "service",
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "status": "needs_clarification",
            "missing_slots": ["date"]
        }
    },
    {
        "sentence": "schedule massage between 2 and 5",
        "booking_mode": "service",
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "status": "needs_clarification",
            "missing_slots": ["date"]
        }
    },

    # ────────────────
    # EDGE CASES — CALENDAR BINDER SAFETY
    # ────────────────
    {
        "sentence": "book haircut tomorrow at noon",
        "booking_mode": "service",
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "status": "ready",
            "slots": {"service_id": "haircut", "has_datetime": True}
        }
    },
    {
        "sentence": "schedule massage tomorrow at midnight",
        "booking_mode": "service",
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "status": "ready",
            "slots": {"service_id": "massage", "has_datetime": True}
        }
    },

    # ────────────────
    # CONTROL — NON-CALENDAR INTENTS
    # ────────────────
    {
        "sentence": "what is my booking status",
        "booking_mode": "service",
        "expected": {
            "intent": "BOOKING_INQUIRY",
            "status": "needs_clarification",
            "missing_slots": ["booking_id"]
        }
    },
    {
        "sentence": "cancel appointment",
        "booking_mode": "service",
        "expected": {
            "intent": "CANCEL_BOOKING",
            "status": "needs_clarification",
            "missing_slots": ["booking_id"]
        }
    },
]
