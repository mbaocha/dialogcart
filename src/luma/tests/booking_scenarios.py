# Booking intent scenarios (CREATE_APPOINTMENT, CREATE_RESERVATION)
from luma.config.core import STATUS_READY, STATUS_NEEDS_CLARIFICATION

booking_scenarios = [
    # ────────────────
    # RESERVATIONS — READY (DATE RANGE)
    # ────────────────
    {
        "sentence": "book me in for delux rom from oct 5th to 9th",
        "booking_mode": "reservation",
        "aliases": {
            "delux": "room"
        },
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
        "aliases": {
            "standard": "room"
        },
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
        "aliases": {
            "delux": "room"
        },
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
        "aliases": {
            "room": "room"
        },
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
        "sentence": "reserve a suite from feb 1st to fe 5th",
        "booking_mode": "reservation",
        "aliases": {
            "suite": "room"
        },
        "expected": {
            "intent": "CREATE_RESERVATION",
            "status": STATUS_READY,
            "slots": {
                "service_id": "suite",  # Single tenant alias for suite
                "date_range": {
                    "start": "2026-02-01",
                    "end": "2026-02-05"
                }
            }
        }
    },
    {
        "sentence": "book standard room from feb 10 to feb 15",
        "booking_mode": "reservation",
        "aliases": {
            "standard": "room"
        },
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
        "aliases": {
            "delux": "room"
        },
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
        "aliases": {
            "room": "room"
        },
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
        "aliases": {
            "suite": "room"
        },
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
        "aliases": {
            "hair cut": "haircut"
        },
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
        "sentence": "schedule beerd trim this friday at noon",
        "booking_mode": "service",
        "aliases": {
            "beerd": "beard grooming"
        },
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
        "aliases": {
            "massage": "massage"
        },
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
        # No aliases - facial is not in tenant aliases (UNSUPPORTED_SERVICE test)
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
        "aliases": {
            "haircut": "haircut"
        },
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
        "aliases": {
            "massage": "massage"
        },
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
        "sentence": "book beard trim this monday at 11am",
        "booking_mode": "service",
        "aliases": {
            "beard": "beard grooming"
        },
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
        # No aliases - facial is not in tenant aliases (UNSUPPORTED_SERVICE test)
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
        "aliases": {
            "massage": "massage"
        },
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
        "sentence": "schedule haircut this friday at 4pm",
        "booking_mode": "service",
        "aliases": {
            "haircut": "haircut"
        },
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
        "aliases": {
            "hair cut": "haircut"
        },
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
        "aliases": {
            "beerd": "beard grooming"
        },
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
        "aliases": {
            "massage": "massage"
        },
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
        # No aliases - facial is not in tenant aliases (UNSUPPORTED_SERVICE test)
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
        "aliases": {
            "massage": "massage"
        },
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
        "aliases": {
            "haircut": "haircut"
        },
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
        "sentence": "schedule massage this friday after 3pm",
        "booking_mode": "service",
        "aliases": {
            "massage": "massage"
        },
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
        # No aliases - facial is not in tenant aliases (UNSUPPORTED_SERVICE test)
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
        "aliases": {
            "haircut": "haircut"
        },
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
        "sentence": "schedule massage this friday morning",
        "booking_mode": "service",
        "aliases": {
            "massage": "massage"
        },
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
        "aliases": {
            "delux": "room"
        },
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
        "aliases": {
            "standard": "room"
        },
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
        "aliases": {
            "suite": "room"
        },
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
        "aliases": {
            "room": "room"
        },
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
        "aliases": {
            "haircut": "haircut"
        },
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
        "aliases": {
            "massage": "massage"
        },
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
    # EXPLICIT ALIAS MATCHING — Multiple aliases for same canonical
    # ────────────────
    {
        "sentence": "book premium haircut tomorrow at 2pm",
        "booking_mode": "service",
        "aliases": {
            "premium haircut": "haircut",
            "standard haircut": "haircut",
            "express haircut": "haircut"
        },
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "status": STATUS_READY,
            "slots": {
                "service_id": "premium haircut",  # Explicitly mentioned tenant alias
                "has_datetime": True
            }
        }
    },
    {
        "sentence": "schedule standard haircut next friday at 10am",
        "booking_mode": "service",
        "aliases": {
            "premium haircut": "haircut",
            "standard haircut": "haircut",
            "express haircut": "haircut"
        },
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "status": STATUS_READY,
            "slots": {
                "service_id": "standard haircut",  # Explicitly mentioned tenant alias
                "has_datetime": True
            }
        }
    },
    {
        "sentence": "book premium haircut tomorrow at 2pm",
        "booking_mode": "service",
        "aliases": {
            "premium spa treatment": "spa_treatment",
            "premium haircut": "haircut",
            "flexi haircut + prunning": "haircut",
        },
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "status": STATUS_READY,
            "slots": {
                # Explicitly mentioned tenant alias (short canonical form normalized to full)
                "service_id": "premium haircut",
                "has_datetime": True
            }
        }
    },
    {
        "sentence": "book premium haircut tomorrow at 2pm",
        "booking_mode": "service",
        "aliases": {
            "premium spa treatment": "beauty_and_wellness.spa_treatment",
            "premium haircut": "beauty_and_wellness.haircut",
            "flexi haircut + prunning": "beauty_and_wellness.haircut",
        },
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "status": STATUS_READY,
            "slots": {
                # Explicitly mentioned tenant alias (full canonical form)
                "service_id": "premium haircut",
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
        "aliases": {
            "room": "room"
        },
        "expected": {
            "intent": "CREATE_RESERVATION",
            "status": STATUS_NEEDS_CLARIFICATION,
            "missing_slots": ["start_date", "end_date"]
        }
    },
    {
        "sentence": "reserve a suite",
        "booking_mode": "reservation",
        "aliases": {
            "suite": "room"
        },
        "expected": {
            "intent": "CREATE_RESERVATION",
            "status": STATUS_NEEDS_CLARIFICATION,
            "missing_slots": ["start_date", "end_date"]
        }
    },
    {
        "sentence": "book a haircut",
        "booking_mode": "service",
        "aliases": {
            "haircut": "haircut"
        },
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "status": STATUS_NEEDS_CLARIFICATION,
            "missing_slots": ["date", "time"]
        }
    },
    {
        "sentence": "schedule a massage",
        "booking_mode": "service",
        "aliases": {
            "massage": "massage"
        },
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "status": STATUS_NEEDS_CLARIFICATION,
            "missing_slots": ["date", "time"]
        }
    },
    {
        "sentence": "book facial",
        "booking_mode": "service",
        # No aliases - facial is not in tenant aliases (UNSUPPORTED_SERVICE test)
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "status": STATUS_NEEDS_CLARIFICATION,
            "missing_slots": ["date", "time"]
        }
    },
    {
        "sentence": "reserve room for tomorrow",
        "booking_mode": "reservation",
        "aliases": {
            "room": "room"
        },
        "expected": {
            "intent": "CREATE_RESERVATION",
            "status": STATUS_NEEDS_CLARIFICATION,
            "missing_slots": ["end_date"]
        }
    },
    {
        "sentence": "book suite from october 10th",
        "booking_mode": "reservation",
        "aliases": {
            "suite": "room"
        },
        "expected": {
            "intent": "CREATE_RESERVATION",
            "status": STATUS_NEEDS_CLARIFICATION,
            "missing_slots": ["end_date"]
        }
    },
    {
        "sentence": "book haircut tomorrow",
        "booking_mode": "service",
        "aliases": {
            "haircut": "haircut"
        },
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "status": STATUS_NEEDS_CLARIFICATION,
            "missing_slots": ["time"]
        }
    },
    {
        "sentence": "schedule massage friday",
        "booking_mode": "service",
        "aliases": {
            "massage": "massage"
        },
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "status": STATUS_NEEDS_CLARIFICATION,
            "missing_slots": ["time"]
        }
    },
    {
        "sentence": "book facial at 3pm",
        "booking_mode": "service",
        # No aliases - facial is not in tenant aliases (UNSUPPORTED_SERVICE test)
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "status": STATUS_NEEDS_CLARIFICATION,
            "missing_slots": ["date"]
        }
    },
    {
        "sentence": "schedule massage at 10am",
        "booking_mode": "service",
        "aliases": {
            "massage": "massage"
        },
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
        "aliases": {
            "premum suite": "room"  # Tenant typo alias for fuzzy matching
        },
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
        "aliases": {
            "premum suite": "room"  # Tenant typo alias
        },
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
        "aliases": {
            "hair cut": "haircut"
        },
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
        "aliases": {
            "massage": "massage"
        },
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
        "sentence": "i'd like to schedual a beard trim this monday at 11am",
        "booking_mode": "service",
        "aliases": {
            "beard": "beard grooming"
        },
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
        "aliases": {
            "presidential room": "room"
        },
        "expected": {
            "intent": "CREATE_RESERVATION",
            "status": STATUS_READY,
            "slots": {
                # Fuzzy match to "presidential room" (typos: "presidental", "rom", "reserv")
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
        "aliases": {
            "presidential room": "room"
        },
        "expected": {
            "intent": "CREATE_RESERVATION",
            "status": STATUS_READY,
            "slots": {
                # Fuzzy match to "presidential room" (typo: "presdential")
                "service_id": "presidential room",
                "date_range": {
                    "start": "2026-11-01",
                    "end": "2026-11-05"
                }
            }
        }
    },
    {
        "sentence": "i want to make an apointment for a haircut this tuesday at 2pm",
        "booking_mode": "service",
        "aliases": {
            "haircut": "haircut"
        },
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
        "aliases": {
            "suite": "room"
        },
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
        # No aliases - facial is not in tenant aliases (UNSUPPORTED_SERVICE test)
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
        # No aliases - manicure is not in tenant aliases (UNSUPPORTED_SERVICE test)
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "status": STATUS_NEEDS_CLARIFICATION,
            # Note: "manicure" is in global canonical but NOT in tenant context
            "clarification_reason": "UNSUPPORTED_SERVICE"
        }
    },
    # ────────────────
    # MODIFY_BOOKING — RESCHEDULING EXISTING BOOKINGS
    # ────────────────
    {
        "sentence": "reschedule my booking ABC123 to tomorrow at 3pm",
        "booking_mode": "service",
        "aliases": {
            "haircut": "haircut"
        },
        "expected": {
            "intent": "MODIFY_BOOKING",
            "status": STATUS_READY,
            "slots": {
                "booking_id": "ABC123",
                "has_datetime": True
            }
        }
    },
    {
        "sentence": "change my appointment XYZ789 to next friday at 10am",
        "booking_mode": "service",
        "aliases": {
            "massage": "massage"
        },
        "expected": {
            "intent": "MODIFY_BOOKING",
            "status": STATUS_READY,
            "slots": {
                "booking_id": "XYZ789",
                "has_datetime": True
            }
        }
    },
    {
        "sentence": "move my reservation DEF456 from oct 5th to oct 10th",
        "booking_mode": "reservation",
        "aliases": {
            "suite": "room"
        },
        "expected": {
            "intent": "MODIFY_BOOKING",
            "status": STATUS_READY,
            "slots": {
                "booking_id": "DEF456",
                "date_range": {
                    "start": "2026-10-10",
                    "end": "2026-10-10"
                }
            }
        }
    },
    {
        "sentence": "update booking GHI789 to dec 15 to dec 20",
        "booking_mode": "reservation",
        "aliases": {
            "delux": "room"
        },
        "expected": {
            "intent": "MODIFY_BOOKING",
            "status": STATUS_READY,
            "slots": {
                "booking_id": "GHI789",
                "date_range": {
                    "start": "2026-12-15",
                    "end": "2026-12-20"
                }
            }
        }
    },
    {
        "sentence": "postpone my booking JKL012 to next monday at 2pm",
        "booking_mode": "service",
        "aliases": {
            "haircut": "haircut"
        },
        "expected": {
            "intent": "MODIFY_BOOKING",
            "status": STATUS_READY,
            "slots": {
                "booking_id": "JKL012",
                "has_datetime": True
            }
        }
    },
    {
        "sentence": "modify reservation MNO345 to november 1st through november 5th",
        "booking_mode": "reservation",
        "aliases": {
            "standard": "room"
        },
        "expected": {
            "intent": "MODIFY_BOOKING",
            "status": STATUS_READY,
            "slots": {
                "booking_id": "MNO345",
                "date_range": {
                    "start": "2026-11-01",
                    "end": "2026-11-05"
                }
            }
        }
    },
    {
        "sentence": "change the time for booking PQR678 to 4pm",
        "booking_mode": "service",
        "aliases": {
            "massage": "massage"
        },
        "expected": {
            "intent": "MODIFY_BOOKING",
            "status": STATUS_READY,
            "slots": {
                "booking_id": "PQR678",
                "has_datetime": True
            }
        }
    },
    {
        "sentence": "reschedule appointment STU901 to this friday evening",
        "booking_mode": "service",
        "aliases": {
            "haircut": "haircut"
        },
        "expected": {
            "intent": "MODIFY_BOOKING",
            "status": STATUS_READY,
            "slots": {
                "booking_id": "STU901",
                "has_datetime": True
            }
        }
    },
    {
        "sentence": "update my room booking VWX234 to jan 10 to jan 15",
        "booking_mode": "reservation",
        "aliases": {
            "premium suite": "room"
        },
        "expected": {
            "intent": "MODIFY_BOOKING",
            "status": STATUS_READY,
            "slots": {
                "booking_id": "VWX234",
                "date_range": {
                    "start": "2026-01-10",
                    "end": "2026-01-15"
                }
            }
        }
    },
    {
        "sentence": "change booking YZA567 time to tomorrow at noon",
        "booking_mode": "service",
        "aliases": {
            "beard": "beard grooming"
        },
        "expected": {
            "intent": "MODIFY_BOOKING",
            "status": STATUS_READY,
            "slots": {
                "booking_id": "YZA567",
                "has_datetime": True
            }
        }
    },
    # ────────────────
    # CANCEL_BOOKING — CANCELLING EXISTING BOOKINGS
    # ────────────────
    {
        "sentence": "cancel my booking BCD890",
        "booking_mode": "service",
        "aliases": {},
        "expected": {
            "intent": "CANCEL_BOOKING",
            "status": STATUS_READY,
            "slots": {
                "booking_id": "BCD890"
            }
        }
    },
    {
        "sentence": "cancel booking EFG123",
        "booking_mode": "service",
        "aliases": {},
        "expected": {
            "intent": "CANCEL_BOOKING",
            "status": STATUS_READY,
            "slots": {
                "booking_id": "EFG123"
            }
        }
    },
    {
        "sentence": "delete my reservation HIJ456",
        "booking_mode": "reservation",
        "aliases": {},
        "expected": {
            "intent": "CANCEL_BOOKING",
            "status": STATUS_READY,
            "slots": {
                "booking_id": "HIJ456"
            }
        }
    },
    {
        "sentence": "cancel my appointment KLM789",
        "booking_mode": "service",
        "aliases": {},
        "expected": {
            "intent": "CANCEL_BOOKING",
            "status": STATUS_READY,
            "slots": {
                "booking_id": "KLM789"
            }
        }
    },
    {
        "sentence": "i need to cancel booking NOP012",
        "booking_mode": "service",
        "aliases": {},
        "expected": {
            "intent": "CANCEL_BOOKING",
            "status": STATUS_READY,
            "slots": {
                "booking_id": "NOP012"
            }
        }
    },
    {
        "sentence": "please cancel reservation QRS345",
        "booking_mode": "reservation",
        "aliases": {},
        "expected": {
            "intent": "CANCEL_BOOKING",
            "status": STATUS_READY,
            "slots": {
                "booking_id": "QRS345"
            }
        }
    },
    {
        "sentence": "delete booking TUV678",
        "booking_mode": "service",
        "aliases": {},
        "expected": {
            "intent": "CANCEL_BOOKING",
            "status": STATUS_READY,
            "slots": {
                "booking_id": "TUV678"
            }
        }
    },
    {
        "sentence": "cancel my room reservation WXY901",
        "booking_mode": "reservation",
        "aliases": {},
        "expected": {
            "intent": "CANCEL_BOOKING",
            "status": STATUS_READY,
            "slots": {
                "booking_id": "WXY901"
            }
        }
    },
    {
        "sentence": "i want to cancel appointment ZAB234",
        "booking_mode": "service",
        "aliases": {},
        "expected": {
            "intent": "CANCEL_BOOKING",
            "status": STATUS_READY,
            "slots": {
                "booking_id": "ZAB234"
            }
        }
    },
    {
        "sentence": "can't make it, cancel booking CDE567",
        "booking_mode": "service",
        "aliases": {},
        "expected": {
            "intent": "CANCEL_BOOKING",
            "status": STATUS_READY,
            "slots": {
                "booking_id": "CDE567"
            }
        }
    },
    # ────────────────
    # MODIFY_BOOKING — NEEDS_CLARIFICATION (MISSING BOOKING_ID OR NEW TIME)
    # ────────────────
    {
        "sentence": "reschedule my booking",
        "booking_mode": "service",
        "aliases": {
            "haircut": "haircut"
        },
        "expected": {
            "intent": "MODIFY_BOOKING",
            "status": STATUS_NEEDS_CLARIFICATION,
            "missing_slots": ["booking_id", "date", "time"]
        }
    },
    {
        "sentence": "change my appointment",
        "booking_mode": "service",
        "aliases": {
            "massage": "massage"
        },
        "expected": {
            "intent": "MODIFY_BOOKING",
            "status": STATUS_NEEDS_CLARIFICATION,
            "missing_slots": ["booking_id", "date", "time"]
        }
    },
    {
        "sentence": "modify booking FGH890",
        "booking_mode": "service",
        "aliases": {},
        "expected": {
            "intent": "MODIFY_BOOKING",
            "status": STATUS_NEEDS_CLARIFICATION,
            "missing_slots": ["date", "time"]
        }
    },
    {
        "sentence": "reschedule reservation IJK123",
        "booking_mode": "reservation",
        "aliases": {},
        "expected": {
            "intent": "MODIFY_BOOKING",
            "status": STATUS_NEEDS_CLARIFICATION,
            "missing_slots": ["start_date", "end_date"]
        }
    },
    {
        "sentence": "change booking time to 3pm",
        "booking_mode": "service",
        "aliases": {},
        "expected": {
            "intent": "MODIFY_BOOKING",
            "status": STATUS_NEEDS_CLARIFICATION,
            "missing_slots": ["booking_id", "date"]
        }
    },
    {
        "sentence": "move booking to tomorrow",
        "booking_mode": "service",
        "aliases": {},
        "expected": {
            "intent": "MODIFY_BOOKING",
            "status": STATUS_NEEDS_CLARIFICATION,
            "missing_slots": ["booking_id", "time"]
        }
    },
    # ────────────────
    # CANCEL_BOOKING — NEEDS_CLARIFICATION (MISSING BOOKING_ID)
    # ────────────────
    {
        "sentence": "cancel my booking",
        "booking_mode": "service",
        "aliases": {},
        "expected": {
            "intent": "CANCEL_BOOKING",
            "status": STATUS_NEEDS_CLARIFICATION,
            "missing_slots": ["booking_id"]
        }
    },
    {
        "sentence": "delete my reservation",
        "booking_mode": "reservation",
        "aliases": {},
        "expected": {
            "intent": "CANCEL_BOOKING",
            "status": STATUS_NEEDS_CLARIFICATION,
            "missing_slots": ["booking_id"]
        }
    },
    {
        "sentence": "i need to cancel",
        "booking_mode": "service",
        "aliases": {},
        "expected": {
            "intent": "CANCEL_BOOKING",
            "status": STATUS_NEEDS_CLARIFICATION,
            "missing_slots": ["booking_id"]
        }
    },
    {
        "sentence": "cancel my appointment",
        "booking_mode": "service",
        "aliases": {},
        "expected": {
            "intent": "CANCEL_BOOKING",
            "status": STATUS_NEEDS_CLARIFICATION,
            "missing_slots": ["booking_id"]
        }
    },
    {
        "sentence": "please cancel",
        "booking_mode": "service",
        "aliases": {},
        "expected": {
            "intent": "CANCEL_BOOKING",
            "status": STATUS_NEEDS_CLARIFICATION,
            "missing_slots": ["booking_id"]
        }
    },
    # ────────────────
    # MODIFY_BOOKING — FUZZY MATCHING (TYPOS IN BOOKING_ID)
    # ────────────────
    {
        "sentence": "reschedule booking LMN456 to tomorrow at 2pm",
        "booking_mode": "service",
        "aliases": {
            "haircut": "haircut"
        },
        "expected": {
            "intent": "MODIFY_BOOKING",
            "status": STATUS_READY,
            "slots": {
                "booking_id": "LMN456",
                "has_datetime": True
            }
        }
    },
    {
        "sentence": "change apointment OPQ789 to next friday",
        "booking_mode": "service",
        "aliases": {
            "massage": "massage"
        },
        "expected": {
            "intent": "MODIFY_BOOKING",
            "status": STATUS_READY,
            "slots": {
                "booking_id": "OPQ789",
                "has_datetime": True
            }
        }
    },
    {
        "sentence": "modify reservtion RST012 to dec 20 to dec 25",
        "booking_mode": "reservation",
        "aliases": {
            "suite": "room"
        },
        "expected": {
            "intent": "MODIFY_BOOKING",
            "status": STATUS_READY,
            "slots": {
                "booking_id": "RST012",
                "date_range": {
                    "start": "2026-12-20",
                    "end": "2026-12-25"
                }
            }
        }
    },
    # ────────────────
    # MODIFY_BOOKING — DELTA BEHAVIOR INVARIANT TESTS
    # ────────────────
    # 1) Full date-range modification
    {
        "sentence": "change my reservation ABC123 to feb 9 to feb 11",
        "booking_mode": "reservation",
        "aliases": {},
        "expected": {
            "intent": "MODIFY_BOOKING",
            "status": STATUS_READY,
            "slots": {
                "booking_id": "ABC123",
                "start_date": "2026-02-09",
                "end_date": "2026-02-11"
            }
        }
    },
    # 2) Partial modification
    {
        "sentence": "change my reservation ABC123 to feb 9",
        "booking_mode": "reservation",
        "aliases": {},
        "expected": {
            "intent": "MODIFY_BOOKING",
            "status": STATUS_NEEDS_CLARIFICATION,
            "missing_slots": ["end_date"]
        }
    },
    # 3) Time-only modification
    {
        "sentence": "move my booking ABC123 to 3pm",
        "booking_mode": "service",
        "aliases": {},
        "expected": {
            "intent": "MODIFY_BOOKING",
            "status": STATUS_READY,
            "slots": {
                "booking_id": "ABC123",
                "has_datetime": True
            }
        }
    },
    # 4) No-delta modification
    {
        "sentence": "reschedule my booking ABC123",
        "booking_mode": "service",
        "aliases": {},
        "expected": {
            "intent": "MODIFY_BOOKING",
            "status": STATUS_NEEDS_CLARIFICATION,
            "missing_slots": ["change"]
        }
    },
    # ────────────────
    # UNKNOWN / FRAGMENT INPUTS
    # ────────────────
    # These tests enforce that Luma does NOT promote intent, does NOT invent missing_slots,
    # only returns extracted slots, and remains stateless for inputs without booking intent verbs.
    {
        "sentence": "feb 12th",
        "booking_mode": "service",
        "aliases": {},
        "expected": {
            "intent": "UNKNOWN",
            "status": STATUS_READY,
            "slots": {
                "date": "2026-02-12"
            }
        }
    },
    {
        "sentence": "3pm",
        "booking_mode": "service",
        "aliases": {},
        "expected": {
            "intent": "UNKNOWN",
            "status": STATUS_READY,
            "slots": {
                "time": "15:00"
            }
        }
    },
    {
        "sentence": "feb 12th at 3pm",
        "booking_mode": "service",
        "aliases": {},
        "expected": {
            "intent": "UNKNOWN",
            "status": STATUS_READY,
            "slots": {
                "date": "2026-02-12",
                "time": "15:00"
            }
        }
    },
    {
        "sentence": "from april 12th to april 16th",
        "booking_mode": "reservation",
        "aliases": {},
        "expected": {
            "intent": "UNKNOWN",
            "status": STATUS_READY,
            "slots": {
                "date_range": {
                    "start": "2026-04-12",
                    "end": "2026-04-16"
                }
            }
        }
    },
    {
        "sentence": "deluxe room",
        "booking_mode": "reservation",
        "aliases": {"deluxe room": "room"},
        "expected": {
            "intent": "UNKNOWN",
            "status": STATUS_READY,
            "slots": {
                "service_id": "deluxe room"
            }
        }
    },
    {
        "sentence": "haircut tomorrow",
        "booking_mode": "service",
        "aliases": {"haircut": "haircut"},
        "expected": {
            "intent": "UNKNOWN",
            "status": STATUS_READY,
            "slots": {
                "service_id": "haircut",
                "date": "2026-01-13"
            }
        }
    },
]
