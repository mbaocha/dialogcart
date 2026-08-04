"""Focused tests: schema-aware execution step requiredness."""

from core.planning.planner.missing_slots import (
    compose_execution_step_required_slots,
    compose_planning_required_slots,
)
from core.policy.intent_policy import select_next_execution_step


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

MULTI_ENTITY_SCHEMA = {
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
            "name": "technician",
            "type": "catalog",
            "role": "staff",
            "description": "Preferred technician.",
            "catalog": {"James": "staff-8"},
            "required": True,
        },
        {
            "name": "engine_type",
            "type": "enum",
            "description": "Engine type.",
            "values": ["petrol", "diesel"],
            "required": True,
        },
        {
            "name": "registration_number",
            "type": "text",
            "description": "Vehicle registration.",
            "required": True,
        },
        {
            "name": "notes",
            "type": "text",
            "description": "Optional notes.",
        },
    ],
}

_COMMIT_FLAGS = {
    "availability_ready": True,
    "time_selection_ready": True,
    "user_confirmation_satisfied": True,
    "availability_check_required": False,
}


def _platform_complete_slots() -> dict:
    return {
        "service_id": "oil-change",
        "date": "2026-07-02",
        "time": "10:00",
    }


def test_salon_execution_unchanged_without_business_required():
    # Exploratory SEARCH still needs only service_id
    search = select_next_execution_step(
        "CREATE_APPOINTMENT",
        {"service_id": "premium haircut"},
        {"availability_check_required": True},
        entity_schema=SALON_SCHEMA,
    )
    assert search is not None
    assert search["action"] == "SEARCH_AVAILABILITY"

    # Committing with platform slots only (no extra business required)
    commit = select_next_execution_step(
        "CREATE_APPOINTMENT",
        {
            "service_id": "premium haircut",
            "date": "2026-07-02",
            "time": "10:00",
        },
        _COMMIT_FLAGS,
        entity_schema=SALON_SCHEMA,
    )
    assert commit is not None
    assert commit["action"] == "CONFIRM_APPOINTMENT"

    absent = select_next_execution_step(
        "CREATE_APPOINTMENT",
        {
            "service_id": "premium haircut",
            "date": "2026-07-02",
            "time": "10:00",
        },
        _COMMIT_FLAGS,
        entity_schema=None,
    )
    assert absent is not None
    assert absent["action"] == "CONFIRM_APPOINTMENT"


def test_exploratory_search_requires_availability_criteria_not_all_required():
    """SEARCH requires required∩availability_criteria; ignores other required attrs."""
    # staff is required + role default availability_criteria → gates SEARCH
    blocked = select_next_execution_step(
        "CREATE_APPOINTMENT",
        {"service_id": "oil-change"},
        {"availability_check_required": True},
        entity_schema=MULTI_ENTITY_SCHEMA,
    )
    assert blocked is None

    step = select_next_execution_step(
        "CREATE_APPOINTMENT",
        {"service_id": "oil-change", "staff_id": "staff-8"},
        {"availability_check_required": True},
        entity_schema=MULTI_ENTITY_SCHEMA,
    )
    assert step is not None
    assert step["action"] == "SEARCH_AVAILABILITY"
    assert compose_execution_step_required_slots(
        intent_name="CREATE_APPOINTMENT",
        step_required_slots=["service_id"],
        mode="exploratory",
        entity_schema=MULTI_ENTITY_SCHEMA,
    ) == ["service_id", "staff_id"]
    # engine_type / registration_number are required but not availability_criteria
    assert "engine_type" not in compose_execution_step_required_slots(
        intent_name="CREATE_APPOINTMENT",
        step_required_slots=["service_id"],
        mode="exploratory",
        entity_schema=MULTI_ENTITY_SCHEMA,
    )


def test_committing_blocked_without_required_enum():
    slots = {
        **_platform_complete_slots(),
        "staff_id": "staff-8",
        "registration_number": "AB12 CDE",
    }
    step = select_next_execution_step(
        "CREATE_APPOINTMENT",
        slots,
        _COMMIT_FLAGS,
        entity_schema=MULTI_ENTITY_SCHEMA,
    )
    assert step is None


def test_committing_blocked_without_required_text():
    slots = {
        **_platform_complete_slots(),
        "staff_id": "staff-8",
        "engine_type": "diesel",
    }
    step = select_next_execution_step(
        "CREATE_APPOINTMENT",
        slots,
        _COMMIT_FLAGS,
        entity_schema=MULTI_ENTITY_SCHEMA,
    )
    assert step is None


def test_committing_blocked_without_required_catalog_staff():
    slots = {
        **_platform_complete_slots(),
        "engine_type": "diesel",
        "registration_number": "AB12 CDE",
    }
    step = select_next_execution_step(
        "CREATE_APPOINTMENT",
        slots,
        _COMMIT_FLAGS,
        entity_schema=MULTI_ENTITY_SCHEMA,
    )
    assert step is None


def test_optional_business_field_does_not_block_commit():
    slots = {
        **_platform_complete_slots(),
        "staff_id": "staff-8",
        "engine_type": "diesel",
        "registration_number": "AB12 CDE",
        # notes intentionally absent
    }
    step = select_next_execution_step(
        "CREATE_APPOINTMENT",
        slots,
        _COMMIT_FLAGS,
        entity_schema=MULTI_ENTITY_SCHEMA,
    )
    assert step is not None
    assert step["action"] == "CONFIRM_APPOINTMENT"


def test_execution_and_planning_share_composed_requiredness():
    planning = compose_planning_required_slots(
        "CREATE_APPOINTMENT", entity_schema=MULTI_ENTITY_SCHEMA
    )
    committing = compose_execution_step_required_slots(
        intent_name="CREATE_APPOINTMENT",
        step_required_slots=["service_id", "date", "time"],
        mode="committing",
        entity_schema=MULTI_ENTITY_SCHEMA,
    )
    assert committing == planning
    assert committing == [
        "service_id",
        "date",
        "time",
        "staff_id",
        "engine_type",
        "registration_number",
    ]
