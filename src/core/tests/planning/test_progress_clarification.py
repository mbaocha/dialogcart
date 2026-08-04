"""Focused tests: Stage 08 progress-step clarification."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.adapters.nlu.entity_schema_builder import build_entity_schema
from core.planning.planner.progress_clarification import (
    resolve_progress_ask,
    select_progress_candidate,
    unresolved_promptables_for_step,
)
from core.planning.pipeline.requests import AttachedRequest
from core.planning.pipeline.stage08_decision_plan import build_decision_plan_from_evidence
from core.planning.pipeline.types import (
    AvailabilityDecision,
    CapabilityDecision,
    ConfirmationDecision,
    SlotTurnState,
    WorkingTurn,
)
from core.policy.intent_policy import evaluate_execution_step_candidates
from core.tests.harness.car_service_catalog import (
    CAR_SERVICE_SERVICES,
    CAR_SERVICE_STAFF,
    FULL_SERVICE_ID,
    OIL_CHANGE_ID,
)


def _car_schema() -> Dict[str, Any]:
    return build_entity_schema(
        "car_service",
        projected_collections={
            "services": CAR_SERVICE_SERVICES,
            "staff": CAR_SERVICE_STAFF,
        },
    )


def _decision(
    *,
    slots: Dict[str, Any],
    missing_slots: List[str],
    ask_next: Optional[str],
    entity_schema: Dict[str, Any],
    promptable_slots: Optional[List[str]] = None,
    declined_slots: Optional[List[str]] = None,
    availability_ready: bool = False,
    awaiting_user_confirmation: bool = False,
    user_confirmation_satisfied: bool = False,
    awaiting_capability: bool = False,
    active_capability: Optional[str] = None,
) -> Any:
    payload = {
        "facts": {},
        "slots": dict(slots),
        "_entity_schema": entity_schema,
    }
    working = WorkingTurn(
        payload=payload,
        effective_collected_slots=dict(slots),
    )
    slot_state = SlotTurnState(
        intent_name="CREATE_APPOINTMENT",
        missing_slots=list(missing_slots),
        effective_collected_slots=dict(slots),
        base_status="READY" if not missing_slots else "NEEDS_CLARIFICATION",
        ask_next=ask_next,
        promptable_slots=list(promptable_slots or []),
        declined_slots=list(declined_slots or []),
    )
    attached = AttachedRequest(
        planning_intent="CREATE_APPOINTMENT",
        turn_operation="PROVIDE_SLOT_VALUE",
        session_reset_occurred=False,
        confirm_booking_continuation=False,
        gate_action=None,
    )
    return build_decision_plan_from_evidence(
        attached_request=attached,
        working_turn=working,
        slot_state=slot_state,
        availability=AvailabilityDecision(availability_ready=availability_ready),
        confirmation=ConfirmationDecision(
            confirmation_state="pending" if awaiting_user_confirmation else None,
            user_confirmation_satisfied=user_confirmation_satisfied,
            awaiting_user_confirmation=awaiting_user_confirmation,
        ),
        capability=CapabilityDecision(
            active_capability=active_capability,
            awaiting_capability=awaiting_capability,
            awaiting_kind="CAPABILITY" if awaiting_capability else None,
        ),
        session_state={},
        organization_id=1,
    )


def test_select_progress_candidate_earliest_fact_eligible_slot_blocked():
    candidates = [
        {
            "id": "SEARCH_AVAILABILITY",
            "missing_slots": ["engine_type"],
            "missing_requirements": [],
        },
        {
            "id": "CONFIRM_APPOINTMENT",
            "missing_slots": ["date", "time", "engine_type", "registration_number"],
            "missing_requirements": [],
        },
    ]
    progress = select_progress_candidate(candidates)
    assert progress is not None
    assert progress["id"] == "SEARCH_AVAILABILITY"
    ask, branch, meta = resolve_progress_ask(
        selected_step=None,
        candidates=candidates,
        promptable_slots=[],
        entity_schema=None,
        default_ask_next="date",
    )
    assert ask == "engine_type"
    assert branch == "progress_step_clarification"
    assert meta and meta["progress_action"] == "SEARCH_AVAILABILITY"


def test_later_commit_does_not_override_exploratory_progress():
    candidates = [
        {
            "id": "SEARCH_AVAILABILITY",
            "missing_slots": ["engine_type"],
            "missing_requirements": [],
        },
        {
            "id": "CONFIRM_APPOINTMENT",
            "missing_slots": ["date"],
            "missing_requirements": [],
        },
    ]
    ask, branch, _ = resolve_progress_ask(
        selected_step=None,
        candidates=candidates,
        promptable_slots=[],
        entity_schema=None,
        default_ask_next="date",
    )
    assert ask == "engine_type"
    assert branch == "progress_step_clarification"


def test_generic_non_search_slot_gated_step():
    """Action-name agnostic: FETCH-style candidate drives ask_next."""
    candidates = [
        {
            "id": "FETCH_BOOKING",
            "missing_slots": ["booking_ref"],
            "missing_requirements": [],
            "optional_slots": [],
            "resolves": ["booking_id"],
        },
        {
            "id": "APPLY_MODIFICATION",
            "missing_slots": ["booking_id", "date"],
            "missing_requirements": ["user_confirmation_satisfied"],
        },
    ]
    ask, branch, meta = resolve_progress_ask(
        selected_step=None,
        candidates=candidates,
        promptable_slots=[],
        entity_schema=None,
        default_ask_next="date",
    )
    assert ask == "booking_ref"
    assert branch == "progress_step_clarification"
    assert meta and meta["progress_action"] == "FETCH_BOOKING"


def test_fact_blocked_commit_does_not_invent_slot_ask():
    candidates = [
        {
            "id": "SEARCH_AVAILABILITY",
            "missing_slots": [],
            "missing_requirements": ["availability_check_required"],
        },
        {
            "id": "CONFIRM_APPOINTMENT",
            "missing_slots": ["registration_number"],
            "missing_requirements": ["user_confirmation_satisfied"],
        },
    ]
    ask, branch, _ = resolve_progress_ask(
        selected_step=None,
        candidates=candidates,
        promptable_slots=[],
        entity_schema=None,
        default_ask_next="registration_number",
    )
    assert ask == "registration_number"
    assert branch is None


def test_candidate_missing_slots_preserve_effective_order():
    schema = _car_schema()
    # Two required availability criteria in declaration order.
    fields = list(schema["fields"])
    fields.insert(
        2,
        {
            "name": "vehicle_size",
            "type": "enum",
            "required": True,
            "availability_criteria": True,
            "values": ["small", "large"],
        },
    )
    schema = {**schema, "fields": fields}
    _, cands = evaluate_execution_step_candidates(
        "CREATE_APPOINTMENT",
        {"service_id": OIL_CHANGE_ID},
        {"availability_check_required": True},
        entity_schema=schema,
    )
    search = next(c for c in cands if c["id"] == "SEARCH_AVAILABILITY")
    assert search["missing_slots"] == ["engine_type", "vehicle_size"]
    ask, branch, _ = resolve_progress_ask(
        selected_step=None,
        candidates=cands,
        promptable_slots=[],
        entity_schema=schema,
        default_ask_next="date",
    )
    assert ask == "engine_type"
    assert branch == "progress_step_clarification"


def test_car_service_asks_engine_type_before_date():
    schema = _car_schema()
    decision = _decision(
        slots={"service_id": FULL_SERVICE_ID},
        missing_slots=["date", "time", "engine_type", "registration_number"],
        ask_next="date",
        entity_schema=schema,
    )
    plan = decision.plan
    assert plan.get("action") is None
    assert plan.get("status") == "NEEDS_CLARIFICATION"
    assert plan.get("ask_next") == "engine_type"
    assert plan.get("awaiting") == "engine_type"
    assert plan.get("missing_slots") == [
        "date",
        "time",
        "engine_type",
        "registration_number",
    ]


def test_salon_searches_immediately_with_service_only():
    salon = {
        "version": 1,
        "fields": [
            {
                "name": "service",
                "type": "catalog",
                "role": "bookable_item",
                "required": True,
                "catalog": {"Haircut": "cut-1"},
            }
        ],
    }
    decision = _decision(
        slots={"service_id": "cut-1"},
        missing_slots=["date", "time"],
        ask_next="date",
        entity_schema=salon,
    )
    plan = decision.plan
    assert plan.get("action") == "SEARCH_AVAILABILITY"
    assert plan.get("ask_next") == "date"


def test_registration_does_not_block_search_but_remains_in_missing():
    schema = _car_schema()
    decision = _decision(
        slots={"service_id": OIL_CHANGE_ID, "engine_type": "petrol"},
        missing_slots=["date", "time", "registration_number"],
        ask_next="date",
        entity_schema=schema,
    )
    plan = decision.plan
    assert plan.get("action") == "SEARCH_AVAILABILITY"
    assert "registration_number" in plan.get("missing_slots")


def test_promptable_defers_selected_progress_step():
    salon = {
        "version": 1,
        "fields": [
            {
                "name": "service",
                "type": "catalog",
                "role": "bookable_item",
                "required": True,
                "catalog": {"Haircut": "cut-1"},
            },
            {
                "name": "staff",
                "type": "catalog",
                "role": "staff",
                "required": False,
                "prompt_if_missing": True,
                "catalog": {"Anna": "a1", "Ben": "b1"},
            },
        ],
    }
    decision = _decision(
        slots={
            "service_id": "cut-1",
            "date": "2026-07-02",
            "time": "10:00",
        },
        missing_slots=[],
        ask_next="staff_id",
        entity_schema=salon,
        promptable_slots=["staff_id"],
    )
    plan = decision.plan
    assert plan.get("action") is None
    assert plan.get("status") == "NEEDS_CLARIFICATION"
    assert plan.get("ask_next") == "staff_id"
    assert plan.get("awaiting") == "staff_id"


def test_promptable_decline_allows_search():
    salon = {
        "version": 1,
        "fields": [
            {
                "name": "service",
                "type": "catalog",
                "role": "bookable_item",
                "required": True,
                "catalog": {"Haircut": "cut-1"},
            },
            {
                "name": "staff",
                "type": "catalog",
                "role": "staff",
                "required": False,
                "prompt_if_missing": True,
                "catalog": {"Anna": "a1", "Ben": "b1"},
            },
        ],
    }
    decision = _decision(
        slots={
            "service_id": "cut-1",
            "date": "2026-07-02",
            "time": "10:00",
        },
        missing_slots=[],
        ask_next=None,
        entity_schema=salon,
        promptable_slots=[],
        declined_slots=["staff_id"],
    )
    assert decision.plan.get("action") == "SEARCH_AVAILABILITY"


def test_confirmation_blocker_not_rewritten_to_slot_ask():
    schema = _car_schema()
    decision = _decision(
        slots={
            "service_id": OIL_CHANGE_ID,
            "engine_type": "diesel",
            "date": "2026-07-02",
            "time": "10:00",
            "registration_number": "AB12CDE",
        },
        missing_slots=[],
        ask_next=None,
        entity_schema=schema,
        availability_ready=True,
        awaiting_user_confirmation=True,
        user_confirmation_satisfied=False,
    )
    plan = decision.plan
    assert plan.get("status") == "AWAITING_CONFIRMATION"
    assert plan.get("awaiting") == "USER_CONFIRMATION"
    # Progress-step clarification must not invent a slot ask over confirmation.
    assert plan.get("ask_next") not in (
        "date",
        "time",
        "engine_type",
        "registration_number",
        "service_id",
    )


def test_unresolved_promptables_for_step_uses_resolves_not_action_name():
    step = {
        "action": "CUSTOM_AVAILABILITY_STEP",
        "optional_slots": [],
        "resolves": ["availability"],
    }
    schema = {
        "version": 1,
        "fields": [
            {
                "name": "staff",
                "type": "catalog",
                "role": "staff",
                "prompt_if_missing": True,
                "catalog": {"A": "1", "B": "2"},
            }
        ],
    }
    assert unresolved_promptables_for_step(step, ["staff_id"], schema) == ["staff_id"]
