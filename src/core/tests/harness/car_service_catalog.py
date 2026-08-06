"""Production-shaped test data for the car_service business category."""

from __future__ import annotations

from typing import Any, Dict, List


CAR_SERVICE_SERVICE_RECORDS: List[Dict[str, Any]] = [
    {
        "id": 26,
        "canonical": 26,
        "name": "Executive Oil Change",
        "aliases": ["executive oil change"],
        "duration": 30,
        "price": 95,
        "currency": "GBP",
        "reservation_fee": 10,
        "description": "We use the best oil type for your car.",
        "is_active": True,
    },
    {
        "id": 27,
        "canonical": 27,
        "name": "Premium Full Service",
        "aliases": ["premium full service"],
        "duration": 45,
        "price": 85,
        "currency": "GBP",
        "reservation_fee": 5,
        "description": "A comprehensive full service, premium style.",
        "is_active": True,
    },
    {
        "id": 28,
        "canonical": 28,
        "name": "Brake Pad Change",
        "aliases": ["brake pad change"],
        "duration": 60,
        "price": 25,
        "currency": "GBP",
        "reservation_fee": 5,
        "description": "We supply and fit the right brake pads for your vehicle.",
        "is_active": True,
    },
]

# Language-only phrase map supplied to NLU. Business metadata lives above.
CAR_SERVICE_ALIASES: Dict[str, int] = {
    alias: int(record["id"])
    for record in CAR_SERVICE_SERVICE_RECORDS
    for alias in record["aliases"]
}

CAR_SERVICE_STAFF: Dict[str, Any] = {
    "John": 201,
    "Mike": 202,
}

CAR_SERVICE_COLLECTIONS: Dict[str, Dict[str, Any]] = {
    "staff": CAR_SERVICE_STAFF,
}

CAR_SERVICE_STRUCTURED_CONTEXT: Dict[str, Any] = {
    "business_name": "CarOne",
    "services": [
        {
            "id": record["id"],
            "name": record["name"],
            "type": "service",
            "description": record["description"],
            "config": {
                "duration": record["duration"],
                "price": record["price"],
                "currency": record["currency"],
                "reservation_fee": record["reservation_fee"],
            },
        }
        for record in CAR_SERVICE_SERVICE_RECORDS
    ],
    "hours": {
        "mon": "9am-5pm",
        "tue": "9am-5pm",
        "wed": "9am-5pm",
        "thu": "9am-5pm",
        "fri": "9am-5pm",
        "sat": "closed",
        "sun": "closed",
    },
    "cancellation_policy": {"notice_hours": 24, "fee": "free"},
}

# Compatibility names retained for existing harness imports.
CAR_SERVICE_SERVICES = CAR_SERVICE_ALIASES
OIL_CHANGE_ID = 26
FULL_SERVICE_ID = 27
JOHN_STAFF_ID = CAR_SERVICE_STAFF["John"]
MIKE_STAFF_ID = CAR_SERVICE_STAFF["Mike"]
