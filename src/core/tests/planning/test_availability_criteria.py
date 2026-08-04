"""Focused tests: schema-driven availability_criteria participation."""

from core.adapters.nlu.entity_schema_builder import (
    build_entity_schema,
    search_criteria_slot_keys_from_entity_schema,
)
from core.config.business_category_loader import clear_business_category_cache
from core.planning.booking_revision import detect_booking_revision
from core.planning.planner.missing_slots import compose_execution_step_required_slots
from core.policy.intent_policy import select_next_execution_step
from core.workflows.availability.fingerprint import compute_availability_fingerprint
from core.workflows.availability.request_adapter import build_service_availability_request


SALON_SCHEMA = {
    "version": 1,
    "fields": [
        {
            "name": "service",
            "type": "catalog",
            "role": "bookable_item",
            "description": "The requested service.",
            "catalog": {"Premium Haircut": "premium haircut"},
        }
    ],
}

HOTEL_SCHEMA = {
    "version": 1,
    "fields": [
        {
            "name": "room_type",
            "type": "catalog",
            "role": "bookable_item",
            "description": "The requested room type.",
            "catalog": {"Deluxe": "deluxe"},
        }
    ],
}

CAR_SCHEMA = {
    "version": 1,
    "fields": [
        {
            "name": "service",
            "type": "catalog",
            "role": "bookable_item",
            "description": "Vehicle service.",
            "catalog": {"Oil Change": "oil-change"},
            "required": True,
        },
        {
            "name": "engine_type",
            "type": "enum",
            "description": "Engine type.",
            "values": ["petrol", "diesel"],
            "required": True,
            "availability_criteria": True,
        },
        {
            "name": "registration_number",
            "type": "text",
            "description": "Vehicle registration.",
            "required": True,
        },
        {
            "name": "staff",
            "type": "catalog",
            "role": "staff",
            "description": "Preferred mechanic.",
            "catalog": {"John": "staff-1"},
            "required": False,
        },
    ],
}


def test_salon_unchanged_without_availability_criteria_flag():
    keys = search_criteria_slot_keys_from_entity_schema(SALON_SCHEMA)
    assert "service_id" in keys
    assert keys == search_criteria_slot_keys_from_entity_schema(
        {
            "version": 1,
            "fields": [
                {
                    **SALON_SCHEMA["fields"][0],
                    # absent flag — role default
                }
            ],
        }
    )
    step = select_next_execution_step(
        "CREATE_APPOINTMENT",
        {"service_id": "premium haircut"},
        {"availability_check_required": True},
        entity_schema=SALON_SCHEMA,
    )
    assert step is not None
    assert step["action"] == "SEARCH_AVAILABILITY"


def test_hotel_unchanged_without_availability_criteria_flag():
    keys = search_criteria_slot_keys_from_entity_schema(HOTEL_SCHEMA)
    assert "service_id" in keys
    assert "engine_type" not in keys


def test_absent_flag_preserves_role_defaults():
    schema = {
        "version": 1,
        "fields": [
            {
                "name": "service",
                "type": "catalog",
                "role": "bookable_item",
                "catalog": {"A": "a"},
            },
            {
                "name": "staff",
                "type": "catalog",
                "role": "staff",
                "catalog": {"B": "b"},
            },
            {
                "name": "notes",
                "type": "text",
                "description": "Notes.",
            },
        ],
    }
    keys = search_criteria_slot_keys_from_entity_schema(schema)
    assert "service_id" in keys
    assert "staff_id" in keys
    assert "notes" not in keys


def test_engine_type_availability_criteria_blocks_search_until_collected():
    without = select_next_execution_step(
        "CREATE_APPOINTMENT",
        {"service_id": "oil-change"},
        {"availability_check_required": True},
        entity_schema=CAR_SCHEMA,
    )
    assert without is None

    required = compose_execution_step_required_slots(
        intent_name="CREATE_APPOINTMENT",
        step_required_slots=["service_id"],
        mode="exploratory",
        entity_schema=CAR_SCHEMA,
    )
    assert "engine_type" in required
    assert "registration_number" not in required

    with_engine = select_next_execution_step(
        "CREATE_APPOINTMENT",
        {"service_id": "oil-change", "engine_type": "diesel"},
        {"availability_check_required": True},
        entity_schema=CAR_SCHEMA,
    )
    assert with_engine is not None
    assert with_engine["action"] == "SEARCH_AVAILABILITY"


def test_registration_number_does_not_block_search():
    step = select_next_execution_step(
        "CREATE_APPOINTMENT",
        {"service_id": "oil-change", "engine_type": "petrol"},
        {"availability_check_required": True},
        entity_schema=CAR_SCHEMA,
    )
    assert step is not None
    assert step["action"] == "SEARCH_AVAILABILITY"
    assert "registration_number" not in search_criteria_slot_keys_from_entity_schema(
        CAR_SCHEMA
    )


def test_changing_engine_type_invalidates_availability():
    session = {
        "slots": {
            "service_id": "oil-change",
            "engine_type": "diesel",
            "registration_number": "AB12CDE",
        }
    }
    revision = detect_booking_revision(
        {
            "facts": {"engine_type": "petrol"},
            "slots": {"engine_type": "petrol"},
            "_entity_schema": CAR_SCHEMA,
        },
        session,
        entity_schema=CAR_SCHEMA,
    )
    assert revision.criteria is True
    assert revision.invalidates_availability is True


def test_changing_registration_number_does_not_invalidate():
    session = {
        "slots": {
            "service_id": "oil-change",
            "engine_type": "diesel",
            "registration_number": "AB12CDE",
        }
    }
    revision = detect_booking_revision(
        {
            "facts": {"registration_number": "ZZ99ZZZ"},
            "slots": {"registration_number": "ZZ99ZZZ"},
            "_entity_schema": CAR_SCHEMA,
        },
        session,
        entity_schema=CAR_SCHEMA,
    )
    assert revision.any is False
    assert revision.invalidates_availability is False


def test_fingerprint_changes_only_for_availability_criteria():
    base = {
        "organization_id": 1,
        "service_id": "oil-change",
        "date": "2026-07-02",
        "engine_type": "diesel",
        "_entity_schema": CAR_SCHEMA,
    }
    fp_base = compute_availability_fingerprint(base, entity_schema=CAR_SCHEMA)
    fp_engine = compute_availability_fingerprint(
        {**base, "engine_type": "petrol"}, entity_schema=CAR_SCHEMA
    )
    fp_reg = compute_availability_fingerprint(
        {**base, "registration_number": "ZZ99ZZZ"}, entity_schema=CAR_SCHEMA
    )
    assert fp_base is not None
    assert fp_engine != fp_base
    assert fp_reg == fp_base


def test_availability_request_contains_configured_criteria_only():
    request = build_service_availability_request(
        {
            "service_id": "oil-change",
            "date": "2026-07-02",
            "engine_type": "diesel",
            "registration_number": "AB12CDE",
            "staff_id": "staff-1",
        },
        organization_id=2,
        api_service_id="oil-change",
        entity_schema=CAR_SCHEMA,
    )
    assert request["service_id"] == "oil-change"
    assert request["date"] == "2026-07-02"
    assert request["extra_params"]["engine_type"] == "diesel"
    assert request["extra_params"]["staff_id"] == "staff-1"
    assert "registration_number" not in request["extra_params"]
    assert "registration_number" not in request["identity"]
    assert request["identity"]["engine_type"] == "diesel"


def test_yaml_car_service_threads_availability_criteria():
    clear_business_category_cache()
    projected = {
        "services": {"Oil Change": "oil-change"},
        "staff": {"John": "staff-1"},
    }
    schema = build_entity_schema("car_service", projected_collections=projected)
    assert schema is not None
    engine = next(f for f in schema["fields"] if f["name"] == "engine_type")
    assert engine.get("availability_criteria") is True
    keys = search_criteria_slot_keys_from_entity_schema(schema)
    assert "engine_type" in keys
    assert "registration_number" not in keys
    assert "staff_id" in keys  # role default
