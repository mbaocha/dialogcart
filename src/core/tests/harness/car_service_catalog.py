"""Test-only car_service catalog (no production data)."""

from __future__ import annotations

from typing import Any, Dict

# Display name → stable id (phrase maps for NLU / entity_schema).
CAR_SERVICE_SERVICES: Dict[str, Any] = {
    "Oil Change": 101,
    "Full Service": 102,
    "Brake Inspection": 103,
}

CAR_SERVICE_STAFF: Dict[str, Any] = {
    "John": 201,
    "Mike": 202,
}

CAR_SERVICE_COLLECTIONS: Dict[str, Dict[str, Any]] = {
    "staff": CAR_SERVICE_STAFF,
}

OIL_CHANGE_ID = CAR_SERVICE_SERVICES["Oil Change"]
FULL_SERVICE_ID = CAR_SERVICE_SERVICES["Full Service"]
JOHN_STAFF_ID = CAR_SERVICE_STAFF["John"]
MIKE_STAFF_ID = CAR_SERVICE_STAFF["Mike"]
