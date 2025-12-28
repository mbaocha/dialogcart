# Booking intent scenarios (CREATE_APPOINTMENT, CREATE_RESERVATION)
from luma.config.core import STATUS_READY, STATUS_NEEDS_CLARIFICATION

booking_scenarios = [
    # ────────────────
    # RESERVATIONS — READY (DATE RANGE)
    # ────────────────
    {
        "sentence": "book me in for delux rom from oct 5th to 9th",
        "booking_mode": "reservation",
        "expected": {
            "intent": "CREATE_RESERVATION",
            "status": STATUS_READY,
            "slots": {
                "service_id": "delux",  # Explicitly mentioned tenant alias
                "date_range": {
                    "start": "2026-10-05",
                    "end": "2026-10-09"
                }
            }
        }
    },
    {
        "sentence": "reserve standard room october 12 to 14",
        "booking_mode": "reservation",
        "expected": {
            "intent": "CREATE_RESERVATION",
            "status": STATUS_READY,
            "slots": {
                "service_id": "standard",  # Explicit tenant alias key
                "date_range": {
                    "start": "2026-10-12",
                    "end": "2026-10-14"
                }
            }
        }
    },
    {
        "sentence": "book delux room from nov 1st to 3rd",
        "booking_mode": "reservation",
        "expected": {
            "intent": "CREATE_RESERVATION",
            "status": STATUS_READY,
            "slots": {
                "service_id": "delux",  # Explicit tenant alias key
                "date_range": {
                    "start": "2026-11-01",
                    "end": "2026-11-03"
                }
            }
        }
    },
    {
        "sentence": "book me a room dec 20 2026 to dec 25 2026",
        "booking_mode": "reservation",
        "expected": {
            "intent": "CREATE_RESERVATION",
            "status": STATUS_READY,
            "slots": {
                "service_id": "room",  # Explicit tenant alias key
                "date_range": {
                    "start": "2026-12-20",
                    "end": "2026-12-25"
                }
            }
        }
    },
    {
        "sentence": "reserve a suite from january 1st to january 5th",
        "booking_mode": "reservation",
        "expected": {
            "intent": "CREATE_RESERVATION",
            "status": STATUS_READY,
            "slots": {
                "service_id": "suite",  # Single tenant alias for suite
                "date_range": {
                    "start": "2026-01-01",
                    "end": "2026-01-05"
                }
            }
        }
    },
    {
        "sentence": "book standard room from feb 10 to feb 15",
        "booking_mode": "reservation",
        "expected": {
            "intent": "CREATE_RESERVATION",
            "status": STATUS_READY,
            "slots": {
                "service_id": "standard",  # Explicit tenant alias key
                "date_range": {
                    "start": "2026-02-10",
                    "end": "2026-02-15"
                }
            }
        }
    },
    {
        "sentence": "reserve delux room march 1 to march 5",
        "booking_mode": "reservation",
        "expected": {
            "intent": "CREATE_RESERVATION",
            "status": STATUS_READY,
            "slots": {
                "service_id": "delux",  # Explicit tenant alias key
                "date_range": {
                    "start": "2026-03-01",
                    "end": "2026-03-05"
                }
            }
        }
    },
    {
        "sentence": "book a room from april 10th to april 15th",
        "booking_mode": "reservation",
        "expected": {
            "intent": "CREATE_RESERVATION",
            "status": STATUS_READY,
            "slots": {
                "service_id": "room",  # Explicit tenant alias key
                "date_range": {
                    "start": "2026-04-10",
                    "end": "2026-04-15"
                }
            }
        }
    },
    {
        "sentence": "reserve suite may 5 to may 10",
        "booking_mode": "reservation",
        "expected": {
            "intent": "CREATE_RESERVATION",
            "status": STATUS_READY,
            "slots": {
                "service_id": "suite",  # Single tenant alias for suite
                "date_range": {
                    "start": "2026-05-05",
                    "end": "2026-05-10"
                }
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
            "status": STATUS_READY,
            "slots": {
                "service_id": "hair cut",  # Tenant alias key explicitly mentioned
                "has_datetime": True
            }
        }
    },
    {
        "sentence": "schedule beerd trim friday at noon",
        "booking_mode": "service",
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "status": STATUS_READY,
            "slots": {
                "service_id": "beerd",  # Tenant alias key explicitly mentioned
                "has_datetime": True
            }
        }
    },
    {
        "sentence": "book massage next monday at 10am",
        "booking_mode": "service",
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "status": STATUS_READY,
            "slots": {
                "service_id": "massage",  # Tenant alias key exists
                "has_datetime": True
            }
        }
    },
    {
        "sentence": "schedule facial tomorrow evening",
        "booking_mode": "service",
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "status": STATUS_NEEDS_CLARIFICATION,
            # Canonical service not in tenant aliases
            "clarification_reason": "UNSUPPORTED_SERVICE"
        }
    },
    {
        "sentence": "book haircut for next friday at 2pm",
        "booking_mode": "service",
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "status": STATUS_READY,
            "slots": {
                # Single tenant alias for haircut (used by default)
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
            "status": STATUS_READY,
            "slots": {
                "service_id": "massage",  # Tenant alias key exists
                "has_datetime": True
            }
        }
    },
    {
        "sentence": "book beard trim on monday at 11am",
        "booking_mode": "service",
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "status": STATUS_READY,
            "slots": {
                # Single tenant alias for beard grooming (used by default)
                "service_id": "beard",
                "has_datetime": True
            }
        }
    },
    {
        "sentence": "schedule facial next tuesday at 3pm",
        "booking_mode": "service",
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "status": STATUS_NEEDS_CLARIFICATION,
            # Canonical service not in tenant aliases
            "clarification_reason": "UNSUPPORTED_SERVICE"
        }
    },
    {
        "sentence": "book massage tomorrow at 2pm",
        "booking_mode": "service",
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "status": STATUS_READY,
            "slots": {
                "service_id": "massage",  # Tenant alias key exists
                "has_datetime": True
            }
        }
    },
    {
        "sentence": "schedule haircut friday at 4pm",
        "booking_mode": "service",
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "status": STATUS_READY,
            "slots": {
                # Single tenant alias for haircut (used by default)
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
            "status": STATUS_READY,
            "slots": {
                "service_id": "hair cut",  # Tenant alias key explicitly mentioned
                "has_datetime": True
            }
        }
    },
    {
        "sentence": "book beerd trim tomorrow at 11am",
        "booking_mode": "service",
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "status": STATUS_READY,
            "slots": {
                "service_id": "beerd",  # Tenant alias key explicitly mentioned
                "has_datetime": True
            }
        }
    },
    {
        "sentence": "book massage tomorrow at 4pm",
        "booking_mode": "service",
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "status": STATUS_READY,
            "slots": {
                "service_id": "massage",  # Tenant alias key exists
                "has_datetime": True
            }
        }
    },
    {
        "sentence": "book facial tomorrow at 1pm",
        "booking_mode": "service",
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "status": STATUS_NEEDS_CLARIFICATION,
            # Canonical service not in tenant aliases
            "clarification_reason": "UNSUPPORTED_SERVICE"
        }
    },
    {
        "sentence": "schedule massage next monday at 9am",
        "booking_mode": "service",
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "status": STATUS_READY,
            "slots": {
                "service_id": "massage",  # Tenant alias key exists
                "has_datetime": True
            }
        }
    },
    {
        "sentence": "book haircut next friday at 3pm",
        "booking_mode": "service",
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "status": STATUS_READY,
            "slots": {
                # Single tenant alias for haircut (used by default)
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
            "status": STATUS_READY,
            "slots": {
                "service_id": "massage",  # Tenant alias key exists
                "has_datetime": True
            }
        }
    },
    {
        "sentence": "book facial monday before 11am",
        "booking_mode": "service",
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "status": STATUS_NEEDS_CLARIFICATION,
            # Canonical service not in tenant aliases
            "clarification_reason": "UNSUPPORTED_SERVICE"
        }
    },
    {
        "sentence": "book haircut tomorrow evening",
        "booking_mode": "service",
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "status": STATUS_READY,
            "slots": {
                # Single tenant alias for haircut (used by default)
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
            "status": STATUS_READY,
            "slots": {
                "service_id": "massage",  # Tenant alias key exists
                "has_datetime": True
            }
        }
    },
    {
        "sentence": "book delux room nov 5 to 7 at night",
        "booking_mode": "reservation",
        "expected": {
            "intent": "CREATE_RESERVATION",
            "status": STATUS_READY,
            "slots": {
                "service_id": "delux",  # Explicit tenant alias key
                "date_range": {
                    "start": "2026-11-05",
                    "end": "2026-11-07"
                }
            }
        }
    },
    {
        "sentence": "reserve standard room from dec 1 to dec 5 in the morning",
        "booking_mode": "reservation",
        "expected": {
            "intent": "CREATE_RESERVATION",
            "status": STATUS_READY,
            "slots": {
                "service_id": "standard",  # Explicit tenant alias key
                "date_range": {
                    "start": "2026-12-01",
                    "end": "2026-12-05"
                }
            }
        }
    },
    {
        "sentence": "book suite jan 10 to jan 15 evening",
        "booking_mode": "reservation",
        "expected": {
            "intent": "CREATE_RESERVATION",
            "status": STATUS_READY,
            "slots": {
                "service_id": "suite",  # Single tenant alias for suite
                "date_range": {
                    "start": "2026-01-10",
                    "end": "2026-01-15"
                }
            }
        }
    },
    {
        "sentence": "reserve room from feb 20 to feb 25 afternoon",
        "booking_mode": "reservation",
        "expected": {
            "intent": "CREATE_RESERVATION",
            "status": STATUS_READY,
            "slots": {
                "service_id": "room",  # Explicit tenant alias key
                "date_range": {
                    "start": "2026-02-20",
                    "end": "2026-02-25"
                }
            }
        }
    },
    {
        "sentence": "book haircut tomorrow at noon",
        "booking_mode": "service",
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "status": STATUS_READY,
            "slots": {
                # Single tenant alias for haircut (used by default)
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
            "status": STATUS_READY,
            "slots": {
                "service_id": "massage",  # Tenant alias key exists
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
            "status": STATUS_NEEDS_CLARIFICATION,
            "missing_slots": ["start_date", "end_date"]
        }
    },
    {
        "sentence": "reserve a suite",
        "booking_mode": "reservation",
        "expected": {
            "intent": "CREATE_RESERVATION",
            "status": STATUS_NEEDS_CLARIFICATION,
            "missing_slots": ["start_date", "end_date"]
        }
    },
    {
        "sentence": "book a haircut",
        "booking_mode": "service",
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "status": STATUS_NEEDS_CLARIFICATION,
            "missing_slots": ["date", "time"]
        }
    },
    {
        "sentence": "schedule a massage",
        "booking_mode": "service",
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "status": STATUS_NEEDS_CLARIFICATION,
            "missing_slots": ["date", "time"]
        }
    },
    {
        "sentence": "book facial",
        "booking_mode": "service",
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "status": STATUS_NEEDS_CLARIFICATION,
            "missing_slots": ["date", "time"]
        }
    },
    {
        "sentence": "reserve room for tomorrow",
        "booking_mode": "reservation",
        "expected": {
            "intent": "CREATE_RESERVATION",
            "status": STATUS_NEEDS_CLARIFICATION,
            "missing_slots": ["end_date"]
        }
    },
    {
        "sentence": "book suite from october 10th",
        "booking_mode": "reservation",
        "expected": {
            "intent": "CREATE_RESERVATION",
            "status": STATUS_NEEDS_CLARIFICATION,
            "missing_slots": ["end_date"]
        }
    },
    {
        "sentence": "book haircut tomorrow",
        "booking_mode": "service",
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "status": STATUS_NEEDS_CLARIFICATION,
            "missing_slots": ["time"]
        }
    },
    {
        "sentence": "schedule massage friday",
        "booking_mode": "service",
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "status": STATUS_NEEDS_CLARIFICATION,
            "missing_slots": ["time"]
        }
    },
    {
        "sentence": "book facial at 3pm",
        "booking_mode": "service",
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "status": STATUS_NEEDS_CLARIFICATION,
            "missing_slots": ["date"]
        }
    },
    {
        "sentence": "schedule massage at 10am",
        "booking_mode": "service",
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "status": STATUS_NEEDS_CLARIFICATION,
            "missing_slots": ["date"]
        }
    },
    # ────────────────
    # FUZZY MATCHING TESTS — Tenant typo tolerance
    # ────────────────
    {
        "sentence": "book me in for premium suite from oct 5th to 9th",
        "booking_mode": "reservation",
        "expected": {
            "intent": "CREATE_RESERVATION",
            "status": STATUS_READY,
            "slots": {
                "service_id": "premum suite",  # Fuzzy matched to tenant typo alias
                "date_range": {
                    "start": "2026-10-05",
                    "end": "2026-10-09"
                }
            }
        }
    },
    {
        "sentence": "reserve premum suite october 12 to 14",
        "booking_mode": "reservation",
        "expected": {
            "intent": "CREATE_RESERVATION",
            "status": STATUS_READY,
            "slots": {
                "service_id": "premum suite",  # Exact match to tenant typo alias
                "date_range": {
                    "start": "2026-10-12",
                    "end": "2026-10-14"
                }
            }
        }
    },
    # ────────────────
    # EXTENDED BOOKING SCENARIOS — Various phrasings with spelling errors
    # ────────────────
    {
        "sentence": "i need a hair cut tomorow at 3pm",
        "booking_mode": "service",
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "status": STATUS_READY,
            "slots": {
                # Tenant alias (typo in "tomorow" handled by date extraction)
                "service_id": "hair cut",
                "has_datetime": True
            }
        }
    },
    {
        "sentence": "can u book me a massge for next friday morning",
        "booking_mode": "service",
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "status": STATUS_READY,
            "slots": {
                # Fuzzy match to "massage" (typo: "massge")
                "service_id": "massage",
                "has_datetime": True
            }
        }
    },
    {
        "sentence": "i'd like to schedual a beard trim on monday at 11am",
        "booking_mode": "service",
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "status": STATUS_READY,
            "slots": {
                "service_id": "beard",  # Tenant alias (typo: "schedual")
                "has_datetime": True
            }
        }
    },
    {
        "sentence": "reserv a presidental rom from dec 15th to dec 20th",
        "booking_mode": "reservation",
        "expected": {
            "intent": "CREATE_RESERVATION",
            "status": STATUS_READY,
            "slots": {
                # Fuzzy match to "standard" (typo: "standrd", "reserv")
                "service_id": "presidential room",
                "date_range": {
                    "start": "2026-12-15",
                    "end": "2026-12-20"
                }
            }
        }
    },
    {
        "sentence": "book me a presdential room november 1st through november 5th",
        "booking_mode": "reservation",
        "expected": {
            "intent": "CREATE_RESERVATION",
            "status": STATUS_READY,
            "slots": {
                # Fuzzy match to "delux" (user says "deluxe")
                "service_id": "presidential room",
                "date_range": {
                    "start": "2026-11-01",
                    "end": "2026-11-05"
                }
            }
        }
    },
    {
        "sentence": "i want to make an apointment for a haircut tuesday at 2pm",
        "booking_mode": "service",
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "status": STATUS_READY,
            "slots": {
                "service_id": "haircut",  # Tenant alias (typo: "apointment")
                "has_datetime": True
            }
        }
    },
    {
        "sentence": "can i get a suite from jan 10 to jan 15 please",
        "booking_mode": "reservation",
        "expected": {
            "intent": "CREATE_RESERVATION",
            "status": STATUS_READY,
            "slots": {
                "service_id": "suite",
                "date_range": {
                    "start": "2026-01-10",
                    "end": "2026-01-15"
                }
            }
        }
    },

    {
        "sentence": "book me a facial treatment next wednesday at 4pm",
        "booking_mode": "service",
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "status": STATUS_NEEDS_CLARIFICATION,
            # Note: "facial" is in global canonical but NOT in tenant context
            "clarification_reason": "UNSUPPORTED_SERVICE"
        }
    },
    {
        "sentence": "schedule a manicure for friday afternoon",
        "booking_mode": "service",
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "status": STATUS_NEEDS_CLARIFICATION,
            # Note: "manicure" is in global canonical but NOT in tenant context
            "clarification_reason": "UNSUPPORTED_SERVICE"
        }
    },

]
