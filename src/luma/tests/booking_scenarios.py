# Booking intent scenarios (CREATE_APPOINTMENT, CREATE_RESERVATION)
booking_scenarios = [
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
    {
        "sentence": "reserve a suite from january 1st to january 5th",
        "booking_mode": "reservation",
        "expected": {
            "intent": "CREATE_RESERVATION",
            "status": "ready",
            "slots": {
                "service_id": "suite",
                "start_date": "2026-01-01",
                "end_date": "2026-01-05"
            }
        }
    },
    {
        "sentence": "book standard room from feb 10 to feb 15",
        "booking_mode": "reservation",
        "expected": {
            "intent": "CREATE_RESERVATION",
            "status": "ready",
            "slots": {
                "service_id": "room",
                "start_date": "2026-02-10",
                "end_date": "2026-02-15"
            }
        }
    },
    {
        "sentence": "reserve deluxe room march 1 to march 5",
        "booking_mode": "reservation",
        "expected": {
            "intent": "CREATE_RESERVATION",
            "status": "ready",
            "slots": {
                "service_id": "room",
                "start_date": "2026-03-01",
                "end_date": "2026-03-05"
            }
        }
    },
    {
        "sentence": "book a room from april 10th to april 15th",
        "booking_mode": "reservation",
        "expected": {
            "intent": "CREATE_RESERVATION",
            "status": "ready",
            "slots": {
                "service_id": "room",
                "start_date": "2026-04-10",
                "end_date": "2026-04-15"
            }
        }
    },
    {
        "sentence": "reserve suite may 5 to may 10",
        "booking_mode": "reservation",
        "expected": {
            "intent": "CREATE_RESERVATION",
            "status": "ready",
            "slots": {
                "service_id": "suite",
                "start_date": "2026-05-05",
                "end_date": "2026-05-10"
            }
        }
    },
    # ────────────────
    # APPOINTMENTS — READY (EXACT TIME)
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
    {
        "sentence": "book haircut for next friday at 2pm",
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
        "sentence": "schedule massage tomorrow at 9am",
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
        "sentence": "book beard trim on monday at 11am",
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
        "sentence": "schedule facial next tuesday at 3pm",
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
    {
        "sentence": "book massage tomorrow at 2pm",
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
        "sentence": "schedule haircut friday at 4pm",
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
        "sentence": "book hair cut tomorrow at 2pm",
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
        "sentence": "book beerd trim tomorrow at 11am",
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
        "sentence": "book massage tomorrow at 4pm",
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
        "sentence": "book facial tomorrow at 1pm",
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
    {
        "sentence": "schedule massage next monday at 9am",
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
        "sentence": "book haircut next friday at 3pm",
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
        "sentence": "schedule massage friday after 3pm",
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
        "sentence": "book facial monday before 11am",
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
    {
        "sentence": "book haircut tomorrow evening",
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
        "sentence": "schedule massage friday morning",
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
    {
        "sentence": "reserve standard room from dec 1 to dec 5 in the morning",
        "booking_mode": "reservation",
        "expected": {
            "intent": "CREATE_RESERVATION",
            "status": "ready",
            "slots": {
                "service_id": "room",
                "start_date": "2026-12-01",
                "end_date": "2026-12-05"
            }
        }
    },
    {
        "sentence": "book suite jan 10 to jan 15 evening",
        "booking_mode": "reservation",
        "expected": {
            "intent": "CREATE_RESERVATION",
            "status": "ready",
            "slots": {
                "service_id": "suite",
                "start_date": "2026-01-10",
                "end_date": "2026-01-15"
            }
        }
    },
    {
        "sentence": "reserve room from feb 20 to feb 25 afternoon",
        "booking_mode": "reservation",
        "expected": {
            "intent": "CREATE_RESERVATION",
            "status": "ready",
            "slots": {
                "service_id": "room",
                "start_date": "2026-02-20",
                "end_date": "2026-02-25"
            }
        }
    },
    {
        "sentence": "book haircut tomorrow at noon",
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
        "sentence": "schedule massage tomorrow at midnight",
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
    # ────────────────
    # NEEDS_CLARIFICATION — MISSING SLOTS
    # ────────────────
    {
        "sentence": "book a room",
        "booking_mode": "reservation",
        "expected": {
            "intent": "CREATE_RESERVATION",
            "status": "needs_clarification",
            "missing_slots": ["start_date", "end_date"]
        }
    },
    {
        "sentence": "reserve a suite",
        "booking_mode": "reservation",
        "expected": {
            "intent": "CREATE_RESERVATION",
            "status": "needs_clarification",
            "missing_slots": ["start_date", "end_date"]
        }
    },
    {
        "sentence": "book a haircut",
        "booking_mode": "service",
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "status": "needs_clarification",
            "missing_slots": ["date", "time"]
        }
    },
    {
        "sentence": "schedule a massage",
        "booking_mode": "service",
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "status": "needs_clarification",
            "missing_slots": ["date", "time"]
        }
    },
    {
        "sentence": "book facial",
        "booking_mode": "service",
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "status": "needs_clarification",
            "missing_slots": ["date", "time"]
        }
    },
    {
        "sentence": "reserve room for tomorrow",
        "booking_mode": "reservation",
        "expected": {
            "intent": "CREATE_RESERVATION",
            "status": "needs_clarification",
            "missing_slots": ["end_date"]
        }
    },
    {
        "sentence": "book suite from october 10th",
        "booking_mode": "reservation",
        "expected": {
            "intent": "CREATE_RESERVATION",
            "status": "needs_clarification",
            "missing_slots": ["end_date"]
        }
    },
    {
        "sentence": "book haircut tomorrow",
        "booking_mode": "service",
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "status": "needs_clarification",
            "missing_slots": ["time"]
        }
    },
    {
        "sentence": "schedule massage friday",
        "booking_mode": "service",
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "status": "needs_clarification",
            "missing_slots": ["time"]
        }
    },
    {
        "sentence": "book facial at 3pm",
        "booking_mode": "service",
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "status": "needs_clarification",
            "missing_slots": ["date"]
        }
    },
    {
        "sentence": "schedule massage at 10am",
        "booking_mode": "service",
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "status": "needs_clarification",
            "missing_slots": ["date"]
        }
    },
]

