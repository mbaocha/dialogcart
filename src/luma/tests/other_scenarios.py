# Other intent scenarios (MODIFY_BOOKING, CANCEL_BOOKING, etc.)
other_scenarios = [
    # ────────────────
    # MODIFY/CANCEL — NEEDS_CLARIFICATION (NO BOOKING_ID)
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
        "sentence": "cancel appointment",
        "booking_mode": "service",
        "expected": {
            "intent": "CANCEL_BOOKING",
            "status": "needs_clarification",
            "missing_slots": ["booking_id"]
        }
    },
]

