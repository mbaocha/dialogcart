# Followup scenarios - multi-turn conversations with shared user_id
# Each scenario is a batch of related turns that share the same user_id
followup_scenarios = [
    {
        "name": "service_to_date_to_time",
        "booking_mode": "service",
        "turns": [
            {
                "sentence": "book me a haircut",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": "needs_clarification",
                    "missing_slots": ["date", "time"]
                }
            },
            {
                "sentence": "tomorrow",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": "needs_clarification",
                    "missing_slots": ["time"]
                }
            },
            {
                "sentence": "at 3pm",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": "ready",
                    "slots": {
                        "service_id": "haircut",  # Single tenant alias for haircut
                        "has_datetime": True
                    }
                }
            }
        ]
    },
    {
        "name": "reservation_service_to_dates",
        "booking_mode": "reservation",
        "turns": [
            {
                "sentence": "reserve a room",
                "expected": {
                    "intent": "CREATE_RESERVATION",
                    "status": "needs_clarification",
                    "missing_slots": ["start_date", "end_date"]
                }
            },
            {
                "sentence": "from october 5th",
                "expected": {
                    "intent": "CREATE_RESERVATION",
                    "status": "needs_clarification",
                    "missing_slots": ["end_date"]
                }
            },
            {
                "sentence": "to october 9th",
                "expected": {
                    "intent": "CREATE_RESERVATION",
                    "status": "ready",
                    "slots": {
                        "service_id": "room",  # Explicit tenant alias key
                        "date_range": {
                            "start": "2026-10-05",
                            "end": "2026-10-09"
                        }
                    }
                }
            }
        ]
    },
    {
        "name": "time_to_date_appointment",
        "booking_mode": "service",
        "turns": [
            {
                "sentence": "book massage at 2pm",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": "needs_clarification",
                    "missing_slots": ["date"]
                }
            },
            {
                "sentence": "tomorrow",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": "ready",
                    "slots": {
                        "service_id": "massage",
                        "has_datetime": True
                    }
                }
            }
        ]
    },
    {
        "name": "service_to_time_appointment",
        "booking_mode": "service",
        "turns": [
            {
                "sentence": "schedule massage",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": "needs_clarification",
                    "missing_slots": ["date", "time"]
                }
            },
            {
                "sentence": "friday at 11am",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": "ready",
                    "slots": {
                        "service_id": "massage",
                        "has_datetime": True
                    }
                }
            }
        ]
    },
    {
        "name": "reservation_date_range_followup",
        "booking_mode": "reservation",
        "turns": [
            {
                "sentence": "book deluxe room",
                "expected": {
                    "intent": "CREATE_RESERVATION",
                    "status": "needs_clarification",
                    "missing_slots": ["start_date", "end_date"]
                }
            },
            {
                "sentence": "november 1st to 3rd",
                "expected": {
                    "intent": "CREATE_RESERVATION",
                    "status": "ready",
                    "slots": {
                        "service_id": "room",
                        "date_range": {
                            "start": "2026-11-01",
                            "end": "2026-11-03"
                        }
                    }
                }
            }
        ]
    },
]
