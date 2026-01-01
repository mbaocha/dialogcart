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
                "sentence": "friday at 11am",
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
                "sentence": "friday",
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
                "sentence": "january 1st",
                "expected": {
                    "intent": "CREATE_RESERVATION",
                    "status": STATUS_NEEDS_CLARIFICATION,
                    "missing_slots": ["end_date"]
                }
            },
            {
                "sentence": "through january 7th",
                "expected": {
                    "intent": "CREATE_RESERVATION",
                    "status": STATUS_READY,
                    "slots": {
                        "service_id": "presidential room",
                        "date_range": {
                            "start": "2026-01-01",
                            "end": "2026-01-07"
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
                "sentence": "saturday",
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
    {
        "name": "appointment_confirmation",
        "booking_mode": "service",
        "aliases": {
            "massage": "massage",
        },
        "turns": [
            {
                "sentence": "book massage tomorrow at 3pm",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_READY,
                    "slots": {
                        "service_id": "massage",
                        "has_datetime": True
                    },
                    "booking": {
                        "confirmation_state": "pending"
                    }
                }
            },
            {
                "sentence": "yes",
                "expected": {
                    "intent": "CREATE_APPOINTMENT",
                    "status": STATUS_READY,
                    "booking": {
                        "confirmation_state": "confirmed"
                    }
                }
            }
        ]
    },
    {
        "name": "reservation_confirmation",
        "booking_mode": "reservation",
        "aliases": {
            "room": "room",
        },
        "turns": [
            {
                "sentence": "book room from october 5th to october 9th",
                "expected": {
                    "intent": "CREATE_RESERVATION",
                    "status": STATUS_READY,
                    "slots": {
                        "service_id": "room",
                        "date_range": {
                            "start": "2026-10-05",
                            "end": "2026-10-09"
                        }
                    },
                    "booking": {
                        "confirmation_state": "pending"
                    }
                }
            },
            {
                "sentence": "confirm",
                "expected": {
                    "intent": "CREATE_RESERVATION",
                    "status": STATUS_READY,
                    "booking": {
                        "confirmation_state": "confirmed"
                    }
                }
            }
        ]
    },
]
