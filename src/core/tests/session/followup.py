"""
Core Session Follow-up Scenarios

Multi-turn conversation scenarios to test Redis-backed session behavior.
Each scenario tests session persistence, merge, and cleanup across turns.

81 scenarios covering:
- service → date → time follow-ups
- service → time → date follow-ups
- reservation check-in → check-out completion
- modify booking (time, date, range)
- ambiguous follow-ups ("tomorrow", "evening", "next friday")
- UNKNOWN → becomes booking via follow-up
- follow-up that switches intent (should reset session)
- negative guardrail tests for awaiting_slot routing (IDs 51-55)
- negative safety-net tests for slot inference prevention (IDs 56-75)
- reservation contract enforcement tests (IDs 76-81)
"""

followup_scenarios = [
    # Service appointment scenarios: service → date → time (IDs 1-10)
    {
        "id": 1,
        "name": "service_to_date_to_time",
        "domain": "service",
        "aliases": {"haircut": "haircut"},
        "turns": [
            {"sentence": "book a haircut", "expected": {"intent": "CREATE_APPOINTMENT",
                                                        "status": "NEEDS_CLARIFICATION", "missing_slots": ["date", "time"]}},
            {"sentence": "tomorrow", "expected": {
                "status": "NEEDS_CLARIFICATION", "missing_slots": ["time"]}},
            {"sentence": "11am", "expected": {
                "status": "READY", "slots": {"has_datetime": True}}}
        ]
    },
    {
        "id": 2,
        "name": "service_to_date_to_time_massage",
        "domain": "service",
        "aliases": {"massage": "massage"},
        "turns": [
            {"sentence": "book massage", "expected": {"intent": "CREATE_APPOINTMENT",
                                                      "status": "NEEDS_CLARIFICATION", "missing_slots": ["date", "time"]}},
            {"sentence": "this friday", "expected": {
                "status": "NEEDS_CLARIFICATION", "missing_slots": ["time"]}},
            {"sentence": "3pm", "expected": {
                "status": "READY", "slots": {"has_datetime": True}}}
        ]
    },
    {
        "id": 3,
        "name": "service_to_date_to_time_facial",
        "domain": "service",
        "aliases": {"facial": "facial"},
        "turns": [
            {"sentence": "schedule facial", "expected": {"intent": "CREATE_APPOINTMENT",
                                                         "status": "NEEDS_CLARIFICATION", "missing_slots": ["date", "time"]}},
            {"sentence": "next monday", "expected": {
                "status": "NEEDS_CLARIFICATION", "missing_slots": ["time"]}},
            {"sentence": "10am", "expected": {
                "status": "READY", "slots": {"has_datetime": True}}}
        ]
    },
    {
        "id": 4,
        "name": "service_to_date_to_time_waxing",
        "domain": "service",
        "aliases": {"waxing": "waxing"},
        "turns": [
            {"sentence": "book waxing", "expected": {"intent": "CREATE_APPOINTMENT",
                                                     "status": "NEEDS_CLARIFICATION", "missing_slots": ["date", "time"]}},
            {"sentence": "saturday", "expected": {
                "status": "NEEDS_CLARIFICATION", "missing_slots": ["time"]}},
            {"sentence": "2pm", "expected": {
                "status": "READY", "slots": {"has_datetime": True}}}
        ]
    },
    {
        "id": 5,
        "name": "service_to_date_to_time_manicure",
        "domain": "service",
        "aliases": {"manicure": "manicure"},
        "turns": [
            {"sentence": "i need a manicure", "expected": {"intent": "CREATE_APPOINTMENT",
                                                           "status": "NEEDS_CLARIFICATION", "missing_slots": ["date", "time"]}},
            {"sentence": "next week", "expected": {
                "status": "NEEDS_CLARIFICATION", "missing_slots": ["time"]}},
            {"sentence": "4pm", "expected": {
                "status": "READY", "slots": {"has_datetime": True}}}
        ]
    },
    {
        "id": 6,
        "name": "service_to_date_to_time_pedicure",
        "domain": "service",
        "aliases": {"pedicure": "pedicure"},
        "turns": [
            {"sentence": "book pedicure", "expected": {"intent": "CREATE_APPOINTMENT",
                                                       "status": "NEEDS_CLARIFICATION", "missing_slots": ["date", "time"]}},
            {"sentence": "this weekend", "expected": {
                "status": "NEEDS_CLARIFICATION", "missing_slots": ["time"]}},
            {"sentence": "11am", "expected": {
                "status": "READY", "slots": {"has_datetime": True}}}
        ]
    },
    {
        "id": 7,
        "name": "service_to_date_to_time_eyebrow",
        "domain": "service",
        "aliases": {"eyebrow": "eyebrow"},
        "turns": [
            {"sentence": "schedule eyebrow", "expected": {"intent": "CREATE_APPOINTMENT",
                                                          "status": "NEEDS_CLARIFICATION", "missing_slots": ["date", "time"]}},
            {"sentence": "wednesday", "expected": {
                "status": "NEEDS_CLARIFICATION", "missing_slots": ["time"]}},
            {"sentence": "1pm", "expected": {
                "status": "READY", "slots": {"has_datetime": True}}}
        ]
    },
    {
        "id": 8,
        "name": "service_to_date_to_time_coloring",
        "domain": "service",
        "aliases": {"coloring": "coloring"},
        "turns": [
            {"sentence": "book coloring", "expected": {"intent": "CREATE_APPOINTMENT",
                                                       "status": "NEEDS_CLARIFICATION", "missing_slots": ["date", "time"]}},
            {"sentence": "next thursday", "expected": {
                "status": "NEEDS_CLARIFICATION", "missing_slots": ["time"]}},
            {"sentence": "9am", "expected": {
                "status": "READY", "slots": {"has_datetime": True}}}
        ]
    },
    {
        "id": 9,
        "name": "service_to_date_to_time_highlight",
        "domain": "service",
        "aliases": {"highlight": "highlight"},
        "turns": [
            {"sentence": "i want highlights", "expected": {"intent": "CREATE_APPOINTMENT",
                                                           "status": "NEEDS_CLARIFICATION", "missing_slots": ["date", "time"]}},
            {"sentence": "next friday", "expected": {
                "status": "NEEDS_CLARIFICATION", "missing_slots": ["time"]}},
            # Luma does NOT extract time from "noon" into slots["time"]
            # Even with awaiting_slot routing, if time_constraint is not present, status remains NEEDS_CLARIFICATION
            {"sentence": "noon", "expected": {
                "status": "NEEDS_CLARIFICATION", "missing_slots": ["time"]}}
        ]
    },
    {
        "id": 10,
        "name": "service_to_date_to_time_cut",
        "domain": "service",
        "aliases": {"cut": "cut"},
        "turns": [
            {"sentence": "book a cut", "expected": {"intent": "CREATE_APPOINTMENT",
                                                    "status": "NEEDS_CLARIFICATION", "missing_slots": ["date", "time"]}},
            {"sentence": "tuesday", "expected": {
                "status": "NEEDS_CLARIFICATION", "missing_slots": ["time"]}},
            {"sentence": "5pm", "expected": {
                "status": "READY", "slots": {"has_datetime": True}}}
        ]
    },
    # Service appointment scenarios: service → time → date (IDs 11-20)
    {
        "id": 11,
        "name": "service_to_time_to_date",
        "domain": "service",
        "aliases": {"massage": "massage"},
        "turns": [
            {"sentence": "book massage at 2pm", "expected": {"intent": "CREATE_APPOINTMENT",
                                                             "status": "NEEDS_CLARIFICATION", "missing_slots": ["date"]}},
            {"sentence": "tomorrow", "expected": {
                "status": "READY", "slots": {"has_datetime": True}}}
        ]
    },
    {
        "id": 12,
        "name": "service_to_time_to_date_haircut",
        "domain": "service",
        "aliases": {"haircut": "haircut"},
        "turns": [
            {"sentence": "book haircut at 10am", "expected": {"intent": "CREATE_APPOINTMENT",
                                                              "status": "NEEDS_CLARIFICATION", "missing_slots": ["date"]}},
            {"sentence": "friday", "expected": {
                "status": "READY", "slots": {"has_datetime": True}}}
        ]
    },
    {
        "id": 13,
        "name": "service_to_time_to_date_facial",
        "domain": "service",
        "aliases": {"facial": "facial"},
        "turns": [
            {"sentence": "schedule facial for 3pm", "expected": {
                "intent": "CREATE_APPOINTMENT", "status": "NEEDS_CLARIFICATION", "missing_slots": ["date"]}},
            {"sentence": "next monday", "expected": {
                "status": "READY", "slots": {"has_datetime": True}}}
        ]
    },
    {
        "id": 14,
        "name": "service_to_time_to_date_morning",
        "domain": "service",
        "aliases": {"massage": "massage"},
        "turns": [
            {"sentence": "book massage at 9am", "expected": {"intent": "CREATE_APPOINTMENT",
                                                             "status": "NEEDS_CLARIFICATION", "missing_slots": ["date"]}},
            {"sentence": "saturday", "expected": {
                "status": "READY", "slots": {"has_datetime": True}}}
        ]
    },
    {
        "id": 15,
        "name": "service_to_time_to_date_evening",
        "domain": "service",
        "aliases": {"haircut": "haircut"},
        "turns": [
            {"sentence": "book haircut at 6pm", "expected": {"intent": "CREATE_APPOINTMENT",
                                                             "status": "NEEDS_CLARIFICATION", "missing_slots": ["date"]}},
            {"sentence": "tuesday", "expected": {
                "status": "READY", "slots": {"has_datetime": True}}}
        ]
    },
    {
        "id": 16,
        "name": "service_to_time_to_date_noon",
        "domain": "service",
        "aliases": {"manicure": "manicure"},
        "turns": [
            {"sentence": "book manicure at noon", "expected": {"intent": "CREATE_APPOINTMENT",
                                                               "status": "NEEDS_CLARIFICATION", "missing_slots": ["date"]}},
            {"sentence": "next week", "expected": {
                "status": "READY", "slots": {"has_datetime": True}}}
        ]
    },
    {
        "id": 17,
        "name": "service_to_time_to_date_afternoon",
        "domain": "service",
        "aliases": {"pedicure": "pedicure"},
        "turns": [
            {"sentence": "book pedicure at 4pm", "expected": {"intent": "CREATE_APPOINTMENT",
                                                              "status": "NEEDS_CLARIFICATION", "missing_slots": ["date"]}},
            {"sentence": "wednesday", "expected": {
                "status": "READY", "slots": {"has_datetime": True}}}
        ]
    },
    {
        "id": 18,
        "name": "service_to_time_to_date_midday",
        "domain": "service",
        "aliases": {"waxing": "waxing"},
        "turns": [
            {"sentence": "schedule waxing at 1pm", "expected": {
                "intent": "CREATE_APPOINTMENT", "status": "NEEDS_CLARIFICATION", "missing_slots": ["date"]}},
            {"sentence": "thursday", "expected": {
                "status": "READY", "slots": {"has_datetime": True}}}
        ]
    },
    {
        "id": 19,
        "name": "service_to_time_to_date_early",
        "domain": "service",
        "aliases": {"facial": "facial"},
        "turns": [
            {"sentence": "book facial at 8am", "expected": {"intent": "CREATE_APPOINTMENT",
                                                            "status": "NEEDS_CLARIFICATION", "missing_slots": ["date"]}},
            {"sentence": "next friday", "expected": {
                "status": "READY", "slots": {"has_datetime": True}}}
        ]
    },
    {
        "id": 20,
        "name": "service_to_time_to_date_late",
        "domain": "service",
        "aliases": {"massage": "massage"},
        "turns": [
            {"sentence": "book massage at 7pm", "expected": {"intent": "CREATE_APPOINTMENT",
                                                             "status": "NEEDS_CLARIFICATION", "missing_slots": ["date"]}},
            {"sentence": "this weekend", "expected": {
                "status": "READY", "slots": {"has_datetime": True}}}
        ]
    },
    # Reservation scenarios: check-in → check-out (IDs 21-30)
    # NOTE: Core is deterministic - it only checks if required slots are explicitly present.
    # Core does NOT infer slot roles from ordering. A second bare `date` does NOT resolve a reservation.
    # Core remains in NEEDS_CLARIFICATION if `end_date` is not explicitly present.
    # has_datetime is NOT for reservations (only for service appointments with time).
    {
        "id": 21,
        "name": "reservation_checkin_to_checkout",
        "domain": "reservation",
        "aliases": {"room": "room"},
        "turns": [
            {"sentence": "reserve a room", "expected": {"intent": "CREATE_RESERVATION",
                                                        "status": "NEEDS_CLARIFICATION", "missing_slots": ["start_date", "end_date"]}},
            {"sentence": "from october 5th", "expected": {
                "status": "NEEDS_CLARIFICATION", "missing_slots": ["end_date"]}},
            # If Luma emits only `date` (not `end_date`), Core stays NEEDS_CLARIFICATION.
            # Core does NOT infer that a second `date` means `end_date`.
            {"sentence": "to october 9th", "expected": {
                "status": "NEEDS_CLARIFICATION", "missing_slots": ["end_date"]}}
        ]
    },
    {
        "id": 22,
        "name": "reservation_suite_checkin_checkout",
        "domain": "reservation",
        "aliases": {"suite": "room"},
        "turns": [
            {"sentence": "book suite", "expected": {"intent": "CREATE_RESERVATION",
                                                    "status": "NEEDS_CLARIFICATION", "missing_slots": ["start_date", "end_date"]}},
            {"sentence": "from nov 1st", "expected": {
                "status": "NEEDS_CLARIFICATION", "missing_slots": ["end_date"]}},
            # Core does NOT infer that a second `date` means `end_date`.
            {"sentence": "to nov 5th", "expected": {
                "status": "NEEDS_CLARIFICATION", "missing_slots": ["end_date"]}}
        ]
    },
    {
        "id": 23,
        "name": "reservation_deluxe_checkin_checkout",
        "domain": "reservation",
        "aliases": {"deluxe": "room"},
        "turns": [
            {"sentence": "reserve deluxe room", "expected": {"intent": "CREATE_RESERVATION",
                                                             "status": "NEEDS_CLARIFICATION", "missing_slots": ["start_date", "end_date"]}},
            {"sentence": "from dec 10th", "expected": {
                "status": "NEEDS_CLARIFICATION", "missing_slots": ["end_date"]}},
            # Core does NOT infer that a second `date` means `end_date`.
            {"sentence": "through dec 15th", "expected": {
                "status": "NEEDS_CLARIFICATION", "missing_slots": ["end_date"]}}
        ]
    },
    {
        "id": 24,
        "name": "reservation_standard_checkin_checkout",
        "domain": "reservation",
        "aliases": {"standard": "room"},
        "turns": [
            {"sentence": "book standard room", "expected": {"intent": "CREATE_RESERVATION",
                                                            "status": "NEEDS_CLARIFICATION", "missing_slots": ["start_date", "end_date"]}},
            {"sentence": "from jan 5th", "expected": {
                "status": "NEEDS_CLARIFICATION", "missing_slots": ["end_date"]}},
            # Core does NOT infer that a second `date` means `end_date`.
            {"sentence": "until jan 8th", "expected": {
                "status": "NEEDS_CLARIFICATION", "missing_slots": ["end_date"]}}
        ]
    },
    {
        "id": 25,
        "name": "reservation_penthouse_checkin_checkout",
        "domain": "reservation",
        "aliases": {"penthouse": "room"},
        "turns": [
            {"sentence": "reserve penthouse", "expected": {"intent": "CREATE_RESERVATION",
                                                           "status": "NEEDS_CLARIFICATION", "missing_slots": ["start_date", "end_date"]}},
            {"sentence": "from feb 1st", "expected": {
                "status": "NEEDS_CLARIFICATION", "missing_slots": ["end_date"]}},
            # Core does NOT infer that a second `date` means `end_date`.
            {"sentence": "to feb 5th", "expected": {
                "status": "NEEDS_CLARIFICATION", "missing_slots": ["end_date"]}}
        ]
    },
    {
        "id": 26,
        "name": "reservation_range_followup",
        "domain": "reservation",
        "aliases": {"room": "room"},
        "turns": [
            {"sentence": "book room", "expected": {"intent": "CREATE_RESERVATION",
                                                   "status": "NEEDS_CLARIFICATION", "missing_slots": ["start_date", "end_date"]}},
            # When Luma emits date_range with start and end, Core sees both slots satisfied → READY
            {"sentence": "march 10 to 15", "expected": {
                "status": "READY"}}
        ]
    },
    {
        "id": 27,
        "name": "reservation_suite_range",
        "domain": "reservation",
        "aliases": {"suite": "room"},
        "turns": [
            {"sentence": "book suite", "expected": {"intent": "CREATE_RESERVATION",
                                                    "status": "NEEDS_CLARIFICATION", "missing_slots": ["start_date", "end_date"]}},
            {"sentence": "april 1st to 3rd", "expected": {
                "status": "READY"}}
        ]
    },
    {
        "id": 28,
        "name": "reservation_deluxe_range",
        "domain": "reservation",
        "aliases": {"deluxe": "room"},
        "turns": [
            {"sentence": "reserve deluxe", "expected": {"intent": "CREATE_RESERVATION",
                                                        "status": "NEEDS_CLARIFICATION", "missing_slots": ["start_date", "end_date"]}},
            {"sentence": "may 20 to 25", "expected": {
                "status": "READY"}}
        ]
    },
    {
        "id": 29,
        "name": "reservation_standard_range",
        "domain": "reservation",
        "aliases": {"standard": "room"},
        "turns": [
            {"sentence": "book standard", "expected": {"intent": "CREATE_RESERVATION",
                                                       "status": "NEEDS_CLARIFICATION", "missing_slots": ["start_date", "end_date"]}},
            {"sentence": "june 1 to 5", "expected": {
                "status": "READY"}}
        ]
    },
    {
        "id": 30,
        "name": "reservation_multi_turn_range",
        "domain": "reservation",
        "aliases": {"room": "room"},
        "turns": [
            {"sentence": "reserve room", "expected": {"intent": "CREATE_RESERVATION",
                                                      "status": "NEEDS_CLARIFICATION", "missing_slots": ["start_date", "end_date"]}},
            {"sentence": "from july 1", "expected": {
                "status": "NEEDS_CLARIFICATION", "missing_slots": ["end_date"]}},
            # Core does NOT infer that a second `date` means `end_date`.
            {"sentence": "to july 7", "expected": {
                "status": "NEEDS_CLARIFICATION", "missing_slots": ["end_date"]}}
        ]
    },
    # Modify booking scenarios (IDs 31-40)
    # NOTE: Core treats MODIFY_BOOKING as potentially affecting the whole booking.
    # Core requires booking_id + all temporal fields relevant to the domain, unless explicitly provided.
    # Service domain: booking_id + date + time
    # Reservation domain: booking_id + start_date + end_date
    # Do NOT use synthetic "change" slot - use actual dimension slots (date, time, start_date, end_date).
    {
        "id": 31,
        "name": "modify_booking_time_only",
        "domain": "service",
        "aliases": {"haircut": "haircut"},
        "turns": [
            # Time is provided (3pm), but booking_id and date are missing
            # Core requires booking_id + date + time for service domain MODIFY_BOOKING
            {"sentence": "change my booking to 3pm", "expected": {"intent": "MODIFY_BOOKING",
                                                                  "status": "NEEDS_CLARIFICATION", "missing_slots": ["booking_id", "date"]}},
            # Luma does NOT extract booking_id from "booking abc123" (slots={})
            # Core does NOT receive booking_id, so status remains NEEDS_CLARIFICATION
            {"sentence": "booking abc123", "expected": {
                "status": "NEEDS_CLARIFICATION", "missing_slots": ["booking_id"]}}
        ]
    },
    {
        "id": 32,
        "name": "modify_booking_date_only",
        "domain": "service",
        "aliases": {"massage": "massage"},
        "turns": [
            # Date is provided (friday), but booking_id and time are missing
            # Core requires booking_id + date + time for service domain MODIFY_BOOKING
            {"sentence": "change booking to friday", "expected": {"intent": "MODIFY_BOOKING",
                                                                  "status": "NEEDS_CLARIFICATION", "missing_slots": ["booking_id", "time"]}},
            # Luma does NOT extract booking_id from "booking xyz789" (slots={})
            # Core does NOT receive booking_id, so status remains NEEDS_CLARIFICATION
            {"sentence": "booking xyz789", "expected": {
                "status": "NEEDS_CLARIFICATION", "missing_slots": ["booking_id"]}}
        ]
    },
    {
        "id": 33,
        "name": "modify_booking_date_range",
        "domain": "reservation",
        "aliases": {"suite": "room"},
        "turns": [
            # Missing booking_id AND change dimensions (start_date, end_date)
            # Core requires booking_id + start_date + end_date for reservation domain MODIFY_BOOKING
            {"sentence": "change my reservation", "expected": {"intent": "MODIFY_BOOKING",
                                                               "status": "NEEDS_CLARIFICATION", "missing_slots": ["booking_id", "start_date", "end_date"]}},
            # booking_id was not extracted; dates do NOT imply booking_id
            {"sentence": "reservation def456 to nov 1st to 5th",
                "expected": {"status": "NEEDS_CLARIFICATION", "missing_slots": ["booking_id"]}}
        ]
    },
    {
        "id": 34,
        "name": "modify_booking_time_followup",
        "domain": "service",
        "aliases": {"haircut": "haircut"},
        "turns": [
            # Missing booking_id AND all temporal fields (date, time)
            # Core requires booking_id + date + time for service domain MODIFY_BOOKING
            {"sentence": "change booking", "expected": {"intent": "MODIFY_BOOKING",
                                                        "status": "NEEDS_CLARIFICATION", "missing_slots": ["booking_id", "date", "time"]}},
            # Luma returns slots={} (booking_id not extracted from "booking 123 to 4pm")
            # Core does NOT receive booking_id, so status remains NEEDS_CLARIFICATION
            {"sentence": "booking 123 to 4pm", "expected": {
                "status": "NEEDS_CLARIFICATION", "missing_slots": ["booking_id"]}}
        ]
    },
    {
        "id": 35,
        "name": "modify_booking_date_followup",
        "domain": "service",
        "aliases": {"massage": "massage"},
        "turns": [
            # Missing booking_id AND all temporal fields (date, time)
            # Core requires booking_id + date + time for service domain MODIFY_BOOKING
            {"sentence": "modify booking", "expected": {"intent": "MODIFY_BOOKING",
                                                        "status": "NEEDS_CLARIFICATION", "missing_slots": ["booking_id", "date", "time"]}},
            # Luma returns date, but NOT booking_id from "booking 456 to monday"
            # Core does NOT receive booking_id, so status remains NEEDS_CLARIFICATION
            {"sentence": "booking 456 to monday", "expected": {
                "status": "NEEDS_CLARIFICATION", "missing_slots": ["booking_id"]}}
        ]
    },
    {
        "id": 36,
        "name": "modify_reservation_range",
        "domain": "reservation",
        "aliases": {"room": "room"},
        "turns": [
            # Missing booking_id AND change dimensions (start_date, end_date)
            # Core requires booking_id + start_date + end_date for reservation domain MODIFY_BOOKING
            {"sentence": "change reservation", "expected": {"intent": "MODIFY_BOOKING",
                                                            "status": "NEEDS_CLARIFICATION", "missing_slots": ["booking_id", "start_date", "end_date"]}},
            # booking_id was not extracted; dates do NOT imply booking_id
            {"sentence": "reservation 789 to dec 1 to 5",
                "expected": {"status": "NEEDS_CLARIFICATION", "missing_slots": ["booking_id"]}}
        ]
    },
    {
        "id": 37,
        "name": "modify_booking_with_id",
        "domain": "service",
        "aliases": {"haircut": "haircut"},
        "turns": [
            # booking_id is provided, but temporal fields (date, time) are missing
            # Core requires booking_id + date + time for service domain MODIFY_BOOKING
            {"sentence": "change booking abc123", "expected": {"intent": "MODIFY_BOOKING",
                                                               "status": "NEEDS_CLARIFICATION", "missing_slots": ["booking_id", "date", "time"]}},
            # Luma returns time, but NOT booking_id from "to 5pm"
            # Core does NOT receive booking_id, so status remains NEEDS_CLARIFICATION
            {"sentence": "to 5pm", "expected": {
                "status": "NEEDS_CLARIFICATION", "missing_slots": ["booking_id"]}}
        ]
    },
    {
        "id": 38,
        "name": "modify_reservation_with_id",
        "domain": "reservation",
        "aliases": {"suite": "room"},
        "turns": [
            # booking_id is provided, but change dimensions (start_date, end_date) are missing
            # Core requires booking_id + start_date + end_date for reservation domain MODIFY_BOOKING
            {"sentence": "change reservation xyz789", "expected": {
                "intent": "MODIFY_BOOKING", "status": "NEEDS_CLARIFICATION", "missing_slots": ["booking_id", "start_date", "end_date"]}},
            # Luma returns date_range, but NOT booking_id from "to jan 10 to 15"
            # Core does NOT receive booking_id, so status remains NEEDS_CLARIFICATION
            {"sentence": "to jan 10 to 15", "expected": {
                "status": "NEEDS_CLARIFICATION", "missing_slots": ["booking_id"]}}
        ]
    },
    {
        "id": 39,
        "name": "modify_booking_multiple_turns",
        "domain": "service",
        "aliases": {"massage": "massage"},
        "turns": [
            # Missing booking_id AND all temporal fields (date, time)
            # Core requires booking_id + date + time for service domain MODIFY_BOOKING
            {"sentence": "change booking", "expected": {"intent": "MODIFY_BOOKING",
                                                        "status": "NEEDS_CLARIFICATION", "missing_slots": ["booking_id", "date", "time"]}},
            # Luma returns slots={} (booking_id not extracted from "booking 111")
            # Core does NOT receive booking_id, so status remains NEEDS_CLARIFICATION
            {"sentence": "booking 111", "expected": {
                "status": "NEEDS_CLARIFICATION", "missing_slots": ["booking_id"]}},
            # Luma returns time, but NOT booking_id from "time to 2pm"
            # Core does NOT receive booking_id, so status remains NEEDS_CLARIFICATION
            {"sentence": "time to 2pm", "expected": {
                "status": "NEEDS_CLARIFICATION", "missing_slots": ["booking_id"]}}
        ]
    },
    {
        "id": 40,
        "name": "modify_reservation_multiple_turns",
        "domain": "reservation",
        "aliases": {"room": "room"},
        "turns": [
            # Missing booking_id AND change dimensions (start_date, end_date)
            {"sentence": "change reservation", "expected": {"intent": "MODIFY_BOOKING",
                                                            "status": "NEEDS_CLARIFICATION", "missing_slots": ["booking_id", "start_date", "end_date"]}},
            # booking_id was not extracted; date slots are blocked until it exists
            {"sentence": "reservation 222", "expected": {
                "status": "NEEDS_CLARIFICATION", "missing_slots": ["booking_id"]}},
            # booking_id still not extracted; dates do NOT imply booking_id
            {"sentence": "dates to feb 1 to 5", "expected": {
                "status": "NEEDS_CLARIFICATION", "missing_slots": ["booking_id"]}}
        ]
    },
    # Ambiguous follow-ups and edge cases (IDs 41-50)
    {
        "id": 41,
        "name": "ambiguous_tomorrow_followup",
        "domain": "service",
        "aliases": {"haircut": "haircut"},
        "turns": [
            {"sentence": "book haircut", "expected": {"intent": "CREATE_APPOINTMENT",
                                                      "status": "NEEDS_CLARIFICATION", "missing_slots": ["date", "time"]}},
            {"sentence": "tomorrow", "expected": {
                "status": "NEEDS_CLARIFICATION", "missing_slots": ["time"]}},
            {"sentence": "evening", "expected": {"status": "READY"}}
        ]
    },
    {
        "id": 42,
        "name": "ambiguous_next_friday",
        "domain": "service",
        "aliases": {"massage": "massage"},
        "turns": [
            {"sentence": "schedule massage", "expected": {"intent": "CREATE_APPOINTMENT",
                                                          "status": "NEEDS_CLARIFICATION", "missing_slots": ["date", "time"]}},
            {"sentence": "next friday", "expected": {
                "status": "NEEDS_CLARIFICATION", "missing_slots": ["time"]}},
            {"sentence": "2pm", "expected": {"status": "READY"}}
        ]
    },
    {
        "id": 43,
        "name": "ambiguous_evening_followup",
        "domain": "service",
        "aliases": {"facial": "facial"},
        "turns": [
            {"sentence": "book facial", "expected": {"intent": "CREATE_APPOINTMENT",
                                                     "status": "NEEDS_CLARIFICATION", "missing_slots": ["date", "time"]}},
            {"sentence": "evening", "expected": {
                "status": "NEEDS_CLARIFICATION", "missing_slots": ["date"]}},
            {"sentence": "tomorrow", "expected": {"status": "READY"}}
        ]
    },
    {
        "id": 44,
        "name": "ambiguous_morning_followup",
        "domain": "service",
        "aliases": {"haircut": "haircut"},
        "turns": [
            {"sentence": "book haircut", "expected": {"intent": "CREATE_APPOINTMENT",
                                                      "status": "NEEDS_CLARIFICATION", "missing_slots": ["date", "time"]}},
            {"sentence": "morning", "expected": {
                "status": "NEEDS_CLARIFICATION", "missing_slots": ["date"]}},
            {"sentence": "friday", "expected": {"status": "READY"}}
        ]
    },
    {
        "id": 45,
        "name": "unknown_to_booking_via_followup",
        "domain": "service",
        "aliases": {"haircut": "haircut"},
        "turns": [
            # UNKNOWN intent must resolve to READY (no clarification required)
            {"sentence": "hello", "expected": {"status": "READY"}},
            # Intent switch resets context - new CREATE_APPOINTMENT requires date and time
            {"sentence": "book a haircut", "expected": {"intent": "CREATE_APPOINTMENT",
                                                        "status": "NEEDS_CLARIFICATION", "missing_slots": ["date", "time"]}},
            {"sentence": "tomorrow at 10am", "expected": {"status": "READY"}}
        ]
    },
    {
        "id": 46,
        "name": "intent_switch_resets_session",
        "domain": "service",
        "aliases": {"haircut": "haircut"},
        "turns": [
            {"sentence": "book haircut", "expected": {"intent": "CREATE_APPOINTMENT",
                                                      "status": "NEEDS_CLARIFICATION", "missing_slots": ["date", "time"]}},
            {"sentence": "cancel my booking", "expected": {"intent": "CANCEL_BOOKING",
                                                           "status": "NEEDS_CLARIFICATION", "missing_slots": ["booking_id"]}},
            # booking_id was not extracted; planning cannot advance to READY
            {"sentence": "booking abc123", "expected": {
                "status": "NEEDS_CLARIFICATION", "missing_slots": ["booking_id"]}}
        ]
    },
    {
        "id": 47,
        "name": "intent_switch_modify_to_cancel",
        "domain": "service",
        "aliases": {"massage": "massage"},
        "turns": [
            # User said "change booking" without specifying what dimension to change
            # MODIFY_BOOKING requires booking_id + at least one change slot (date, time, etc.)
            {"sentence": "change booking", "expected": {"intent": "MODIFY_BOOKING",
                                                        "status": "NEEDS_CLARIFICATION", "missing_slots": ["booking_id", "date", "time"]}},
            # Intent switch resets context completely - CANCEL_BOOKING only needs booking_id
            {"sentence": "actually cancel it", "expected": {"intent": "CANCEL_BOOKING",
                                                            "status": "NEEDS_CLARIFICATION", "missing_slots": ["booking_id"]}},
            # booking_id was not extracted; dates do NOT imply booking_id
            {"sentence": "booking xyz789", "expected": {
                "status": "NEEDS_CLARIFICATION", "missing_slots": ["booking_id"]}}
        ]
    },
    {
        "id": 48,
        "name": "intent_switch_create_to_cancel",
        "domain": "service",
        "aliases": {"facial": "facial"},
        "turns": [
            {"sentence": "book facial", "expected": {"intent": "CREATE_APPOINTMENT",
                                                     "status": "NEEDS_CLARIFICATION", "missing_slots": ["date", "time"]}},
            {"sentence": "nevermind cancel", "expected": {"intent": "CANCEL_BOOKING",
                                                          "status": "NEEDS_CLARIFICATION", "missing_slots": ["booking_id"]}},
            # booking_id was not extracted; planning cannot advance to READY
            {"sentence": "booking 999", "expected": {
                "status": "NEEDS_CLARIFICATION", "missing_slots": ["booking_id"]}}
        ]
    },
    {
        "id": 49,
        "name": "multi_turn_ambiguous_followup",
        "domain": "service",
        "aliases": {"waxing": "waxing"},
        "turns": [
            {"sentence": "book waxing", "expected": {"intent": "CREATE_APPOINTMENT",
                                                     "status": "NEEDS_CLARIFICATION", "missing_slots": ["date", "time"]}},
            # "next week" resolved a date_range; date is no longer missing
            {"sentence": "next week", "expected": {
                "status": "NEEDS_CLARIFICATION", "missing_slots": ["time"]}},
            # "morning" provides time (normalized from context.time_constraint)
            # date_range + time fully satisfies CREATE_APPOINTMENT → READY
            {"sentence": "morning", "expected": {"status": "READY"}}
        ]
    },
    {
        "id": 50,
        "name": "reservation_ambiguous_followup",
        "domain": "reservation",
        "aliases": {"room": "room"},
        "turns": [
            {"sentence": "book room", "expected": {"intent": "CREATE_RESERVATION",
                                                   "status": "NEEDS_CLARIFICATION", "missing_slots": ["start_date", "end_date"]}},
            # "next month" resolved start_date; only end_date remains missing
            {"sentence": "next month", "expected": {
                "status": "NEEDS_CLARIFICATION", "missing_slots": ["end_date"]}},
            # "from the 1st" provides start_date (normalization may map date → start_date)
            {"sentence": "from the 1st", "expected": {
                "status": "NEEDS_CLARIFICATION", "missing_slots": ["end_date"]}},
            # "to the 5th" does NOT provide end_date; a single date does NOT imply both start_date and end_date
            # has_datetime is NOT for reservations (only service appointments with time)
            {"sentence": "to the 5th", "expected": {
                "status": "NEEDS_CLARIFICATION", "missing_slots": ["end_date"]}}
        ]
    },
    # Negative guardrail tests: awaiting_slot routing must NOT infer or over-route (IDs 51-55)
    # These tests ensure awaiting_slot logic is deterministic and non-inferential
    {
        "id": 51,
        "name": "awaiting_time_rejects_date",
        "domain": "service",
        "aliases": {"haircut": "haircut"},
        "turns": [
            {"sentence": "book haircut", "expected": {"intent": "CREATE_APPOINTMENT",
                                                      "status": "NEEDS_CLARIFICATION", "missing_slots": ["date", "time"]}},
            {"sentence": "tomorrow", "expected": {
                "status": "NEEDS_CLARIFICATION", "missing_slots": ["time"]}},
            # Guardrail: awaiting_slot=time must NOT accept date values
            # "next week" is a date, not a time - must remain NEEDS_CLARIFICATION with missing_slots=["time"]
            {"sentence": "next week", "expected": {
                "status": "NEEDS_CLARIFICATION", "missing_slots": ["time"]}}
        ]
    },
    {
        "id": 52,
        "name": "awaiting_date_rejects_time",
        "domain": "service",
        "aliases": {"massage": "massage"},
        "turns": [
            {"sentence": "book massage at 3pm", "expected": {"intent": "CREATE_APPOINTMENT",
                                                             "status": "NEEDS_CLARIFICATION", "missing_slots": ["date"]}},
            # Guardrail: awaiting_slot=date must NOT accept time values
            # "5pm" is a time, not a date - must remain NEEDS_CLARIFICATION with missing_slots=["date"]
            {"sentence": "5pm", "expected": {
                "status": "NEEDS_CLARIFICATION", "missing_slots": ["date"]}}
        ]
    },
    {
        "id": 53,
        "name": "awaiting_slot_cleared_on_intent_switch",
        "domain": "service",
        "aliases": {"haircut": "haircut"},
        "turns": [
            {"sentence": "book haircut", "expected": {"intent": "CREATE_APPOINTMENT",
                                                      "status": "NEEDS_CLARIFICATION", "missing_slots": ["date", "time"]}},
            {"sentence": "tomorrow", "expected": {
                "status": "NEEDS_CLARIFICATION", "missing_slots": ["time"]}},
            # Guardrail: Intent switch must clear awaiting_slot
            # CANCEL_BOOKING has different missing_slots - awaiting_slot from CREATE_APPOINTMENT must not leak
            {"sentence": "cancel booking", "expected": {"intent": "CANCEL_BOOKING",
                                                        "status": "NEEDS_CLARIFICATION", "missing_slots": ["booking_id"]}}
        ]
    },
    {
        "id": 54,
        "name": "awaiting_slot_not_used_for_modify_booking",
        "domain": "service",
        "aliases": {"haircut": "haircut"},
        "turns": [
            # Guardrail: MODIFY_BOOKING must NOT use awaiting_slot routing
            # MODIFY_BOOKING requires explicit dimensions - awaiting_slot logic is CREATE_APPOINTMENT only
            {"sentence": "change booking", "expected": {"intent": "MODIFY_BOOKING",
                                                        "status": "NEEDS_CLARIFICATION", "missing_slots": ["booking_id", "date", "time"]}},
            # "tomorrow" should NOT be routed via awaiting_slot for MODIFY_BOOKING
            # Missing booking_id blocks resolution - date alone doesn't satisfy MODIFY_BOOKING
            {"sentence": "tomorrow", "expected": {
                "status": "NEEDS_CLARIFICATION", "missing_slots": ["booking_id"]}}
        ]
    },
    {
        "id": 55,
        "name": "awaiting_slot_does_not_route_booking_id",
        "domain": "service",
        "aliases": {"massage": "massage"},
        "turns": [
            {"sentence": "book massage", "expected": {"intent": "CREATE_APPOINTMENT",
                                                      "status": "NEEDS_CLARIFICATION", "missing_slots": ["date", "time"]}},
            # Guardrail: awaiting_slot must NEVER route booking_id
            # booking_id is never inferred or routed - only explicitly extracted slots apply
            # "booking 123" should NOT be routed to date or time slots
            {"sentence": "booking 123", "expected": {
                "status": "NEEDS_CLARIFICATION", "missing_slots": ["date", "time"]}}
        ]
    },
    # ========================================================================
    # Negative Safety-Net Tests: Slot Inference Prevention (IDs 56-65)
    # ========================================================================
    # These tests ensure Core NEVER infers slots that must be explicitly extracted
    {
        "id": 56,
        "name": "booking_id_not_inferred_from_phrase",
        "domain": "service",
        "aliases": {"haircut": "haircut"},
        "turns": [
            {"sentence": "cancel my booking", "expected": {"intent": "CANCEL_BOOKING",
                                                           "status": "NEEDS_CLARIFICATION", "missing_slots": ["booking_id"]}},
            # Guardrail: Core must NOT infer booking_id from phrases like "booking abc123"
            # Luma may not extract booking_id from this phrase - Core must NOT infer it
            {"sentence": "booking abc123", "expected": {
                "status": "NEEDS_CLARIFICATION", "missing_slots": ["booking_id"]}}
        ]
    },
    {
        "id": 57,
        "name": "booking_id_not_inferred_from_reservation_phrase",
        "domain": "reservation",
        "aliases": {"room": "room"},
        "turns": [
            {"sentence": "cancel reservation", "expected": {"intent": "CANCEL_BOOKING",
                                                            "status": "NEEDS_CLARIFICATION", "missing_slots": ["booking_id"]}},
            # Guardrail: Core must NOT infer booking_id from "reservation xyz789"
            # Luma may not extract booking_id - Core must NOT infer it
            {"sentence": "reservation xyz789", "expected": {
                "status": "NEEDS_CLARIFICATION", "missing_slots": ["booking_id"]}}
        ]
    },
    {
        "id": 58,
        "name": "end_date_not_inferred_from_second_date",
        "domain": "reservation",
        "aliases": {"suite": "room"},
        "turns": [
            {"sentence": "reserve suite", "expected": {"intent": "CREATE_RESERVATION",
                                                       "status": "NEEDS_CLARIFICATION", "missing_slots": ["start_date", "end_date"]}},
            {"sentence": "from nov 1st", "expected": {
                "status": "NEEDS_CLARIFICATION", "missing_slots": ["end_date"]}},
            # Guardrail: Core must NOT infer end_date from a second date slot
            # If Luma emits only "date" (not "end_date"), Core must NOT infer end_date
            # Core does NOT assume a second date means end_date
            {"sentence": "nov 5th", "expected": {
                "status": "NEEDS_CLARIFICATION", "missing_slots": ["end_date"]}}
        ]
    },
    {
        "id": 59,
        "name": "end_date_not_inferred_from_date_range_phrase",
        "domain": "reservation",
        "aliases": {"room": "room"},
        "turns": [
            {"sentence": "book room", "expected": {"intent": "CREATE_RESERVATION",
                                                   "status": "NEEDS_CLARIFICATION", "missing_slots": ["start_date", "end_date"]}},
            {"sentence": "from dec 10th", "expected": {
                "status": "NEEDS_CLARIFICATION", "missing_slots": ["end_date"]}},
            # Guardrail: Core must NOT infer end_date from phrases like "to dec 15th"
            # If Luma does not emit end_date slot, Core must NOT infer it
            {"sentence": "to dec 15th", "expected": {
                "status": "NEEDS_CLARIFICATION", "missing_slots": ["end_date"]}}
        ]
    },
    {
        "id": 60,
        "name": "start_date_not_inferred_from_date_for_reservation",
        "domain": "reservation",
        "aliases": {"deluxe": "room"},
        "turns": [
            {"sentence": "reserve deluxe", "expected": {"intent": "CREATE_RESERVATION",
                                                        "status": "NEEDS_CLARIFICATION", "missing_slots": ["start_date", "end_date"]}},
            # Guardrail: Core must NOT infer start_date from a generic "date" slot
            # For reservations, Luma must emit "start_date" explicitly
            # If Luma emits only "date", Core must NOT infer start_date
            {"sentence": "jan 5th", "expected": {
                "status": "NEEDS_CLARIFICATION", "missing_slots": ["start_date", "end_date"]}}
        ]
    },
    # ========================================================================
    # Negative Safety-Net Tests: Execution Blocking (IDs 61-65)
    # ========================================================================
    # These tests ensure Core NEVER executes with insufficient slots
    {
        "id": 61,
        "name": "create_appointment_blocked_without_date",
        "domain": "service",
        "aliases": {"massage": "massage"},
        "turns": [
            {"sentence": "book massage at 3pm", "expected": {"intent": "CREATE_APPOINTMENT",
                                                             "status": "NEEDS_CLARIFICATION", "missing_slots": ["date"]}},
            # Guardrail: Core must NOT execute (status must NOT become READY) without date
            # Even if time is provided, missing date blocks execution
            {"sentence": "what times are available", "expected": {
                "status": "NEEDS_CLARIFICATION", "missing_slots": ["date"]}}
        ]
    },
    {
        "id": 62,
        "name": "create_appointment_blocked_without_time",
        "domain": "service",
        "aliases": {"haircut": "haircut"},
        "turns": [
            {"sentence": "book haircut tomorrow", "expected": {"intent": "CREATE_APPOINTMENT",
                                                               "status": "NEEDS_CLARIFICATION", "missing_slots": ["time"]}},
            # Guardrail: Core must NOT execute (status must NOT become READY) without time
            # Even if date is provided, missing time blocks execution
            {"sentence": "what services do you offer", "expected": {
                "status": "NEEDS_CLARIFICATION", "missing_slots": ["time"]}}
        ]
    },
    {
        "id": 63,
        "name": "create_reservation_blocked_without_end_date",
        "domain": "reservation",
        "aliases": {"room": "room"},
        "turns": [
            {"sentence": "book room", "expected": {"intent": "CREATE_RESERVATION",
                                                   "status": "NEEDS_CLARIFICATION", "missing_slots": ["start_date", "end_date"]}},
            {"sentence": "from feb 1st", "expected": {
                "status": "NEEDS_CLARIFICATION", "missing_slots": ["end_date"]}},
            # Guardrail: Core must NOT execute (status must NOT become READY) without end_date
            # Even if start_date is provided, missing end_date blocks execution
            {"sentence": "what rooms are available", "expected": {
                "status": "NEEDS_CLARIFICATION", "missing_slots": ["end_date"]}}
        ]
    },
    {
        "id": 64,
        "name": "modify_booking_blocked_without_booking_id",
        "domain": "service",
        "aliases": {"haircut": "haircut"},
        "turns": [
            {"sentence": "change booking to friday", "expected": {"intent": "MODIFY_BOOKING",
                                                                  "status": "NEEDS_CLARIFICATION", "missing_slots": ["booking_id", "time"]}},
            # Guardrail: Core must NOT execute (status must NOT become READY) without booking_id
            # Even if date is provided, missing booking_id blocks execution
            {"sentence": "at 2pm", "expected": {
                "status": "NEEDS_CLARIFICATION", "missing_slots": ["booking_id"]}}
        ]
    },
    {
        "id": 65,
        "name": "cancel_booking_blocked_without_booking_id",
        "domain": "service",
        "aliases": {},
        "turns": [
            {"sentence": "cancel my booking", "expected": {"intent": "CANCEL_BOOKING",
                                                           "status": "NEEDS_CLARIFICATION", "missing_slots": ["booking_id"]}},
            # Guardrail: Core must NOT execute (status must NOT become READY) without booking_id
            # Cancellation requires booking_id - no other slots can substitute
            {"sentence": "the one tomorrow", "expected": {
                "status": "NEEDS_CLARIFICATION", "missing_slots": ["booking_id"]}}
        ]
    },
    # ========================================================================
    # Negative Safety-Net Tests: Ambiguous Temporal Phrase Rejection (IDs 66-70)
    # ========================================================================
    # These tests ensure Core NEVER auto-resolves ambiguous temporal phrases
    {
        "id": 66,
        "name": "tomorrow_rejected_when_awaiting_time",
        "domain": "service",
        "aliases": {"facial": "facial"},
        "turns": [
            {"sentence": "book facial", "expected": {"intent": "CREATE_APPOINTMENT",
                                                     "status": "NEEDS_CLARIFICATION", "missing_slots": ["date", "time"]}},
            {"sentence": "tomorrow", "expected": {
                "status": "NEEDS_CLARIFICATION", "missing_slots": ["time"]}},
            # Guardrail: "tomorrow" is a date, not a time
            # When awaiting_slot=time, Core must NOT accept "tomorrow" as time
            # Core must remain NEEDS_CLARIFICATION with missing_slots=["time"]
            {"sentence": "tomorrow", "expected": {
                "status": "NEEDS_CLARIFICATION", "missing_slots": ["time"]}}
        ]
    },
    {
        "id": 67,
        "name": "next_week_rejected_when_awaiting_time",
        "domain": "service",
        "aliases": {"waxing": "waxing"},
        "turns": [
            {"sentence": "book waxing", "expected": {"intent": "CREATE_APPOINTMENT",
                                                     "status": "NEEDS_CLARIFICATION", "missing_slots": ["date", "time"]}},
            {"sentence": "next monday", "expected": {
                "status": "NEEDS_CLARIFICATION", "missing_slots": ["time"]}},
            # Guardrail: "next week" is a date, not a time
            # When awaiting_slot=time, Core must NOT accept "next week" as time
            {"sentence": "next week", "expected": {
                "status": "NEEDS_CLARIFICATION", "missing_slots": ["time"]}}
        ]
    },
    {
        "id": 68,
        "name": "time_phrase_rejected_when_awaiting_date",
        "domain": "service",
        "aliases": {"manicure": "manicure"},
        "turns": [
            {"sentence": "book manicure at 3pm", "expected": {"intent": "CREATE_APPOINTMENT",
                                                              "status": "NEEDS_CLARIFICATION", "missing_slots": ["date"]}},
            # Guardrail: "5pm" is a time, not a date
            # When awaiting_slot=date, Core must NOT accept "5pm" as date
            {"sentence": "5pm", "expected": {
                "status": "NEEDS_CLARIFICATION", "missing_slots": ["date"]}}
        ]
    },
    {
        "id": 69,
        "name": "evening_rejected_when_awaiting_date",
        "domain": "service",
        "aliases": {"pedicure": "pedicure"},
        "turns": [
            {"sentence": "book pedicure at evening", "expected": {"intent": "CREATE_APPOINTMENT",
                                                                  "status": "NEEDS_CLARIFICATION", "missing_slots": ["date"]}},
            # Guardrail: "morning" is a time phrase, not a date
            # When awaiting_slot=date, Core must NOT accept "morning" as date
            {"sentence": "morning", "expected": {
                "status": "NEEDS_CLARIFICATION", "missing_slots": ["date"]}}
        ]
    },
    {
        "id": 70,
        "name": "ambiguous_phrase_not_auto_resolved",
        "domain": "service",
        "aliases": {"coloring": "coloring"},
        "turns": [
            {"sentence": "book coloring", "expected": {"intent": "CREATE_APPOINTMENT",
                                                       "status": "NEEDS_CLARIFICATION", "missing_slots": ["date", "time"]}},
            # Guardrail: Ambiguous phrases like "this weekend" must NOT auto-resolve
            # If Luma does not extract clear date/time, Core must NOT infer it
            {"sentence": "this weekend", "expected": {
                "status": "NEEDS_CLARIFICATION", "missing_slots": ["date", "time"]}}
        ]
    },
    # ========================================================================
    # Negative Safety-Net Tests: Cross-Domain Leakage Prevention (IDs 71-75)
    # ========================================================================
    # These tests ensure service and reservation domain rules do NOT leak
    {
        "id": 71,
        "name": "service_date_time_not_applied_to_reservation",
        "domain": "reservation",
        "aliases": {"room": "room"},
        "turns": [
            {"sentence": "book room", "expected": {"intent": "CREATE_RESERVATION",
                                                   "status": "NEEDS_CLARIFICATION", "missing_slots": ["start_date", "end_date"]}},
            # Guardrail: Service domain uses date+time, reservation uses start_date+end_date
            # Core must NOT accept "date" + "time" as satisfying reservation requirements
            # Reservation requires start_date + end_date, not date + time
            {"sentence": "tomorrow at 3pm", "expected": {
                "status": "NEEDS_CLARIFICATION", "missing_slots": ["start_date", "end_date"]}}
        ]
    },
    {
        "id": 72,
        "name": "reservation_date_range_not_applied_to_service",
        "domain": "service",
        "aliases": {"haircut": "haircut"},
        "turns": [
            {"sentence": "book haircut", "expected": {"intent": "CREATE_APPOINTMENT",
                                                      "status": "NEEDS_CLARIFICATION", "missing_slots": ["date", "time"]}},
            # Guardrail: Reservation domain uses start_date+end_date, service uses date+time
            # Core must NOT accept "start_date" + "end_date" as satisfying service requirements
            # Service requires date + time, not start_date + end_date
            {"sentence": "from nov 1st to nov 5th", "expected": {
                "status": "NEEDS_CLARIFICATION", "missing_slots": ["date", "time"]}}
        ]
    },
    {
        "id": 73,
        "name": "awaiting_slot_not_leaked_across_domains",
        "domain": "service",
        "aliases": {"massage": "massage"},
        "turns": [
            {"sentence": "book massage", "expected": {"intent": "CREATE_APPOINTMENT",
                                                      "status": "NEEDS_CLARIFICATION", "missing_slots": ["date", "time"]}},
            {"sentence": "tomorrow", "expected": {
                "status": "NEEDS_CLARIFICATION", "missing_slots": ["time"]}},
            # Guardrail: If domain switches (e.g., service → reservation), awaiting_slot must be cleared
            # This test verifies awaiting_slot does not leak if domain context changes
            # (Note: Actual domain switch would require different test setup, but this tests the principle)
            {"sentence": "reserve room", "expected": {"intent": "CREATE_RESERVATION",
                                                      "status": "NEEDS_CLARIFICATION", "missing_slots": ["start_date", "end_date"]}}
        ]
    },
    {
        "id": 74,
        "name": "service_has_datetime_not_applied_to_reservation",
        "domain": "reservation",
        "aliases": {"suite": "room"},
        "turns": [
            {"sentence": "book suite", "expected": {"intent": "CREATE_RESERVATION",
                                                    "status": "NEEDS_CLARIFICATION", "missing_slots": ["start_date", "end_date"]}},
            # Guardrail: Service domain uses has_datetime (date+time), reservation does NOT
            # Core must NOT set has_datetime for reservations
            # Reservation requires start_date + end_date, not date + time
            {"sentence": "nov 1st at 2pm", "expected": {
                "status": "NEEDS_CLARIFICATION", "missing_slots": ["start_date", "end_date"]}}
        ]
    },
    {
        "id": 75,
        "name": "reservation_date_range_not_satisfies_service_date",
        "domain": "service",
        "aliases": {"facial": "facial"},
        "turns": [
            {"sentence": "book facial", "expected": {"intent": "CREATE_APPOINTMENT",
                                                     "status": "NEEDS_CLARIFICATION", "missing_slots": ["date", "time"]}},
            # Guardrail: Reservation date_range does NOT satisfy service date requirement
            # Service requires explicit "date" slot, not "date_range" or "start_date"
            {"sentence": "dec 10 to 15", "expected": {
                "status": "NEEDS_CLARIFICATION", "missing_slots": ["date", "time"]}}
        ]
    },
    # ========================================================================
    # Reservation Contract Enforcement Tests (IDs 76-81)
    # ========================================================================
    # These tests verify the fixes applied to session_merge.py to enforce
    # strict reservation slot contracts (no inference, role-specific slots)
    {
        "id": 76,
        "name": "reservation_date_does_not_satisfy_start_date",
        "domain": "reservation",
        "aliases": {"suite": "room"},
        "turns": [
            {"sentence": "book suite", "expected": {"intent": "CREATE_RESERVATION",
                                                    "status": "NEEDS_CLARIFICATION", "missing_slots": ["start_date", "end_date"]}},
            # Guardrail: Generic "date" slot does NOT satisfy "start_date" requirement
            # Luma may return slots.date, but Core must remove it and NOT set slots.start_date
            # This verifies the fix that removes date → start_date mapping
            {"sentence": "tomorrow", "expected": {
                "status": "NEEDS_CLARIFICATION", "missing_slots": ["start_date", "end_date"]}}
        ]
    },
    {
        "id": 77,
        "name": "reservation_single_date_does_not_infer_end_date",
        "domain": "reservation",
        "aliases": {"deluxe": "room"},
        "turns": [
            {"sentence": "reserve deluxe from jan 15", "expected": {"intent": "CREATE_RESERVATION",
                                                                    "status": "NEEDS_CLARIFICATION", "missing_slots": ["end_date"]}},
            # Guardrail: Single date with START_DATE role does NOT infer end_date
            # Even if start_date is set, end_date must be explicitly provided
            # This verifies the fix that removed single date → end_date inference
            {"sentence": "confirm", "expected": {
                "status": "NEEDS_CLARIFICATION", "missing_slots": ["end_date"]}}
        ]
    },
    {
        "id": 78,
        "name": "reservation_generic_date_slot_removed",
        "domain": "reservation",
        "aliases": {"penthouse": "room"},
        "turns": [
            {"sentence": "book penthouse", "expected": {"intent": "CREATE_RESERVATION",
                                                        "status": "NEEDS_CLARIFICATION", "missing_slots": ["start_date", "end_date"]}},
            # Guardrail: Generic "date" slot from Luma must be removed for reservations
            # Core must NOT keep slots.date, and must NOT infer slots.start_date from it
            # This verifies the fix that deletes generic date slot for CREATE_RESERVATION
            {"sentence": "jan 20th", "expected": {
                "status": "NEEDS_CLARIFICATION", "missing_slots": ["start_date", "end_date"]}}
        ]
    },
    {
        "id": 79,
        "name": "reservation_range_mode_requires_both_dates",
        "domain": "reservation",
        "aliases": {"villa": "room"},
        "turns": [
            {"sentence": "reserve villa", "expected": {"intent": "CREATE_RESERVATION",
                                                       "status": "NEEDS_CLARIFICATION", "missing_slots": ["start_date", "end_date"]}},
            # Guardrail: Range mode with single date does NOT infer start_date or end_date
            # Both dates must be explicitly provided in date_refs
            # This verifies the fix that blocks single-date promotion in range mode
            {"sentence": "from jan 10", "expected": {
                "status": "NEEDS_CLARIFICATION", "missing_slots": ["start_date", "end_date"]}}
        ]
    },
    {
        "id": 80,
        "name": "service_appointment_date_extraction_unchanged",
        "domain": "service",
        "aliases": {"haircut": "haircut"},
        "turns": [
            {"sentence": "book haircut", "expected": {"intent": "CREATE_APPOINTMENT",
                                                      "status": "NEEDS_CLARIFICATION", "missing_slots": ["date", "time"]}},
            # Guardrail: Service appointment behavior must remain unchanged
            # Generic "date" slot extraction still works for CREATE_APPOINTMENT
            # This verifies that reservation fixes don't break service appointments
            {"sentence": "tomorrow", "expected": {
                "status": "NEEDS_CLARIFICATION", "missing_slots": ["time"]}}
        ]
    },
    {
        "id": 81,
        "name": "reservation_safety_assertion_restores_missing_slots",
        "domain": "reservation",
        "aliases": {"suite": "room"},
        "turns": [
            {"sentence": "book suite", "expected": {"intent": "CREATE_RESERVATION",
                                                    "status": "NEEDS_CLARIFICATION", "missing_slots": ["start_date", "end_date"]}},
            # Guardrail: Safety assertion must restore missing_slots if incorrectly cleared
            # If Luma incorrectly returns empty missing_slots, Core must restore start_date and end_date
            # This verifies the safety assertion added to session_merge.py
            {"sentence": "ok", "expected": {
                "status": "NEEDS_CLARIFICATION", "missing_slots": ["start_date", "end_date"]}}
        ]
    }
]
