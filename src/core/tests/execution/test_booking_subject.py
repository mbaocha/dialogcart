"""Focused contract tests for execution-time booking_subject projection."""

from copy import deepcopy
from unittest.mock import Mock

import pytest

from core.execution.booking_subject import (
    BookingSubjectValidationError,
    booking_subject_enabled,
    build_booking_subject,
)
from core.execution.dispatcher import execute


SUBJECT_SCHEMA = {
    "version": 1,
    "fields": [
        {"name": "service", "type": "catalog", "role": "bookable_item"},
        {
            "name": "engine_type",
            "type": "enum",
            "role": "booking_subject",
            "required": True,
        },
        {
            "name": "registration_number",
            "type": "text",
            "role": "booking_subject",
            "required": True,
        },
        {"name": "staff", "type": "catalog", "role": "staff"},
    ],
}


def test_builder_collects_all_tagged_fields_in_schema_order_without_mutation():
    slots = {
        "service_id": 10,
        "engine_type": "petrol",
        "registration_number": "AB12 CDE",
        "staff_id": 7,
        "customer_contact_name": "Ada",
        "date": "2026-08-20",
    }
    original_slots = deepcopy(slots)
    original_schema = deepcopy(SUBJECT_SCHEMA)

    assert build_booking_subject(slots=slots, entity_schema=SUBJECT_SCHEMA) == {
        "engine_type": "petrol",
        "registration_number": "AB12 CDE",
    }
    assert slots == original_slots
    assert SUBJECT_SCHEMA == original_schema


@pytest.mark.parametrize("value", [None, {"nested": True}, ["array"], float("inf")])
def test_builder_rejects_missing_or_invalid_required_tagged_value(value):
    slots = {"engine_type": value, "registration_number": "AB12 CDE"}
    with pytest.raises(BookingSubjectValidationError):
        build_booking_subject(slots=slots, entity_schema=SUBJECT_SCHEMA)


def test_builder_returns_none_when_no_entities_are_tagged():
    assert build_booking_subject(
        slots={"engine_type": "petrol"},
        entity_schema={"version": 1, "fields": []},
    ) is None


def test_optional_enum_and_text_subjects_are_omitted_or_included_by_yaml_name():
    schema = {
        "version": 1,
        "fields": [
            {
                "name": "vehicle_type",
                "type": "enum",
                "role": "booking_subject",
                "required": False,
            },
            {
                "name": "vehicle_notes",
                "type": "text",
                "role": "booking_subject",
                "required": False,
            },
        ],
    }
    assert build_booking_subject(slots={}, entity_schema=schema) is None
    assert build_booking_subject(
        slots={"vehicle_type": "suv", "vehicle_notes": "Rear wheel"},
        entity_schema=schema,
    ) == {"vehicle_type": "suv", "vehicle_notes": "Rear wheel"}


@pytest.mark.parametrize("forbidden", ["__proto__", "constructor", "prototype"])
def test_builder_rejects_forbidden_subject_keys_as_defence_in_depth(forbidden):
    schema = {
        "version": 1,
        "fields": [
            {"name": forbidden, "type": "text", "role": "booking_subject"}
        ],
    }
    with pytest.raises(BookingSubjectValidationError, match="forbidden"):
        build_booking_subject(slots={forbidden: "value"}, entity_schema=schema)


def test_catalog_subject_characterizes_deferred_id_only_projection():
    schema = {
        "version": 1,
        "fields": [
            {"name": "vehicle", "type": "catalog", "role": "booking_subject"}
        ],
    }
    assert build_booking_subject(
        slots={"vehicle_id": "vehicle-42"}, entity_schema=schema
    ) == {"vehicle": "vehicle-42"}


@pytest.mark.parametrize("value", ["1", "true", "TRUE", " yes ", "on"])
def test_booking_subject_gate_enabled_values(monkeypatch, value):
    monkeypatch.setenv("BOOKING_SUBJECT_ENABLED", value)
    assert booking_subject_enabled() is True


@pytest.mark.parametrize("value", ["", "0", "false", "enabled", "off"])
def test_booking_subject_gate_default_and_other_values_are_disabled(monkeypatch, value):
    monkeypatch.setenv("BOOKING_SUBJECT_ENABLED", value)
    assert booking_subject_enabled() is False


def test_confirmed_service_create_sends_subject_and_keeps_staff_top_level(monkeypatch):
    monkeypatch.setenv("BOOKING_SUBJECT_ENABLED", "true")
    client = Mock()
    client.create_booking.return_value = {"booking": {"id": 99}}
    plan = {
        "action": "CONFIRM_APPOINTMENT",
        "intent_name": "CREATE_APPOINTMENT",
        "slots": {
            "organization_id": 1,
            "customer_id": 2,
            "service_id": 10,
            "engine_type": "petrol",
            "registration_number": "AB12 CDE",
            "staff_id": 7,
            "datetime_range": {
                "start": "2026-08-20T10:00:00+01:00",
                "end": "2026-08-20T11:00:00+01:00",
            },
        },
        "sku_to_catalog_id": {10: 10},
        "_entity_schema": SUBJECT_SCHEMA,
    }

    execute(plan, booking_client=client)

    kwargs = client.create_booking.call_args.kwargs
    assert kwargs["booking_subject"] == {
        "engine_type": "petrol",
        "registration_number": "AB12 CDE",
    }
    assert kwargs["staff_id"] == 7
    assert kwargs["start_time"] == "2026-08-20T10:00:00+01:00"
    assert "end_time" not in kwargs
    assert "staff" not in kwargs["booking_subject"]
    assert "booked_for" not in kwargs


def test_disabled_gate_preserves_legacy_service_call(monkeypatch):
    monkeypatch.delenv("BOOKING_SUBJECT_ENABLED", raising=False)
    client = Mock()
    client.create_booking.return_value = {"booking": {"id": 99}}
    plan = {
        "action": "CONFIRM_APPOINTMENT",
        "intent_name": "CREATE_APPOINTMENT",
        "slots": {
            "organization_id": 1,
            "customer_id": 2,
            "service_id": 10,
            "engine_type": "petrol",
            "registration_number": "AB12 CDE",
            "datetime_range": {
                "start": "2026-08-20T10:00:00+01:00",
                "end": "2026-08-20T11:00:00+01:00",
            },
        },
        "sku_to_catalog_id": {10: 10},
        "_entity_schema": SUBJECT_SCHEMA,
    }

    execute(plan, booking_client=client)

    assert "booking_subject" not in client.create_booking.call_args.kwargs


def test_service_booking_hold_uses_finalized_schema_and_slots(monkeypatch):
    monkeypatch.setenv("BOOKING_SUBJECT_ENABLED", "1")
    client = Mock()
    client.create_booking.return_value = {"booking": {"id": 100, "status": "pending"}}
    plan = {
        "action": "CREATE_BOOKING_HOLD",
        "intent_name": "CREATE_APPOINTMENT",
        "slots": {
            "organization_id": 1,
            "customer_id": 2,
            "service_id": 10,
            "engine_type": "ev",
            "registration_number": "XY99 ZZZ",
            "datetime_range": {
                "start": "2026-08-20T10:00:00+01:00",
                "end": "2026-08-20T11:00:00+01:00",
            },
        },
        "_entity_schema": SUBJECT_SCHEMA,
    }

    execute(plan, booking_client=client)

    kwargs = client.create_booking.call_args.kwargs
    assert kwargs["booking_subject"] == {
        "engine_type": "ev",
        "registration_number": "XY99 ZZZ",
    }
    assert "booked_for" not in kwargs


@pytest.mark.parametrize("action", ["CONFIRM_APPOINTMENT", "CREATE_BOOKING_HOLD"])
def test_required_subject_failure_precedes_service_request(monkeypatch, action):
    monkeypatch.setenv("BOOKING_SUBJECT_ENABLED", "yes")
    client = Mock()
    plan = {
        "action": action,
        "intent_name": "CREATE_APPOINTMENT",
        "slots": {
            "organization_id": 1,
            "customer_id": 2,
            "service_id": 10,
            "registration_number": "AB12 CDE",
            "datetime_range": {
                "start": "2026-08-20T10:00:00+01:00",
                "end": "2026-08-20T11:00:00+01:00",
            },
        },
        "sku_to_catalog_id": {10: 10},
        "_entity_schema": SUBJECT_SCHEMA,
    }
    with pytest.raises((BookingSubjectValidationError, ValueError)):
        execute(plan, booking_client=client)
    client.create_booking.assert_not_called()


def test_enabled_gate_omits_empty_subject_instead_of_sending_empty_object(monkeypatch):
    monkeypatch.setenv("BOOKING_SUBJECT_ENABLED", "on")
    client = Mock()
    client.create_booking.return_value = {"booking": {"id": 99}}
    plan = {
        "action": "CONFIRM_APPOINTMENT",
        "intent_name": "CREATE_APPOINTMENT",
        "slots": {
            "organization_id": 1,
            "customer_id": 2,
            "service_id": 10,
            "datetime_range": {
                "start": "2026-08-20T10:00:00+01:00",
                "end": "2026-08-20T11:00:00+01:00",
            },
        },
        "sku_to_catalog_id": {10: 10},
        "_entity_schema": {"version": 1, "fields": []},
    }
    execute(plan, booking_client=client)
    assert "booking_subject" not in client.create_booking.call_args.kwargs


def test_reservation_hold_never_sends_subject_even_when_gate_enabled(monkeypatch):
    monkeypatch.setenv("BOOKING_SUBJECT_ENABLED", "true")
    client = Mock()
    client.create_booking.return_value = {"booking": {"id": 101, "status": "pending"}}
    plan = {
        "action": "CREATE_BOOKING_HOLD",
        "intent_name": "CREATE_RESERVATION",
        "slots": {
            "organization_id": 1,
            "customer_id": 2,
            "item_id": 10,
            "start_date": "2026-08-20",
            "end_date": "2026-08-21",
            "engine_type": "ev",
            "registration_number": "XY99 ZZZ",
        },
        "_entity_schema": SUBJECT_SCHEMA,
    }
    execute(plan, booking_client=client)
    kwargs = client.create_booking.call_args.kwargs
    assert kwargs["booking_type"] == "reservation"
    assert "booking_subject" not in kwargs


def test_existing_hold_id_preserves_idempotency_without_external_request(monkeypatch):
    monkeypatch.setenv("BOOKING_SUBJECT_ENABLED", "true")
    client = Mock()
    plan = {
        "action": "CREATE_BOOKING_HOLD",
        "intent_name": "CREATE_APPOINTMENT",
        "slots": {
            "organization_id": 1,
            "customer_id": 2,
            "booking_id": 777,
            "booking_code": "EXISTING",
        },
        "_entity_schema": SUBJECT_SCHEMA,
    }
    result = execute(plan, booking_client=client)
    client.create_booking.assert_not_called()
    assert result["refs"]["booking_id"] == 777
