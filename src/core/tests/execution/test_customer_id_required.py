"""Booking dispatcher must not invent customer_id."""

from __future__ import annotations

import pytest

from core.execution.dispatcher import _require_customer_id


def test_require_customer_id_passes_resolved_id():
    assert _require_customer_id({"customer_id": 42}) == 42
    assert _require_customer_id({"customer_id": "7"}) == 7


def test_require_customer_id_rejects_missing():
    with pytest.raises(ValueError, match="customer_id is required"):
        _require_customer_id({})


def test_require_customer_id_rejects_non_positive():
    with pytest.raises(ValueError, match="positive integer"):
        _require_customer_id({"customer_id": 0})
    with pytest.raises(ValueError, match="positive integer"):
        _require_customer_id({"customer_id": -3})


def test_confirm_appointment_does_not_default_to_one():
    """Regression: slots without customer_id must fail closed, not use 1."""
    from unittest.mock import MagicMock

    from core.execution.dispatcher import _execute_confirm_appointment

    booking_client = MagicMock()
    plan = {
        "intent_name": "CREATE_APPOINTMENT",
        "slots": {
            "organization_id": 2,
            "service_id": 26,
            "datetime_range": {
                "start": "2026-08-04T09:00:00+00:00",
                "end": "2026-08-04T10:00:00+00:00",
            },
        },
        "sku_to_catalog_id": {},
    }
    with pytest.raises(ValueError, match="customer_id is required"):
        _execute_confirm_appointment(plan, booking_client)
    booking_client.create_booking.assert_not_called()
