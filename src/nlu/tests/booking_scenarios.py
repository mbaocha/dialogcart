# Booking intent scenarios (CREATE_APPOINTMENT, CREATE_RESERVATION)
# Luma is a pure fact extractor - tests assert extraction only, no semantics

booking_scenarios = [
    # ────────────────
    # CREATE_RESERVATION — Date range extraction
    # ────────────────
    {
        "sentence": "book me in for delux rom from oct 5th to 9th",
        "booking_mode": "reservation",
        "aliases": {"delux": "room"},
        "expected": {
            "intent": "CREATE_RESERVATION",
            "facts": {"service_id": "delux", "dates": ["2026-10-05", "2026-10-09"]},
        },
    },
    {
        "sentence": "book me a room dec 20 2026 to dec 25 2026",
        "booking_mode": "reservation",
        "aliases": {"room": "room"},
        "expected": {
            "intent": "CREATE_RESERVATION",
            "facts": {"service_id": "room", "dates": ["2026-12-20", "2026-12-25"]},
        },
    },
    {
        "sentence": "reserve a suite from feb 1st to fe 5th",
        "booking_mode": "reservation",
        "aliases": {"suite": "room"},
        "expected": {
            "intent": "CREATE_RESERVATION",
            "facts": {"service_id": "suite", "dates": ["2026-02-01", "2026-02-05"]},
        },
    },
    {
        "sentence": "reserve delux room march 1 to march 5",
        "booking_mode": "reservation",
        "aliases": {"delux": "room"},
        "expected": {
            "intent": "CREATE_RESERVATION",
            "facts": {"service_id": "delux", "dates": ["2026-03-01", "2026-03-05"]},
        },
    },
    {
        "sentence": "reserve suite may 5 to may 10",
        "booking_mode": "reservation",
        "aliases": {"suite": "room"},
        "expected": {
            "intent": "CREATE_RESERVATION",
            "facts": {"service_id": "suite", "dates": ["2026-05-05", "2026-05-10"]},
        },
    },
    {
        "sentence": "book delux room nov 5 to 7 at night",
        "booking_mode": "reservation",
        "aliases": {"delux": "room"},
        "expected": {
            "intent": "CREATE_RESERVATION",
            "facts": {"service_id": "delux", "dates": ["2026-11-05", "2026-11-07"]},
        },
    },
    # ────────────────
    # CREATE_APPOINTMENT — Date and time extraction
    # ────────────────
    {
        "sentence": "book hair cut tomorrow at 3pm",
        "booking_mode": "service",
        "aliases": {"hair cut": "haircut"},
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "facts": {"service_id": "hair cut", "dates": ["2026-01-14"]},
            "time_constraint": {
                "mode": "exact",
                "start": "15:00",
                "end": "15:00",
                "label": None,
            },
        },
    },
    {
        "sentence": "schedule beerd trim this friday at noon",
        "booking_mode": "service",
        "aliases": {"beerd": "beard grooming"},
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "facts": {"service_id": "beerd", "dates": ["2026-01-16"]},
            "time_constraint": {
                "mode": "exact",
                "start": "12:00",
                "end": "12:00",
                "label": None,
            },
        },
    },
    {
        "sentence": "book massage next monday at 10am",
        "booking_mode": "service",
        "aliases": {"massage": "massage"},
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "facts": {"service_id": "massage", "dates": ["2026-01-19"]},
            "time_constraint": {
                "mode": "exact",
                "start": "10:00",
                "end": "10:00",
                "label": None,
            },
        },
    },
    {
        "sentence": "book haircut for next friday at 2pm",
        "booking_mode": "service",
        "aliases": {"haircut": "haircut"},
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "facts": {"service_id": "haircut", "dates": ["2026-01-23"]},
            "time_constraint": {
                "mode": "exact",
                "start": "14:00",
                "end": "14:00",
                "label": None,
            },
        },
    },
    {
        "sentence": "book beard trim this monday at 11am",
        "booking_mode": "service",
        "aliases": {"beard": "beard grooming"},
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "facts": {"service_id": "beard", "dates": ["2026-01-19"]},
            "time_constraint": {
                "mode": "exact",
                "start": "11:00",
                "end": "11:00",
                "label": None,
            },
        },
    },
    {
        "sentence": "schedule massage this friday after 3pm",
        "booking_mode": "service",
        "aliases": {"massage": "massage"},
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "facts": {"service_id": "massage", "dates": ["2026-01-16"]},
            "time_constraint": {
                "mode": "exact",
                "start": "15:00",
                "end": "23:59",
                "label": None,
            },
        },
    },
    {
        "sentence": "book haircut tomorrow evening",
        "booking_mode": "service",
        "aliases": {"haircut": "haircut"},
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "facts": {"service_id": "haircut", "dates": ["2026-01-14"]},
            "time_constraint": {
                "mode": "fuzzy",
                "label": "evening",
                "start": "17:00",
                "end": "21:59",
            },
        },
    },
    {
        "sentence": "schedule massage this friday morning",
        "booking_mode": "service",
        "aliases": {"massage": "massage"},
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "facts": {"service_id": "massage", "dates": ["2026-01-16"]},
            "time_constraint": {
                "mode": "fuzzy",
                "label": "morning",
                "start": "00:00",
                "end": "11:59",
            },
        },
    },
    {
        "sentence": "schedule massage tomorrow at midnight",
        "booking_mode": "service",
        "aliases": {"massage": "massage"},
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "facts": {"service_id": "massage", "dates": ["2026-01-14"]},
            "time_constraint": {
                "mode": "exact",
                "start": "00:00",
                "end": "00:00",
                "label": None,
            },
        },
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
            "express haircut": "haircut",
        },
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "facts": {"service_id": "premium haircut", "dates": ["2026-01-14"]},
            "time_constraint": {
                "mode": "exact",
                "start": "14:00",
                "end": "14:00",
                "label": None,
            },
        },
    },
    {
        "sentence": "schedule standard haircut next friday at 10am",
        "booking_mode": "service",
        "aliases": {
            "premium haircut": "haircut",
            "standard haircut": "haircut",
            "express haircut": "haircut",
        },
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "facts": {"service_id": "standard haircut", "dates": ["2026-01-23"]},
            "time_constraint": {
                "mode": "exact",
                "start": "10:00",
                "end": "10:00",
                "label": None,
            },
        },
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
            "facts": {"service_id": "premium haircut", "dates": ["2026-01-14"]},
            "time_constraint": {
                "mode": "exact",
                "start": "14:00",
                "end": "14:00",
                "label": None,
            },
        },
    },
    # ────────────────
    # FUZZY MATCHING TESTS — Tenant typo tolerance
    # ────────────────
    {
        "sentence": "book me in for premium suite from oct 5th to 9th",
        "booking_mode": "reservation",
        "aliases": {"premum suite": "room"},
        "expected": {
            "intent": "CREATE_RESERVATION",
            "facts": {
                "service_id": "premum suite",
                "dates": ["2026-10-05", "2026-10-09"],
            },
        },
    },
    {
        "sentence": "reserve premum suite october 12 to 14",
        "booking_mode": "reservation",
        "aliases": {"premum suite": "room"},
        "expected": {
            "intent": "CREATE_RESERVATION",
            "facts": {
                "service_id": "premum suite",
                "dates": ["2026-10-12", "2026-10-14"],
            },
        },
    },
    # ────────────────
    # EXTENDED BOOKING SCENARIOS — Various phrasings with spelling errors
    # ────────────────
    {
        "sentence": "i need a hair cut tomorow at 3pm",
        "booking_mode": "service",
        "aliases": {"hair cut": "haircut"},
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "facts": {"service_id": "hair cut", "dates": ["2026-01-14"]},
            "time_constraint": {
                "mode": "exact",
                "start": "15:00",
                "end": "15:00",
                "label": None,
            },
        },
    },
    {
        "sentence": "can u book me a massge for next friday morning",
        "booking_mode": "service",
        "aliases": {"massage": "massage"},
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "facts": {"service_id": "massage", "dates": ["2026-01-23"]},
            "time_constraint": {
                "mode": "fuzzy",
                "label": "morning",
                "start": "00:00",
                "end": "11:59",
            },
        },
    },
    {
        "sentence": "reserv a presidental rom from dec 15th to dec 20th",
        "booking_mode": "reservation",
        "aliases": {"presidential room": "room"},
        "expected": {
            "intent": "CREATE_RESERVATION",
            "facts": {
                "service_id": "presidential room",
                "dates": ["2026-12-15", "2026-12-20"],
            },
        },
    },
    {
        "sentence": "book me a presdential room november 1st through november 5th",
        "booking_mode": "reservation",
        "aliases": {"presidential room": "room"},
        "expected": {
            "intent": "CREATE_RESERVATION",
            "facts": {
                "service_id": "presidential room",
                "dates": ["2026-11-01", "2026-11-05"],
            },
        },
    },
    {
        "sentence": "i want to make an apointment for a haircut this tuesday at 2pm",
        "booking_mode": "service",
        "aliases": {"haircut": "haircut"},
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "facts": {"service_id": "haircut", "dates": ["2026-01-20"]},
            "time_constraint": {
                "mode": "exact",
                "start": "14:00",
                "end": "14:00",
                "label": None,
            },
        },
    },
    {
        "sentence": "can i get a suite from jan 10 to jan 15 please",
        "booking_mode": "reservation",
        "aliases": {"suite": "room"},
        "expected": {
            "intent": "CREATE_RESERVATION",
            "facts": {"service_id": "suite", "dates": ["2026-01-10", "2026-01-15"]},
        },
    },
    # ────────────────
    # MODIFY_BOOKING — Extraction of booking_id and temporal facts
    # ────────────────
    {
        "sentence": "reschedule my booking ABC123 to tomorrow at 3pm",
        "booking_mode": "service",
        "aliases": {"haircut": "haircut"},
        "expected": {
            "intent": "MODIFY_BOOKING",
            "facts": {
                "booking_id": "ABC123",
                "dates": ["2026-01-14"],
                "times": ["15:00"],
            },
        },
    },
    {
        "sentence": "change my appointment XYZ789 to next friday at 10am",
        "booking_mode": "service",
        "aliases": {"massage": "massage"},
        "expected": {
            "intent": "MODIFY_BOOKING",
            "facts": {
                "booking_id": "XYZ789",
                "dates": ["2026-01-23"],
                "times": ["10:00"],
            },
        },
    },
    {
        "sentence": "move my reservation DEF456 from oct 5th to oct 10th",
        "booking_mode": "reservation",
        "aliases": {"suite": "room"},
        "expected": {
            "intent": "MODIFY_BOOKING",
            "facts": {"booking_id": "DEF456", "dates": ["2026-10-05", "2026-10-10"]},
        },
    },
    {
        "sentence": "postpone my booking JKL012 to next monday at 2pm",
        "booking_mode": "service",
        "aliases": {"haircut": "haircut"},
        "expected": {
            "intent": "MODIFY_BOOKING",
            "facts": {
                "booking_id": "JKL012",
                "dates": ["2026-01-19"],
                "times": ["14:00"],
            },
        },
    },
    {
        "sentence": "modify reservation MNO345 to november 1st through november 5th",
        "booking_mode": "reservation",
        "aliases": {"standard": "room"},
        "expected": {
            "intent": "MODIFY_BOOKING",
            "facts": {"booking_id": "MNO345", "dates": ["2026-11-01", "2026-11-05"]},
        },
    },
    {
        "sentence": "change the time for booking PQR678 to 4pm",
        "booking_mode": "service",
        "aliases": {"massage": "massage"},
        "expected": {
            "intent": "MODIFY_BOOKING",
            "facts": {"booking_id": "PQR678", "times": ["16:00"]},
        },
    },
    {
        "sentence": "reschedule appointment STU901 to this friday evening",
        "booking_mode": "service",
        "aliases": {"haircut": "haircut"},
        "expected": {
            "intent": "MODIFY_BOOKING",
            "facts": {"booking_id": "STU901", "dates": ["2026-01-16"]},
            "time_constraint": {
                "mode": "fuzzy",
                "label": "evening",
                "start": "17:00",
                "end": "21:59",
            },
        },
    },
    {
        "sentence": "change booking YZA567 time to tomorrow at noon",
        "booking_mode": "service",
        "aliases": {"beard": "beard grooming"},
        "expected": {
            "intent": "MODIFY_BOOKING",
            "facts": {
                "booking_id": "YZA567",
                "dates": ["2026-01-14"],
                "times": ["12:00"],
            },
        },
    },
    {
        "sentence": "change apointment OPQ789 to next friday",
        "booking_mode": "service",
        "aliases": {"massage": "massage"},
        "expected": {
            "intent": "MODIFY_BOOKING",
            "facts": {"booking_id": "OPQ789", "dates": ["2026-01-23"]},
        },
    },
    {
        "sentence": "modify reservtion RST012 to dec 20 to dec 25",
        "booking_mode": "reservation",
        "aliases": {"suite": "room"},
        "expected": {
            "intent": "MODIFY_BOOKING",
            "facts": {"booking_id": "RST012", "dates": ["2026-12-20", "2026-12-25"]},
        },
    },
    {
        "sentence": "change my reservation ABC123 to feb 9 to feb 11",
        "booking_mode": "reservation",
        "aliases": {},
        "expected": {
            "intent": "MODIFY_BOOKING",
            "facts": {"booking_id": "ABC123", "dates": ["2026-02-09", "2026-02-11"]},
        },
    },
    {
        "sentence": "change my reservation ABC123 to feb 9",
        "booking_mode": "reservation",
        "aliases": {},
        "expected": {
            "intent": "MODIFY_BOOKING",
            "facts": {"booking_id": "ABC123", "dates": ["2026-02-09"]},
        },
    },
    {
        "sentence": "move my booking ABC123 to 3pm",
        "booking_mode": "service",
        "aliases": {},
        "expected": {
            "intent": "MODIFY_BOOKING",
            "facts": {"booking_id": "ABC123", "times": ["15:00"]},
        },
    },
    {
        "sentence": "change my booking to 3pm",
        "booking_mode": "service",
        "aliases": {},
        "expected": {"intent": "MODIFY_BOOKING", "facts": {"times": ["15:00"]}},
    },
    # ────────────────
    # CANCEL_BOOKING — Extraction of booking_id
    # ────────────────
    {
        "sentence": "cancel my booking BCD890",
        "booking_mode": "service",
        "aliases": {},
        "expected": {"intent": "CANCEL_BOOKING", "facts": {"booking_id": "BCD890"}},
    },
    {
        "sentence": "delete my reservation HIJ456",
        "booking_mode": "reservation",
        "aliases": {},
        "expected": {"intent": "CANCEL_BOOKING", "facts": {"booking_id": "HIJ456"}},
    },
    {
        "sentence": "i need to cancel booking NOP012",
        "booking_mode": "service",
        "aliases": {},
        "expected": {"intent": "CANCEL_BOOKING", "facts": {"booking_id": "NOP012"}},
    },
    {
        "sentence": "can't make it, cancel booking CDE567",
        "booking_mode": "service",
        "aliases": {},
        "expected": {"intent": "CANCEL_BOOKING", "facts": {"booking_id": "CDE567"}},
    },
    # ────────────────
    # UNKNOWN / FRAGMENT INPUTS — Extraction only, no semantics
    # ────────────────
    {
        "sentence": "feb 12th",
        "booking_mode": "service",
        "aliases": {},
        "expected": {"intent": "UNKNOWN", "facts": {"dates": ["2026-02-12"]}},
    },
    {
        "sentence": "3pm",
        "booking_mode": "service",
        "aliases": {},
        "expected": {"intent": "UNKNOWN", "facts": {"times": ["15:00"]}},
    },
    {
        "sentence": "feb 12th at 3pm",
        "booking_mode": "service",
        "aliases": {},
        "expected": {
            "intent": "UNKNOWN",
            "facts": {"dates": ["2026-02-12"], "times": ["15:00"]},
        },
    },
    {
        "sentence": "from april 12th to april 16th",
        "booking_mode": "reservation",
        "aliases": {},
        "expected": {
            "intent": "UNKNOWN",
            "facts": {"dates": ["2026-04-12", "2026-04-16"]},
        },
    },
    {
        "sentence": "deluxe room",
        "booking_mode": "reservation",
        "aliases": {"deluxe room": "room"},
        "expected": {"intent": "UNKNOWN", "facts": {"service_id": "deluxe room"}},
    },
    {
        "sentence": "haircut tomorrow",
        "booking_mode": "service",
        "aliases": {"haircut": "haircut"},
        "expected": {
            "intent": "UNKNOWN",
            "facts": {"service_id": "haircut", "dates": ["2026-01-14"]},
        },
    },
    # ────────────────
    # FOLLOW-UP DATE INPUTS — Weekday phrases (fragment extraction)
    # ────────────────
    {
        "sentence": "friday",
        "booking_mode": "service",
        "aliases": {},
        "expected": {"intent": "UNKNOWN", "facts": {"dates": ["2026-01-16"]}},
    },
    {
        "sentence": "this friday",
        "booking_mode": "service",
        "aliases": {},
        "expected": {"intent": "UNKNOWN", "facts": {"dates": ["2026-01-16"]}},
    },
    {
        "sentence": "next friday",
        "booking_mode": "service",
        "aliases": {},
        "expected": {"intent": "UNKNOWN", "facts": {"dates": ["2026-01-23"]}},
    },
    {
        "sentence": "monday",
        "booking_mode": "service",
        "aliases": {},
        "expected": {"intent": "UNKNOWN", "facts": {"dates": ["2026-01-19"]}},
    },
    {
        "sentence": "tuesday",
        "booking_mode": "service",
        "aliases": {},
        "expected": {"intent": "UNKNOWN", "facts": {"dates": ["2026-01-20"]}},
    },
    {
        "sentence": "wednesday",
        "booking_mode": "service",
        "aliases": {},
        "expected": {"intent": "UNKNOWN", "facts": {"dates": ["2026-01-14"]}},
    },
    {
        "sentence": "saturday",
        "booking_mode": "service",
        "aliases": {},
        "expected": {"intent": "UNKNOWN", "facts": {"dates": ["2026-01-17"]}},
    },
    {
        "sentence": "next week",
        "booking_mode": "service",
        "aliases": {},
        "expected": {
            "intent": "UNKNOWN",
            "facts": {"dates": ["2026-01-19", "2026-01-25"]},
        },
    },
    {
        "sentence": "this weekend",
        "booking_mode": "service",
        "aliases": {},
        "expected": {
            "intent": "UNKNOWN",
            "facts": {"dates": ["2026-01-17", "2026-01-18"]},
        },
    },
    # ────────────────
    # DATE FORMAT VARIANTS — One test per distinct parsing path
    # ────────────────
    {
        "sentence": "3rd mar by 3pm",
        "booking_mode": "service",
        "aliases": {},
        "expected": {
            "intent": "UNKNOWN",
            "facts": {"dates": ["2026-03-03"], "times": ["15:00"]},
        },
    },
    {
        "sentence": "march 3",
        "booking_mode": "service",
        "aliases": {},
        "expected": {"intent": "UNKNOWN", "facts": {"dates": ["2026-03-03"]}},
    },
    {
        "sentence": "MAR 3",
        "booking_mode": "service",
        "aliases": {},
        "expected": {"intent": "UNKNOWN", "facts": {"dates": ["2026-03-03"]}},
    },
    {
        "sentence": "mar 2nd",
        "booking_mode": "service",
        "aliases": {},
        "expected": {"intent": "UNKNOWN", "facts": {"dates": ["2026-03-02"]}},
    },
    {
        "sentence": "04/03",
        "booking_mode": "service",
        "aliases": {},
        "expected": {"intent": "UNKNOWN", "facts": {"dates": ["2026-03-04"]}},
    },
    {
        "sentence": "3:00 PM",
        "booking_mode": "service",
        "aliases": {},
        "expected": {"intent": "UNKNOWN", "facts": {"times": ["15:00"]}},
    },
    {
        "sentence": "15:00",
        "booking_mode": "service",
        "aliases": {},
        "expected": {"intent": "UNKNOWN", "facts": {"times": ["15:00"]}},
    },
    {
        "sentence": "at 3pm",
        "booking_mode": "service",
        "aliases": {},
        "expected": {"intent": "UNKNOWN", "facts": {"times": ["15:00"]}},
    },
    {
        "sentence": "3-Mar",
        "booking_mode": "service",
        "aliases": {},
        "expected": {"intent": "UNKNOWN", "facts": {"dates": ["2026-03-03"]}},
    },
    {
        "sentence": "March 3, 2026",
        "booking_mode": "service",
        "aliases": {},
        "expected": {"intent": "UNKNOWN", "facts": {"dates": ["2026-03-03"]}},
    },
    {
        "sentence": "March 3rd at 3pm",
        "booking_mode": "service",
        "aliases": {},
        "expected": {
            "intent": "UNKNOWN",
            "facts": {"dates": ["2026-03-03"], "times": ["15:00"]},
        },
    },
    # ────────────────
    # UNKNOWN / FRAGMENT INPUTS — Date range fragments
    # ────────────────
    {
        "sentence": "Mar 5 to 8",
        "booking_mode": "reservation",
        "aliases": {},
        "expected": {
            "intent": "UNKNOWN",
            "facts": {"dates": ["2026-03-05", "2026-03-08"]},
        },
    },
    {
        "sentence": "March 5 to March 8",
        "booking_mode": "reservation",
        "aliases": {},
        "expected": {
            "intent": "UNKNOWN",
            "facts": {"dates": ["2026-03-05", "2026-03-08"]},
        },
    },
    {
        "sentence": "05/03 to 08/03",
        "booking_mode": "reservation",
        "aliases": {},
        "expected": {
            "intent": "UNKNOWN",
            "facts": {"dates": ["2026-03-05", "2026-03-08"]},
        },
    },
    {
        "sentence": "Mar 5 through 8",
        "booking_mode": "reservation",
        "aliases": {},
        "expected": {
            "intent": "UNKNOWN",
            "facts": {"dates": ["2026-03-05", "2026-03-08"]},
        },
    },
    {
        "sentence": "from Mar 5th through Mar 8th",
        "booking_mode": "reservation",
        "aliases": {},
        "expected": {
            "intent": "UNKNOWN",
            "facts": {"dates": ["2026-03-05", "2026-03-08"]},
        },
    },
    # ────────────────
    # DATE-TIME PAIRING — Explicit vs ambiguous
    # ────────────────
    {
        "sentence": "on March 3rd at 3pm",
        "booking_mode": "service",
        "aliases": {},
        "expected": {
            "intent": "UNKNOWN",
            "facts": {"dates": ["2026-03-03"], "times": ["15:00"]},
        },
    },
    {
        "sentence": "tomorrow at 3pm",
        "booking_mode": "service",
        "aliases": {},
        "expected": {
            "intent": "UNKNOWN",
            "facts": {"dates": ["2026-01-14"], "times": ["15:00"]},
        },
    },
    {
        "sentence": "March 3rd and April 8th at 14:00",
        "booking_mode": "service",
        "aliases": {},
        "expected": {
            "intent": "UNKNOWN",
            "facts": {"dates": ["2026-03-03", "2026-04-08"], "times": ["14:00"]},
        },
    },
    {
        "sentence": "April 3 at 14:00 and April 8 at 15:00",
        "booking_mode": "service",
        "aliases": {},
        "expected": {
            "intent": "UNKNOWN",
            "facts": {
                "dates": ["2026-04-03", "2026-04-08"],
                "times": ["14:00", "15:00"],
            },
        },
    },
    {
        "sentence": "book haircut friday evening",
        "booking_mode": "service",
        "aliases": {"haircut": "haircut"},
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "facts": {
                "service_id": "haircut",
                "dates": ["2026-01-16"],
            },
            "time_constraint": {
                "mode": "fuzzy",
                "label": "evening",
                "start": "17:00",
                "end": "21:59",
            },
        },
    },
    # ────────────────
    # ALIAS MATCHING GAPS — Cases where model must return exact alias key
    # ────────────────
    {
        # Gap: alias key contains a space ("hair cut"), user omits it ("haircut").
        # Haiku must return the exact key "hair cut", not the user's spelling.
        # If it returns "haircut" the orchestrator's exact-dict lookup silently fails
        # and canonical service_id is never set.
        "sentence": "book a haircut tomorrow at 3pm",
        "booking_mode": "service",
        "aliases": {"hair cut": "beauty.haircut"},
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "facts": {
                "service_id": "hair cut",
                "dates": ["2026-01-14"],
                "times": ["15:00"],
            },
        },
    },
    {
        # Gap: user input is ambiguous across multiple alias keys.
        # "massage" matches both "swedish massage" and "deep tissue massage".
        # Expected: service_id=null so planning asks which one.
        # If Haiku silently picks one, a wrong service gets booked without clarification.
        "sentence": "book a massage tomorrow at 2pm",
        "booking_mode": "service",
        "aliases": {
            "swedish massage": "wellness.swedish_massage",
            "deep tissue massage": "wellness.deep_tissue",
        },
        "expected": {
            "intent": "CREATE_APPOINTMENT",
            "facts": {
                "service_id": None,
                "dates": ["2026-01-14"],
                "times": ["14:00"],
            },
        },
    },
    # ────────────────
    # GENERAL_INQUIRY — FAQ / off-script questions routed to RAG
    # ────────────────
    {
        # 83 — return policies: no booking verb, pure informational
        "sentence": "what are your return policies",
        "booking_mode": "service",
        "expected": {
            "intent": "GENERAL_INQUIRY",
            "facts": {
                "dates": [],
                "times": [],
                "date_time_pairs": [],
                "service_id": None,
                "booking_id": None,
            },
            "search_query": "return policies",
        },
    },
    {
        # 84 — location/logistics question
        "sentence": "do you have parking",
        "booking_mode": "service",
        "expected": {
            "intent": "GENERAL_INQUIRY",
            "facts": {
                "dates": [],
                "times": [],
                "date_time_pairs": [],
                "service_id": None,
                "booking_id": None,
            },
            "search_query": "parking",
        },
    },
    {
        # 85 — operating hours
        "sentence": "what are your opening hours",
        "booking_mode": "service",
        "expected": {
            "intent": "GENERAL_INQUIRY",
            "facts": {
                "dates": [],
                "times": [],
                "date_time_pairs": [],
                "service_id": None,
                "booking_id": None,
            },
            "search_query": "opening hours",
        },
    },
    # ────────────────
    # search_query — RAG normalisation for other informational intents
    # ────────────────
    {
        # 86 — DETAILS: ask about a specific service; search_query strips filler
        "sentence": "tell me about deep tissue massage",
        "booking_mode": "service",
        "aliases": {
            "deep tissue massage": "wellness.deep_tissue",
            "swedish massage": "wellness.swedish",
        },
        "expected": {
            "intent": "DETAILS",
            "facts": {
                "dates": [],
                "times": [],
                "date_time_pairs": [],
                "service_id": "deep tissue massage",
                "booking_id": None,
            },
            "search_query": "deep tissue massage",
        },
    },
    {
        # 87 — DISCOVERY: catalog browse; search_query captures the user's goal
        "sentence": "what services do you offer",
        "booking_mode": "service",
        "expected": {
            "intent": "DISCOVERY",
            "facts": {
                "dates": [],
                "times": [],
                "date_time_pairs": [],
                "service_id": None,
                "booking_id": None,
            },
            "search_query": "available services",
        },
    },
    {
        # 88 — QUOTE: pricing question; search_query is the item being priced
        "sentence": "how much does a haircut cost",
        "booking_mode": "service",
        "aliases": {"hair cut": "beauty.haircut"},
        "expected": {
            "intent": "QUOTE",
            "facts": {
                "dates": [],
                "times": [],
                "date_time_pairs": [],
                "service_id": "hair cut",
                "booking_id": None,
            },
            "search_query": "haircut price",
        },
    },
]
