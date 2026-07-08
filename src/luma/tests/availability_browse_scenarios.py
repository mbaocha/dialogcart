"""HTTP integration scenarios for availability browse operations (Luma → Core contract)."""

availability_browse_scenarios = [
    {
        "sentence": "show more",
        "booking_mode": "service",
        "expected": {
            "intent": "AVAILABILITY",
            "operation": "browse_next",
        },
    },
    {
        "sentence": "show more times",
        "booking_mode": "service",
        "expected": {
            "intent": "AVAILABILITY",
            "operation": "browse_next",
        },
    },
    {
        "sentence": "show additional times",
        "booking_mode": "service",
        "expected": {
            "intent": "AVAILABILITY",
            "operation": "browse_next",
        },
    },
    {
        "sentence": "next page",
        "booking_mode": "service",
        "expected": {
            "intent": "AVAILABILITY",
            "operation": "browse_next",
        },
    },
    {
        "sentence": "more availability",
        "booking_mode": "service",
        "expected": {
            "intent": "AVAILABILITY",
            "operation": "browse_next",
        },
    },
    {
        "sentence": "previous page",
        "booking_mode": "service",
        "expected": {
            "intent": "AVAILABILITY",
            "operation": "browse_previous",
        },
    },
    {
        "sentence": "earlier times",
        "booking_mode": "service",
        "expected": {
            "intent": "AVAILABILITY",
            "operation": "browse_previous",
        },
    },
    {
        "sentence": "go back",
        "booking_mode": "service",
        "expected": {
            "intent": "AVAILABILITY",
            "operation": "browse_previous",
        },
    },
]
