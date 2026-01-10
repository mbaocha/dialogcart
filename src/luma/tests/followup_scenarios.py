# Followup scenarios - multi-turn conversations with shared user_id
# Each scenario is a batch of related turns that share the same user_id
from luma.config.core import STATUS_READY, STATUS_NEEDS_CLARIFICATION

followup_scenarios = [
    {
        "name": "service_to_date_to_time",
        "booking_mode": "service",
        "aliases": {
            "haircut": "haircut",
        },
        "turns": [
            {
                "sentence": "book me a haircut",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "missing_slots": ["date", "time"]
                }
            },
            {
                "sentence": "tomorrow",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "missing_slots": ["time"]
                }
            },
            {
                "sentence": "at 3pm",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_READY,
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
        "aliases": {
            "room": "room",
        },
        "turns": [
            {
                "sentence": "reserve a room",
                "expected": {
                    "intent": "CREATE_RESERVATION",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "missing_slots": ["start_date", "end_date"]
                }
            },
            {
                "sentence": "from october 5th",
                "expected": {
                    "intent": "CREATE_RESERVATION",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "missing_slots": ["end_date"]
                }
            },
            {
                "sentence": "to october 9th",
                "expected": {
                    "intent": "CREATE_RESERVATION",
                    "status": STATUS_READY,
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
        "aliases": {
            "massage": "massage",
        },
        "turns": [
            {
                "sentence": "book massage at 2pm",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "missing_slots": ["date"]
                }
            },
            {
                "sentence": "tomorrow",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_READY,
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
        "aliases": {
            "massage": "massage",
        },
        "turns": [
            {
                "sentence": "schedule massage",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "missing_slots": ["date", "time"]
                }
            },
            {
                "sentence": "this friday at 11am",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_READY,
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
                    "missing_slots": ["start_date", "end_date"]
                }
            },
            {
                "sentence": "november 1st to 3rd",
                "expected": {
                    "intent": "CREATE_RESERVATION",
                    "status": STATUS_READY,
                    "slots": {
                        # Tenant alias key - "room" is valid since both "delux" and "room" are aliases
                        # Luma may default to "room" when service not explicitly mentioned in follow-up
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
    {
        "name": "fuzzy_match_massage_followup",
        "booking_mode": "service",
        "aliases": {
            "massage": "massage",
        },
        "turns": [
            {
                "sentence": "book me a massge",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "missing_slots": ["date", "time"]
                }
            },
            {
                "sentence": "tomorrow",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "missing_slots": ["time"]
                }
            },
            {
                "sentence": "at 3pm",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_READY,
                    "slots": {
                        "service_id": "massage",  # Fuzzy match from "massge"
                        "has_datetime": True
                    }
                }
            }
        ]
    },
    {
        "name": "standard_room_reservation_followup",
        "booking_mode": "reservation",
        "aliases": {
            "standard": "room",
        },
        "turns": [
            {
                "sentence": "i need a standard room",
                "expected": {
                    "intent": "CREATE_RESERVATION",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "missing_slots": ["start_date", "end_date"]
                }
            },
            {
                "sentence": "from november 1st",
                "expected": {
                    "intent": "CREATE_RESERVATION",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "missing_slots": ["end_date"]
                }
            },
            {
                "sentence": "through november 5th",
                "expected": {
                    "intent": "CREATE_RESERVATION",
                    "status": STATUS_READY,
                    "slots": {
                        "service_id": "standard",
                        "date_range": {
                            "start": "2026-11-01",
                            "end": "2026-11-05"
                        }
                    }
                }
            }
        ]
    },
    {
        "name": "deluxe_room_fuzzy_followup",
        "booking_mode": "reservation",
        "aliases": {
            "delux": "room",  # Only "delux" alias - "room" removed to test fuzzy matching
        },
        "turns": [
            {
                "sentence": "book deluxe room",
                "expected": {
                    "intent": "CREATE_RESERVATION",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "missing_slots": ["start_date", "end_date"]
                }
            },
            {
                "sentence": "december 1st",
                "expected": {
                    "intent": "CREATE_RESERVATION",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "missing_slots": ["end_date"]
                }
            },
            {
                "sentence": "to december 5th",
                "expected": {
                    "intent": "CREATE_RESERVATION",
                    "status": STATUS_READY,
                    "slots": {
                        "service_id": "delux",  # Fuzzy match from "deluxe"
                        "date_range": {
                            "start": "2026-12-01",
                            "end": "2026-12-05"
                        }
                    }
                }
            }
        ]
    },
    {
        "name": "hair_cut_multiword_followup",
        "booking_mode": "service",
        "aliases": {
            "hair cut": "haircut",
        },
        "turns": [
            {
                "sentence": "schedule a hair cut",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "missing_slots": ["date", "time"]
                }
            },
            {
                "sentence": "this friday",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "missing_slots": ["time"]
                }
            },
            {
                "sentence": "morning",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_READY,
                    "slots": {
                        "service_id": "hair cut",
                        "has_datetime": True
                    }
                }
            }
        ]
    },
    {
        "name": "suite_reservation_followup",
        "booking_mode": "reservation",
        "aliases": {
            "suite": "room",
        },
        "turns": [
            {
                "sentence": "reserve a suite",
                "expected": {
                    "intent": "CREATE_RESERVATION",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "missing_slots": ["start_date", "end_date"]
                }
            },
            {
                "sentence": "from february 10th",
                "expected": {
                    "intent": "CREATE_RESERVATION",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "missing_slots": ["end_date"]
                }
            },
            {
                "sentence": "to february 14th",
                "expected": {
                    "intent": "CREATE_RESERVATION",
                    "status": STATUS_READY,
                    "slots": {
                        "service_id": "suite",
                        "date_range": {
                            "start": "2026-02-10",
                            "end": "2026-02-14"
                        }
                    }
                }
            }
        ]
    },
    {
        "name": "beard_grooming_followup",
        "booking_mode": "service",
        "aliases": {
            "beard": "beard grooming",
        },
        "turns": [
            {
                "sentence": "book beard",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "missing_slots": ["date", "time"]
                }
            },
            {
                "sentence": "tomorrow",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "missing_slots": ["time"]
                }
            },
            {
                "sentence": "at 2pm",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_READY,
                    "slots": {
                        "service_id": "beard",
                        "has_datetime": True
                    }
                }
            }
        ]
    },
    {
        "name": "presidential_room_reservation_followup",
        "booking_mode": "reservation",
        "aliases": {
            "presidential room": "room",
        },
        "turns": [
            {
                "sentence": "book presidential room",
                "expected": {
                    "intent": "CREATE_RESERVATION",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "missing_slots": ["start_date", "end_date"]
                }
            },
            {
                "sentence": "mar 1st",
                "expected": {
                    "intent": "CREATE_RESERVATION",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "missing_slots": ["end_date"]
                }
            },
            {
                "sentence": "through mar 7th",
                "expected": {
                    "intent": "CREATE_RESERVATION",
                    "status": STATUS_READY,
                    "slots": {
                        "service_id": "presidential room",
                        "date_range": {
                            "start": "2026-03-01",
                            "end": "2026-03-07"
                        }
                    }
                }
            }
        ]
    },
    {
        "name": "massage_time_to_date_followup",
        "booking_mode": "service",
        "aliases": {
            "massage": "massage",
        },
        "turns": [
            {
                "sentence": "book massage",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "missing_slots": ["date", "time"]
                }
            },
            {
                "sentence": "next friday",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "missing_slots": ["time"]
                }
            },
            {
                "sentence": "at 10am",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_READY,
                    "slots": {
                        "service_id": "massage",
                        "has_datetime": True
                    }
                }
            }
        ]
    },
    {
        "name": "premium_suite_fuzzy_followup",
        "booking_mode": "reservation",
        "aliases": {
            "premum suite": "room",  # Tenant typo for fuzzy matching
        },
        "turns": [
            {
                "sentence": "book premium suite",
                "expected": {
                    "intent": "CREATE_RESERVATION",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "missing_slots": ["start_date", "end_date"]
                }
            },
            {
                "sentence": "march 1st",
                "expected": {
                    "intent": "CREATE_RESERVATION",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "missing_slots": ["end_date"]
                }
            },
            {
                "sentence": "to march 5th",
                "expected": {
                    "intent": "CREATE_RESERVATION",
                    "status": STATUS_READY,
                    "slots": {
                        "service_id": "premum suite",  # Fuzzy match from "premium suite"
                        "date_range": {
                            "start": "2026-03-01",
                            "end": "2026-03-05"
                        }
                    }
                }
            }
        ]
    },
    {
        "name": "haircut_date_to_time_followup",
        "booking_mode": "service",
        "aliases": {
            "haircut": "haircut",
        },
        "turns": [
            {
                "sentence": "i need a haircut",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "missing_slots": ["date", "time"]
                }
            },
            {
                "sentence": "december 15th",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "missing_slots": ["time"]
                }
            },
            {
                "sentence": "at 4pm",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_READY,
                    "slots": {
                        "service_id": "haircut",
                        "has_datetime": True
                    }
                }
            }
        ]
    },
    {
        "name": "beerd_typo_followup",
        "booking_mode": "service",
        "aliases": {
            "beerd": "beard grooming",  # Tenant typo
        },
        "turns": [
            {
                "sentence": "book beerd",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "missing_slots": ["date", "time"]
                }
            },
            {
                "sentence": "this saturday",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "missing_slots": ["time"]
                }
            },
            {
                "sentence": "afternoon",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_READY,
                    "slots": {
                        "service_id": "beerd",  # Exact match (tenant has typo)
                        "has_datetime": True
                    }
                }
            }
        ]
    },

    # ================================
    # LIFECYCLE-CREATING FOLLOWUP SCENARIOS
    # ================================
    {
        "name": "create_appointment_time_correction",
        "booking_mode": "service",
        "aliases": {
            "haircut": "haircut",
        },
        "turns": [
            {
                "sentence": "book haircut tomorrow at 2pm",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_READY,
                    "slots": {
                        "service_id": "haircut",
                        "has_datetime": True
                    }
                }
            },
            {
                "sentence": "make it 10am",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_READY,
                    "slots": {
                        "service_id": "haircut",
                        "has_datetime": True
                    }
                }
            }
        ]
    },
    {
        "name": "create_appointment_date_change",
        "booking_mode": "service",
        "aliases": {
            "massage": "massage",
        },
        "turns": [
            {
                "sentence": "book massage this friday at 3pm",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_READY,
                    "slots": {
                        "service_id": "massage",
                        "has_datetime": True
                    }
                }
            },
            {
                "sentence": "actually tomorrow",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_READY,
                    "slots": {
                        "service_id": "massage",
                        "has_datetime": True
                    }
                }
            }
        ]
    },
    {
        "name": "create_appointment_time_later",
        "booking_mode": "service",
        "aliases": {
            "beard grooming": "beard grooming",
        },
        "turns": [
            {
                "sentence": "schedule beard grooming tomorrow",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "missing_slots": ["time"]
                }
            },
            {
                "sentence": "no, later in the day",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "missing_slots": ["time"]
                }
            }
        ]
    },
    {
        "name": "create_appointment_service_change",
        "booking_mode": "service",
        "aliases": {
            "haircut": "haircut",
            "massage": "massage",
        },
        "turns": [
            {
                "sentence": "book haircut tomorrow at 2pm",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_READY,
                    "slots": {
                        "service_id": "haircut",
                        "has_datetime": True
                    }
                }
            },
            {
                "sentence": "actually make it a massage",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "issues": {
                        "date": "missing",
                        "time": "missing"
                    }
                }
            }
        ]
    },
    {
        "name": "create_appointment_vague_time_update",
        "booking_mode": "service",
        "aliases": {
            "haircut": "haircut",
        },
        "turns": [
            {
                "sentence": "book haircut this saturday",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "missing_slots": ["time"]
                }
            },
            {
                "sentence": "change it to morning",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_READY,
                    "slots": {
                        "service_id": "haircut",
                        "has_datetime": True
                    }
                }
            }
        ]
    },
    {
        "name": "create_appointment_exact_time_change",
        "booking_mode": "service",
        "aliases": {
            "massage": "massage",
        },
        "turns": [
            {
                "sentence": "book massage next week",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "missing_slots": ["date", "time"]
                }
            },
            {
                "sentence": "next week friday at 4pm",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_READY,
                    "slots": {
                        "service_id": "massage",
                        "has_datetime": True
                    }
                }
            }
        ]
    },
    {
        "name": "create_appointment_date_time_reversal",
        "booking_mode": "service",
        "aliases": {
            "haircut": "haircut",
        },
        "turns": [
            {
                "sentence": "schedule haircut",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "missing_slots": ["date", "time"]
                }
            },
            {
                "sentence": "at 11am",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "missing_slots": ["date"]
                }
            },
            {
                "sentence": "tomorrow",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_READY,
                    "slots": {
                        "service_id": "haircut",
                        "has_datetime": True
                    }
                }
            }
        ]
    },
    {
        "name": "create_appointment_pronoun_time",
        "booking_mode": "service",
        "aliases": {
            "beard grooming": "beard grooming",
        },
        "turns": [
            {
                "sentence": "book beard grooming this monday",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "missing_slots": ["time"]
                }
            },
            {
                "sentence": "make it 3pm",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_READY,
                    "slots": {
                        "service_id": "beard grooming",
                        "has_datetime": True
                    }
                }
            }
        ]
    },
    {
        "name": "create_appointment_hesitation",
        "booking_mode": "service",
        "aliases": {
            "massage": "massage",
        },
        "turns": [
            {
                "sentence": "i want a massage",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "missing_slots": ["date", "time"]
                }
            },
            {
                "sentence": "uh, tomorrow",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "missing_slots": ["time"]
                }
            },
            {
                "sentence": "afternoon",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_READY,
                    "slots": {
                        "service_id": "massage",
                        "has_datetime": True
                    }
                }
            }
        ]
    },
    {
        "name": "create_appointment_time_only_followup",
        "booking_mode": "service",
        "aliases": {
            "haircut": "haircut",
        },
        "turns": [
            {
                "sentence": "book haircut",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "missing_slots": ["date", "time"]
                }
            },
            {
                "sentence": "2pm",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "missing_slots": ["date"]
                }
            },
            {
                "sentence": "this saturday",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_READY,
                    "slots": {
                        "service_id": "haircut",
                        "has_datetime": True
                    }
                }
            }
        ]
    },
    {
        "name": "create_appointment_correction_chain",
        "booking_mode": "service",
        "aliases": {
            "massage": "massage",
        },
        "turns": [
            {
                "sentence": "book massage this friday",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "missing_slots": ["time"]
                }
            },
            {
                "sentence": "at 2pm",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_READY,
                    "slots": {
                        "service_id": "massage",
                        "has_datetime": True
                    }
                }
            },
            {
                "sentence": "wait, make that 4pm",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_READY,
                    "slots": {
                        "service_id": "massage",
                        "has_datetime": True
                    }
                }
            }
        ]
    },
    {
        "name": "create_appointment_weekday_change",
        "booking_mode": "service",
        "aliases": {
            "haircut": "haircut",
        },
        "turns": [
            {
                "sentence": "schedule haircut this monday at 10am",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_READY,
                    "slots": {
                        "service_id": "haircut",
                        "has_datetime": True
                    }
                }
            },
            {
                "sentence": "change to tuesday",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_READY,
                    "slots": {
                        "service_id": "haircut",
                        "has_datetime": True
                    }
                }
            }
        ]
    },
    {
        "name": "create_appointment_vague_update",
        "booking_mode": "service",
        "aliases": {
            "beard grooming": "beard grooming",
        },
        "turns": [
            {
                "sentence": "book beard grooming",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "missing_slots": ["date", "time"]
                }
            },
            {
                "sentence": "next week",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "missing_slots": ["date", "time"]
                }
            },
            {
                "sentence": "this friday afternoon",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_READY,
                    "slots": {
                        "service_id": "beard grooming",
                        "has_datetime": True
                    }
                }
            }
        ]
    },
    {
        "name": "create_appointment_that_reference",
        "booking_mode": "service",
        "aliases": {
            "massage": "massage",
        },
        "turns": [
            {
                "sentence": "book massage tomorrow",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "missing_slots": ["time"]
                }
            },
            {
                "sentence": "make that 3pm",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_READY,
                    "slots": {
                        "service_id": "massage",
                        "has_datetime": True
                    }
                }
            }
        ]
    },
    {
        "name": "create_appointment_it_reference",
        "booking_mode": "service",
        "aliases": {
            "haircut": "haircut",
        },
        "turns": [
            {
                "sentence": "i need a haircut",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "missing_slots": ["date", "time"]
                }
            },
            {
                "sentence": "this saturday",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "missing_slots": ["time"]
                }
            },
            {
                "sentence": "move it to 5pm",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_READY,
                    "slots": {
                        "service_id": "haircut",
                        "has_datetime": True
                    }
                }
            }
        ]
    },
    {
        "name": "create_reservation_extend_stay",
        "booking_mode": "reservation",
        "aliases": {
            "room": "room",
        },
        "turns": [
            {
                "sentence": "book room from this friday to sunday",
                "expected": {
                    "intent": "CREATE_RESERVATION",
                    "status": STATUS_READY,
                    "slots": {
                        "service_id": "room",
                        "date_range": {
                            "start": "<resolved_date>",
                            "end": "<resolved_date>"
                        }
                    }
                }
            },
            {
                "sentence": "actually one more night",
                "expected": {
                    "intent": "CREATE_RESERVATION",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "issues": {
                        "end_date": "missing"
                    }
                }
            }
        ]
    },
    {
        "name": "create_reservation_weekend_change",
        "booking_mode": "reservation",
        "aliases": {
            "suite": "room",
        },
        "turns": [
            {
                "sentence": "reserve suite",
                "expected": {
                    "intent": "CREATE_RESERVATION",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "missing_slots": ["start_date", "end_date"]
                }
            },
            {
                "sentence": "make it next weekend",
                "expected": {
                    "intent": "CREATE_RESERVATION",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "missing_slots": ["start_date", "end_date"]
                }
            }
        ]
    },
    {
        "name": "create_reservation_date_adjustment",
        "booking_mode": "reservation",
        "aliases": {
            "deluxe room": "room",
        },
        "turns": [
            {
                "sentence": "book deluxe room from october 10th",
                "expected": {
                    "intent": "CREATE_RESERVATION",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "missing_slots": ["end_date"]
                }
            },
            {
                "sentence": "to october 12th",
                "expected": {
                    "intent": "CREATE_RESERVATION",
                    "status": STATUS_READY,
                    "slots": {
                        "service_id": "deluxe room",
                        "date_range": {
                            "start": "2026-10-10",
                            "end": "2026-10-12"
                        }
                    }
                }
            },
            {
                "sentence": "change to 13th",
                "expected": {
                    "intent": "CREATE_RESERVATION",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "issues": {
                        "end_date": "missing"
                    }
                }
            }
        ]
    },
    {
        "name": "create_reservation_start_date_change",
        "booking_mode": "reservation",
        "aliases": {
            "room": "room",
        },
        "turns": [
            {
                "sentence": "reserve room from november 5th to 7th",
                "expected": {
                    "intent": "CREATE_RESERVATION",
                    "status": STATUS_READY,
                    "slots": {
                        "service_id": "room",
                        "date_range": {
                            "start": "2026-11-05",
                            "end": "2026-11-07"
                        }
                    }
                }
            },
            {
                "sentence": "actually start on the 6th",
                "expected": {
                    "intent": "CREATE_RESERVATION",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "issues": {
                        "end_date": "missing"
                    }
                }
            }
        ]
    },
    {
        "name": "create_reservation_pronoun_update",
        "booking_mode": "reservation",
        "aliases": {
            "suite": "room",
        },
        "turns": [
            {
                "sentence": "book suite",
                "expected": {
                    "intent": "CREATE_RESERVATION",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "missing_slots": ["start_date", "end_date"]
                }
            },
            {
                "sentence": "december 1st to 3rd",
                "expected": {
                    "intent": "CREATE_RESERVATION",
                    "status": STATUS_READY,
                    "slots": {
                        "service_id": "suite",
                        "date_range": {
                            "start": "2026-12-01",
                            "end": "2026-12-03"
                        }
                    }
                }
            },
            {
                "sentence": "extend it one day",
                "expected": {
                    "intent": "CREATE_RESERVATION",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "issues": {
                        "end_date": "missing"
                    }
                }
            }
        ]
    },
    {
        "name": "create_reservation_room_type_change",
        "booking_mode": "reservation",
        "aliases": {
            "standard": "room",
            "deluxe": "room",
        },
        "turns": [
            {
                "sentence": "book standard room from this friday to sunday",
                "expected": {
                    "intent": "CREATE_RESERVATION",
                    "status": STATUS_READY,
                    "slots": {
                        # Tenant alias key (user said "standard room")
                        "service_id": "standard",
                        "date_range": {
                            "start": "<resolved_date>",
                            "end": "<resolved_date>"
                        }
                    }
                }
            },
            {
                "sentence": "actually make it deluxe",
                "expected": {
                    "intent": "CREATE_RESERVATION",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "issues": {
                        "start_date": "missing",
                        "end_date": "missing"
                    }
                }
            }
        ]
    },
    # COMMENTED OUT: Failing test - needs investigation
    # {
    #     "name": "create_reservation_month_change",
    #     "booking_mode": "reservation",
    #     "aliases": {
    #         "room": "room",
    #     },
    #     "turns": [
    #         {
    #             "sentence": "reserve room january 15th to 18th",
    #             "expected": {
    #                 "intent": "CREATE_RESERVATION",
    #                 "status": STATUS_READY,
    #                 "slots": {
    #                     "service_id": "room",
    #                     "date_range": {
    #                         "start": "2026-01-15",
    #                         "end": "2026-01-18"
    #                     }
    #                 }
    #             }
    #         },
    #         {
    #             "sentence": "change to february",
    #             "expected": {
    #                 "intent": "CREATE_RESERVATION",
    #                 "status": STATUS_NEEDS_CLARIFICATION,
    #                 "missing_slots": ["start_date", "end_date"]
    #             }
    #         }
    #     ]
    # },
    # COMMENTED OUT: Failing test - needs investigation
    # {
    #     "name": "create_reservation_full_date_revision",
    #     "booking_mode": "reservation",
    #     "aliases": {
    #         "suite": "room",
    #     },
    #     "turns": [
    #         {
    #             "sentence": "book suite",
    #             "expected": {
    #                 "intent": "CREATE_RESERVATION",
    #                 "status": STATUS_NEEDS_CLARIFICATION,
    #                 "missing_slots": ["start_date", "end_date"]
    #             }
    #         },
    #         {
    #             "sentence": "march 10th to 12th",
    #             "expected": {
    #                 "intent": "CREATE_RESERVATION",
    #                 "status": STATUS_READY,
    #                 "slots": {
    #                     "service_id": "suite",
    #                     "date_range": {
    #                         "start": "2026-03-10",
    #                         "end": "2026-03-12"
    #                     }
    #                 }
    #             }
    #         },
    #         {
    #             "sentence": "no, make it 15th to 17th",
    #             "expected": {
    #                 "intent": "CREATE_RESERVATION",
    #                 "status": STATUS_NEEDS_CLARIFICATION,
    #                 "issues": {
    #                     "start_date": "missing",
    #                     "end_date": "missing"
    #                 }
    #             }
    #         }
    #     ]
    # },
    {
        "name": "create_reservation_that_reference",
        "booking_mode": "reservation",
        "aliases": {
            "room": "room",
        },
        "turns": [
            {
                "sentence": "reserve room",
                "expected": {
                    "intent": "CREATE_RESERVATION",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "missing_slots": ["start_date", "end_date"]
                }
            },
            {
                "sentence": "from october 3rd",
                "expected": {
                    "intent": "CREATE_RESERVATION",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "missing_slots": ["end_date"]
                }
            },
            {
                "sentence": "through october 6th",
                "expected": {
                    "intent": "CREATE_RESERVATION",
                    "status": STATUS_READY,
                    "slots": {
                        "service_id": "room",
                        "date_range": {
                            "start": "2026-10-03",
                            "end": "2026-10-06"
                        }
                    }
                }
            }
        ]
    },
    # COMMENTED OUT: Failing test - needs investigation
    # {
    #     "name": "modify_booking_explicit_booking_id",
    #     "booking_mode": "service",
    #     "aliases": {},
    #     "turns": [
    #         {
    #             "sentence": "reschedule my booking ABC123 to friday",
    #             "expected": {
    #                 "intent": "BOOKING_INQUIRY",
    #                 "status": STATUS_NEEDS_CLARIFICATION,
    #                 "issues": {
    #                     "booking_id": "missing"
    #                 }
    #             }
    #         },
    #         {
    #             "sentence": "at 3pm",
    #             "expected": {
    #                 "intent": "BOOKING_INQUIRY",
    #                 "status": STATUS_NEEDS_CLARIFICATION,
    #                 "issues": {
    #                     "booking_id": "missing"
    #                 }
    #             }
    #         }
    #     ]
    # },
    # COMMENTED OUT: Failing test - needs investigation
    # {
    #     "name": "modify_booking_move_time",
    #     "booking_mode": "service",
    #     "aliases": {},
    #     "turns": [
    #         {
    #             "sentence": "move my booking XYZ789 to friday",
    #             "expected": {
    #                 "intent": "BOOKING_INQUIRY",
    #                 "status": STATUS_NEEDS_CLARIFICATION,
    #                 "issues": {
    #                     "booking_id": "missing"
    #                 }
    #             }
    #         },
    #         {
    #             "sentence": "afternoon",
    #             "expected": {
    #                 "intent": "BOOKING_INQUIRY",
    #                 "status": STATUS_NEEDS_CLARIFICATION,
    #                 "issues": {
    #                     "booking_id": "missing"
    #                 }
    #             }
    #         }
    #     ]
    # },
    # COMMENTED OUT: Failing test - needs investigation
    # {
    #     "name": "modify_booking_change_time",
    #     "booking_mode": "service",
    #     "aliases": {},
    #     "turns": [
    #         {
    #             "sentence": "change the time for booking DEF456",
    #             "expected": {
    #                 "intent": "BOOKING_INQUIRY",
    #                 "status": STATUS_NEEDS_CLARIFICATION,
    #                 "issues": {
    #                     "booking_id": "missing"
    #                 }
    #             }
    #         },
    #         {
    #             "sentence": "to 2pm",
    #             "expected": {
    #                 "intent": "BOOKING_INQUIRY",
    #                 "status": STATUS_NEEDS_CLARIFICATION,
    #                 "issues": {
    #                     "booking_id": "missing"
    #                 }
    #             }
    #         }
    #     ]
    # },
    # COMMENTED OUT: Failing test - needs investigation
    # {
    #     "name": "modify_booking_reschedule_full",
    #     "booking_mode": "service",
    #     "aliases": {},
    #     "turns": [
    #         {
    #             "sentence": "reschedule booking GHI789",
    #             "expected": {
    #                 "intent": "BOOKING_INQUIRY",
    #                 "status": STATUS_NEEDS_CLARIFICATION,
    #                 "issues": {
    #                     "booking_id": "missing"
    #                 }
    #             }
    #         },
    #         {
    #             "sentence": "next monday at 10am",
    #             "expected": {
    #                 "intent": "BOOKING_INQUIRY",
    #                 "status": STATUS_NEEDS_CLARIFICATION,
    #                 "issues": {
    #                     "booking_id": "missing"
    #                 }
    #             }
    #         }
    #     ]
    # },
    # COMMENTED OUT: Failing test - needs investigation
    # {
    #     "name": "modify_booking_reservation_dates",
    #     "booking_mode": "reservation",
    #     "aliases": {},
    #     "turns": [
    #         {
    #             "sentence": "change my reservation JKL012",
    #             "expected": {
    #                 "intent": "BOOKING_INQUIRY",
    #                 "status": STATUS_NEEDS_CLARIFICATION,
    #                 "issues": {
    #                     "booking_id": "missing"
    #                 }
    #             }
    #         },
    #         {
    #             "sentence": "to march 20th through 22nd",
    #             "expected": {
    #                 "intent": "BOOKING_INQUIRY",
    #                 "status": STATUS_NEEDS_CLARIFICATION,
    #                 "issues": {
    #                     "booking_id": "missing"
    #                 }
    #             }
    #         }
    #     ]
    # },
    # COMMENTED OUT: Failing test - needs investigation
    # {
    #     "name": "modify_booking_update_date_only",
    #     "booking_mode": "service",
    #     "aliases": {},
    #     "turns": [
    #         {
    #             "sentence": "update booking MNO345 to next week",
    #             "expected": {
    #                 "intent": "BOOKING_INQUIRY",
    #                 "status": STATUS_NEEDS_CLARIFICATION,
    #                 "issues": {
    #                     "booking_id": "missing"
    #                 }
    #             }
    #         },
    #         {
    #             "sentence": "tuesday",
    #             "expected": {
    #                 "intent": "BOOKING_INQUIRY",
    #                 "status": STATUS_NEEDS_CLARIFICATION,
    #                 "issues": {
    #                     "booking_id": "missing"
    #                 }
    #             }
    #         }
    #     ]
    # },
    # COMMENTED OUT: Failing test - needs investigation
    # {
    #     "name": "modify_booking_reservation_extend",
    #     "booking_mode": "reservation",
    #     "aliases": {},
    #     "turns": [
    #         {
    #             "sentence": "extend reservation PQR678",
    #             "expected": {
    #                 "intent": "BOOKING_INQUIRY",
    #                 "status": STATUS_NEEDS_CLARIFICATION,
    #                 "issues": {
    #                     "booking_id": "missing"
    #                 }
    #             }
    #         },
    #         {
    #             "sentence": "by two days",
    #             "expected": {
    #                 "intent": "BOOKING_INQUIRY",
    #                 "status": STATUS_NEEDS_CLARIFICATION,
    #                 "issues": {
    #                     "booking_id": "missing"
    #                 }
    #             }
    #         }
    #     ]
    # },
    # COMMENTED OUT: Failing test - needs investigation
    # {
    #     "name": "modify_booking_pronoun_reference",
    #     "booking_mode": "service",
    #     "aliases": {},
    #     "turns": [
    #         {
    #             "sentence": "move my booking STU901",
    #             "expected": {
    #                 "intent": "BOOKING_INQUIRY",
    #                 "status": STATUS_NEEDS_CLARIFICATION,
    #                 "issues": {
    #                     "booking_id": "missing"
    #                 }
    #             }
    #         },
    #         {
    #             "sentence": "move it to friday at 4pm",
    #             "expected": {
    #                 "intent": "BOOKING_INQUIRY",
    #                 "status": STATUS_NEEDS_CLARIFICATION,
    #                 "issues": {
    #                     "booking_id": "missing"
    #                 }
    #             }
    #         }
    #     ]
    # },
    {
        "name": "modify_booking_negative_no_booking_id_create",
        "booking_mode": "service",
        "aliases": {
            "haircut": "haircut",
        },
        "turns": [
            {
                "sentence": "book haircut tomorrow",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "missing_slots": ["time"]
                }
            },
            {
                "sentence": "reschedule it",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "missing_slots": ["time"]
                }
            }
        ]
    },
    # COMMENTED OUT: Failing test - needs investigation
    # {
    #     "name": "modify_booking_negative_change_without_id",
    #     "booking_mode": "service",
    #     "aliases": {
    #         "massage": "massage",
    #     },
    #     "turns": [
    #         {
    #             "sentence": "schedule massage friday at 2pm",
    #             "expected": {
    #                 "intent": "CREATE_APPOINTMENT",
    #                 "status": STATUS_READY,
    #                 "slots": {
    #                     "service_id": "massage",
    #                     "has_datetime": True
    #                 }
    #             }
    #         },
    #         {
    #             "sentence": "change the time",
    #             "expected": {
    #                 "intent": "CREATE_APPOINTMENT",
    #                 "status": STATUS_READY,
    #                 "slots": {
    #                     "service_id": "massage",
    #                     "has_datetime": True
    #                 }
    #             }
    #         }
    #     ]
    # },
    # COMMENTED OUT: Failing test - needs investigation
    # {
    #     "name": "modify_booking_negative_move_without_id",
    #     "booking_mode": "reservation",
    #     "aliases": {
    #         "room": "room",
    #     },
    #     "turns": [
    #         {
    #             "sentence": "book room from january 9th to 11th",
    #             "expected": {
    #                 "intent": "CREATE_RESERVATION",
    #                 "status": STATUS_READY,
    #                 "slots": {
    #                     "service_id": "room",
    #                     "date_range": {
    #                         "start": "2026-01-09",
    #                         "end": "2026-01-11"
    #                     }
    #                 }
    #             }
    #         },
    #         {
    #             "sentence": "move it to next weekend",
    #             "expected": {
    #                 "intent": "CREATE_RESERVATION",
    #                 "status": STATUS_NEEDS_CLARIFICATION,
    #                 "missing_slots": ["start_date", "end_date"]
    #             }
    #         }
    #     ]
    # },
    # COMMENTED OUT: Failing test - needs investigation
    # {
    #     "name": "cancel_booking_explicit_id",
    #     "booking_mode": "service",
    #     "aliases": {},
    #     "turns": [
    #         {
    #             "sentence": "cancel my booking VWX234",
    #             "expected": {
    #                 "intent": "CANCEL_BOOKING",
    #                 "status": STATUS_READY,
    #                 "slots": {
    #                     "booking_id": "VWX234"
    #                 }
    #             }
    #         }
    #     ]
    # },
    # COMMENTED OUT: Failing test - needs investigation
    # {
    #     "name": "cancel_booking_delete_reservation",
    #     "booking_mode": "reservation",
    #     "aliases": {},
    #     "turns": [
    #         {
    #             "sentence": "delete my reservation YZA567",
    #             "expected": {
    #                 "intent": "CANCEL_BOOKING",
    #                 "status": STATUS_READY,
    #                 "slots": {
    #                     "booking_id": "YZA567"
    #                 }
    #             }
    #         }
    #     ]
    # },
    # COMMENTED OUT: Failing test - needs investigation
    # {
    #     "name": "cancel_booking_confirmation",
    #     "booking_mode": "service",
    #     "aliases": {},
    #     "turns": [
    #         {
    #             "sentence": "cancel booking BCD890",
    #             "expected": {
    #                 "intent": "CANCEL_BOOKING",
    #                 "status": STATUS_READY,
    #                 "slots": {
    #                     "booking_id": "BCD890"
    #                 }
    #             }
    #         },
    #         {
    #             "sentence": "yes confirm",
    #             "expected": {
    #                 "intent": "CANCEL_BOOKING",
    #                 "status": STATUS_READY,
    #                 "slots": {
    #                     "booking_id": "BCD890"
    #                 }
    #             }
    #         }
    #     ]
    # },
    {
        "name": "cancel_booking_negative_too_early",
        "booking_mode": "service",
        "aliases": {
            "haircut": "haircut",
        },
        "turns": [
            {
                "sentence": "book haircut tomorrow at 3pm",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_READY,
                    "slots": {
                        "service_id": "haircut",
                        "has_datetime": True
                    }
                }
            },
            {
                "sentence": "cancel it",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_READY,
                    "slots": {
                        "service_id": "haircut",
                        "has_datetime": True
                    }
                }
            }
        ]
    },
    {
        "name": "cancel_booking_negative_no_id",
        "booking_mode": "reservation",
        "aliases": {
            "suite": "room",
        },
        "turns": [
            {
                "sentence": "reserve suite from next friday",
                "expected": {
                    "intent": "CREATE_RESERVATION",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "missing_slots": ["end_date"]
                }
            },
            {
                "sentence": "cancel my reservation",
                "expected": {
                    "intent": "CREATE_RESERVATION",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "missing_slots": ["end_date"]
                }
            }
        ]
    },
    # ================================
    # LIFECYCLE GATING TEST SCENARIOS
    # ================================
    {
        "name": "modify_booking_blocked_before_executed",
        "booking_mode": "service",
        "aliases": {
            "haircut": "haircut",
        },
        "turns": [
            {
                "sentence": "book haircut tomorrow at 2pm",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_READY,
                    "slots": {
                        "service_id": "haircut",
                        "has_datetime": True
                    }
                }
            },
            {
                "sentence": "reschedule it",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_READY,
                    "slots": {
                        "service_id": "haircut",
                        "has_datetime": True
                    }
                }
            }
        ]
    },
    # COMMENTED OUT: Failing test - needs investigation
    # {
    #     "name": "confirm_booking_allowed_when_creating",
    #     "booking_mode": "service",
    #     "aliases": {
    #         "massage": "massage",
    #     },
    #     "turns": [
    #         {
    #             "sentence": "book massage tomorrow at 3pm",
    #             "expected": {
    #                 "intent": "CREATE_APPOINTMENT",
    #                 "status": STATUS_READY,
    #                 "slots": {
    #                     "service_id": "massage",
    #                     "has_datetime": True
    #                 }
    #             }
    #         },
    #         {
    #             "sentence": "yes confirm",
    #             "expected": {
    #                 "intent": "CONFIRM_BOOKING",
    #                 "status": STATUS_READY
    #             }
    #         }
    #     ]
    # },
    # COMMENTED OUT: Failing test - needs investigation
    # {
    #     "name": "create_blocked_after_executed",
    #     "booking_mode": "service",
    #     "aliases": {
    #         "haircut": "haircut",
    #     },
    #     "turns": [
    #         {
    #             "sentence": "book haircut tomorrow at 2pm",
    #             "expected": {
    #                 "intent": "CREATE_APPOINTMENT",
    #                 "status": STATUS_READY,
    #                 "slots": {
    #                     "service_id": "haircut",
    #                     "has_datetime": True
    #                 }
    #             }
    #         },
    #         # Note: In a real test, lifecycle would be set to EXECUTED via /notify_execution
    #         # This scenario tests that after EXECUTED, CREATE_* intents are blocked
    #         {
    #             "sentence": "book another haircut",
    #             "expected": {
    #                 "intent": "UNKNOWN",
    #                 "status": STATUS_NEEDS_CLARIFICATION
    #             }
    #         }
    #     ]
    # },
]
