"""Focused tests: composed planning requiredness (platform + entity_schema)."""

from core.planning.planner.missing_slots import (
    compose_planning_required_slots,
    derive_ask_next,
    get_planning_required_slots_for_intent,
)
from core.planning.policy.action_policy import load_planning_policy, plan_intent
from core.planning.turn_state import finalize_turn_state


# Salon-like schema: bookable service only, not marked required beyond platform.
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
        },
        {
            "name": "engine_type",
            "type": "enum",
            "description": "Engine type.",
            "values": ["petrol", "diesel", "hybrid", "ev"],
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


def test_salon_requiredness_unchanged_without_business_required():
    policy = load_planning_policy()
    platform = get_planning_required_slots_for_intent("CREATE_APPOINTMENT")
    with_salon = get_planning_required_slots_for_intent(
        "CREATE_APPOINTMENT", entity_schema=SALON_SCHEMA
    )
    assert platform == ["service_id", "date", "time"]
    assert with_salon == ["service_id", "date", "time"]

    plan = plan_intent(
        "CREATE_APPOINTMENT", {}, policy, entity_schema=SALON_SCHEMA
    )
    assert plan["missing_slots"] == ["service_id", "date", "time"]


def test_absent_schema_preserves_platform_requiredness():
    assert get_planning_required_slots_for_intent(
        "CREATE_APPOINTMENT", entity_schema=None
    ) == ["service_id", "date", "time"]
    assert compose_planning_required_slots("CREATE_APPOINTMENT") == [
        "service_id",
        "date",
        "time",
    ]


def test_required_enum_field_appended():
    required = get_planning_required_slots_for_intent(
        "CREATE_APPOINTMENT", entity_schema=MULTI_ENTITY_SCHEMA
    )
    assert "engine_type" in required
    assert required.index("engine_type") > required.index("time")


def test_required_text_field_appended():
    required = get_planning_required_slots_for_intent(
        "CREATE_APPOINTMENT", entity_schema=MULTI_ENTITY_SCHEMA
    )
    assert "registration_number" in required


def test_optional_business_field_not_required():
    required = get_planning_required_slots_for_intent(
        "CREATE_APPOINTMENT", entity_schema=MULTI_ENTITY_SCHEMA
    )
    assert "notes" not in required
    assert "technician" not in required
    assert "staff_id" not in required


def test_business_required_appended_after_platform_in_declaration_order():
    required = compose_planning_required_slots(
        "CREATE_APPOINTMENT", entity_schema=MULTI_ENTITY_SCHEMA
    )
    # Platform first; bookable_item maps to service_id (already platform — not duplicated).
    assert required[:3] == ["service_id", "date", "time"]
    assert required.count("service_id") == 1
    # Declaration order among new business keys: engine_type then registration_number
    assert required[3:] == ["engine_type", "registration_number"]


def test_plan_intent_missing_includes_business_required():
    policy = load_planning_policy()
    plan = plan_intent(
        "CREATE_APPOINTMENT",
        {"service_id": "oil-change", "date": "2026-07-02", "time": "10:00"},
        policy,
        entity_schema=MULTI_ENTITY_SCHEMA,
    )
    assert plan["missing_slots"] == ["engine_type", "registration_number"]
    assert derive_ask_next(plan["missing_slots"]) == "engine_type"


def test_finalize_turn_state_uses_composed_requiredness():
    turn = finalize_turn_state(
        intent_name="CREATE_APPOINTMENT",
        merged_session_slots={
            "service_id": "oil-change",
            "date": "2026-07-02",
            "time": "10:00",
            "engine_type": "diesel",
        },
        planning_context={"entity_schema": MULTI_ENTITY_SCHEMA},
    )
    assert turn["missing_slots"] == ["registration_number"]
    assert turn["status"] == "NEEDS_CLARIFICATION"

    complete = finalize_turn_state(
        intent_name="CREATE_APPOINTMENT",
        merged_session_slots={
            "service_id": "oil-change",
            "date": "2026-07-02",
            "time": "10:00",
            "engine_type": "diesel",
            "registration_number": "AB12 CDE",
        },
        planning_context={"entity_schema": MULTI_ENTITY_SCHEMA},
    )
    assert complete["missing_slots"] == []
    assert complete["status"] == "READY"
