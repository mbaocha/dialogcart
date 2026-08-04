"""Ask-order ownership: missing_slots preserve required_slots; ask_next is first missing."""

from core.planning.planner.missing_slots import (
    derive_ask_next,
    get_planning_required_slots_for_intent,
)
from core.planning.policy.action_policy import load_planning_policy, plan_intent


def test_create_appointment_missing_slots_preserve_required_order():
    policy = load_planning_policy()
    plan = plan_intent("CREATE_APPOINTMENT", {}, policy)
    required = get_planning_required_slots_for_intent("CREATE_APPOINTMENT")

    assert required == ["service_id", "date", "time"]
    assert plan["missing_slots"] == ["service_id", "date", "time"]
    assert plan["missing_slots"] != sorted(required)


def test_ask_next_is_first_missing_required_slot():
    missing = ["service_id", "date", "time"]
    assert derive_ask_next(missing) == "service_id"
    assert derive_ask_next(["date", "time"]) == "date"
    assert derive_ask_next(["time"]) == "time"
    assert derive_ask_next([]) is None


def test_cold_start_ask_order_service_then_date_then_time():
    policy = load_planning_policy()
    cold = plan_intent("CREATE_APPOINTMENT", {}, policy)
    assert derive_ask_next(cold["missing_slots"]) == "service_id"

    after_service = plan_intent(
        "CREATE_APPOINTMENT", {"service_id": "premium haircut"}, policy
    )
    assert after_service["missing_slots"] == ["date", "time"]
    assert derive_ask_next(after_service["missing_slots"]) == "date"

    after_date = plan_intent(
        "CREATE_APPOINTMENT",
        {"service_id": "premium haircut", "date": "2026-07-24"},
        policy,
    )
    assert after_date["missing_slots"] == ["time"]
    assert derive_ask_next(after_date["missing_slots"]) == "time"
