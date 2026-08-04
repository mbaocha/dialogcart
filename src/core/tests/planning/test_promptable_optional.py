"""Focused tests for promptable optional business entities."""

from __future__ import annotations

import inspect
from typing import Any, Dict

import core.planning.planner.promptable as promptable_mod
from core.adapters.nlu.entity_schema_builder import (
    bookable_item_slot_key,
    planning_slot_key_for_role,
    required_slot_keys_from_entity_schema,
)
from core.planning.planner.missing_slots import derive_ask_next
from core.planning.planner.promptable import (
    apply_preference_decline,
    catalog_unique_id_count,
    derive_promptable_slots,
    has_meaningful_catalog_choice,
    planning_keys_from_declined_entities,
    unresolved_search_promptables,
)
from core.planning.pipeline.stage04_slots import resolve_slot_turn_state
from core.planning.pipeline.types import WorkingTurn
from core.rendering import response_renderer as rr


SALON_PROMPT_SCHEMA = {
    "version": 1,
    "fields": [
        {
            "name": "service",
            "type": "catalog",
            "role": "bookable_item",
            "description": "Service",
            "catalog": {"Premium Haircut": "premium-haircut"},
        },
        {
            "name": "staff",
            "type": "catalog",
            "role": "staff",
            "required": False,
            "prompt_if_missing": True,
            "description": "Preferred stylist.",
            "catalog": {"Sarah": "staff-1", "James": "staff-2"},
        },
    ],
}

SINGLE_STYLIST_SCHEMA = {
    "version": 1,
    "fields": [
        {
            "name": "service",
            "type": "catalog",
            "role": "bookable_item",
            "catalog": {"Cut": "cut-1"},
        },
        {
            "name": "staff",
            "type": "catalog",
            "role": "staff",
            "prompt_if_missing": True,
            "catalog": {"OnlyOne": "staff-9"},
        },
    ],
}

REQUIRED_WITH_PROMPT_FLAG = {
    "version": 1,
    "fields": [
        {
            "name": "engine_type",
            "type": "enum",
            "required": True,
            "prompt_if_missing": True,
            "values": ["petrol", "diesel"],
        },
        {
            "name": "staff",
            "type": "catalog",
            "role": "staff",
            "required": True,
            "prompt_if_missing": True,
            "catalog": {"A": "1", "B": "2"},
        },
    ],
}


def test_prompt_if_missing_ignored_for_required_entities():
    required = required_slot_keys_from_entity_schema(REQUIRED_WITH_PROMPT_FLAG)
    assert "engine_type" in required
    assert "staff_id" in required
    promptable = derive_promptable_slots(
        REQUIRED_WITH_PROMPT_FLAG,
        {"service_id": "x"},
        [],
    )
    assert promptable == []


def test_prompt_if_missing_forwarded_on_optional_schema_field():
    from core.adapters.nlu import entity_schema_builder as esb

    field = esb._build_entity_field(
        {
            "name": "staff",
            "type": "catalog",
            "role": "staff",
            "required": False,
            "prompt_if_missing": True,
            "description": "Preferred stylist.",
            "catalog": "staff",
        },
        catalog_data=None,
        projected_collections={"staff": {"Sarah": 1, "James": 2}},
    )
    assert field is not None
    assert field.get("prompt_if_missing") is True
    assert "required" not in field


def test_single_stylist_no_prompt():
    field = SINGLE_STYLIST_SCHEMA["fields"][1]
    assert catalog_unique_id_count(field) == 1
    assert has_meaningful_catalog_choice(field) is False
    promptable = derive_promptable_slots(
        SINGLE_STYLIST_SCHEMA,
        {"service_id": "cut-1"},
        [],
    )
    assert promptable == []


def test_stylist_offered_once_when_service_known():
    promptable = derive_promptable_slots(
        SALON_PROMPT_SCHEMA,
        {"service_id": "premium-haircut"},
        [],
    )
    assert promptable == ["staff_id"]
    assert derive_ask_next([], promptable) == "staff_id"
    assert derive_ask_next(["time"], promptable) == "time"


def test_core_maps_declined_entities_to_declined_slots():
    keys = planning_keys_from_declined_entities(SALON_PROMPT_SCHEMA, ["staff"])
    assert keys == ["staff_id"]
    declined = apply_preference_decline(
        declined_slots=[],
        turn_declined_slots=keys,
        slots={"service_id": "premium-haircut"},
    )
    assert declined == ["staff_id"]
    promptable = derive_promptable_slots(
        SALON_PROMPT_SCHEMA,
        {"service_id": "premium-haircut"},
        declined,
    )
    assert promptable == []


def test_regex_decline_helpers_removed():
    assert not hasattr(promptable_mod, "_DECLINE_PATTERNS")
    assert not hasattr(promptable_mod, "is_preference_decline_utterance")


def test_selecting_after_decline_clears_decline():
    declined = apply_preference_decline(
        declined_slots=["staff_id"],
        turn_declined_slots=[],
        slots={"service_id": "premium-haircut", "staff_id": "staff-1"},
    )
    assert declined == []


def test_search_promptables_gate_list():
    promptable = derive_promptable_slots(
        SALON_PROMPT_SCHEMA,
        {"service_id": "premium-haircut", "date": "2026-07-02", "time": "10:00"},
        [],
    )
    assert unresolved_search_promptables(promptable, SALON_PROMPT_SCHEMA) == [
        "staff_id"
    ]


def test_stage04_sets_promptable_ask_next():
    payload: Dict[str, Any] = {
        "facts": {},
        "slots": {
            "service_id": "premium-haircut",
            "date": "2026-07-02",
            "time": "10:00",
        },
        "_entity_schema": SALON_PROMPT_SCHEMA,
        "_effective_collected_slots": {
            "service_id": "premium-haircut",
            "date": "2026-07-02",
            "time": "10:00",
        },
    }
    working = WorkingTurn(
        payload=payload,
        effective_collected_slots=dict(payload["_effective_collected_slots"]),
    )
    state = resolve_slot_turn_state(
        working_turn=working,
        intent_name="CREATE_APPOINTMENT",
        session_state={},
    )
    assert "staff_id" not in state.missing_slots
    assert state.promptable_slots == ["staff_id"]
    assert state.ask_next == "staff_id"


def test_stage04_consumes_declined_entities_not_source_text():
    payload: Dict[str, Any] = {
        "facts": {"staff": None},
        "declined_entities": ["staff"],
        "slots": {
            "service_id": "premium-haircut",
            "date": "2026-07-02",
            "time": "10:00",
        },
        "_entity_schema": SALON_PROMPT_SCHEMA,
        "_source_text": "No preference",
        "_effective_collected_slots": {
            "service_id": "premium-haircut",
            "date": "2026-07-02",
            "time": "10:00",
        },
    }
    working = WorkingTurn(
        payload=payload,
        effective_collected_slots=dict(payload["_effective_collected_slots"]),
    )
    state = resolve_slot_turn_state(
        working_turn=working,
        intent_name="CREATE_APPOINTMENT",
        session_state={"ask_next": "staff_id", "declined_slots": []},
    )
    assert "staff_id" in state.declined_slots
    assert state.promptable_slots == []
    assert state.ask_next is None


def test_stage04_ignores_source_text_without_declined_entities():
    """Utterance alone must not populate declined_slots after regex removal."""
    payload: Dict[str, Any] = {
        "facts": {},
        "slots": {
            "service_id": "premium-haircut",
            "date": "2026-07-02",
            "time": "10:00",
        },
        "_entity_schema": SALON_PROMPT_SCHEMA,
        "_source_text": "No preference",
        "_effective_collected_slots": {
            "service_id": "premium-haircut",
            "date": "2026-07-02",
            "time": "10:00",
        },
    }
    working = WorkingTurn(
        payload=payload,
        effective_collected_slots=dict(payload["_effective_collected_slots"]),
    )
    state = resolve_slot_turn_state(
        working_turn=working,
        intent_name="CREATE_APPOINTMENT",
        session_state={"ask_next": "staff_id", "declined_slots": []},
    )
    assert state.declined_slots == []
    assert state.promptable_slots == ["staff_id"]


def test_renderer_includes_no_preference_for_promptable(monkeypatch):
    captured = {}

    def fake_render_llm(request):
        captured["instruction"] = request.render_instruction
        return "Do you have a preferred stylist?"

    monkeypatch.setattr(rr, "render_llm", fake_render_llm)
    result = {"status": "NEEDS_CLARIFICATION"}
    decision = {
        "intent_name": "CREATE_APPOINTMENT",
        "ask_next": "staff_id",
        "facts": {
            "missing_slots": [],
            "promptable_slots": ["staff_id"],
            "ask_next": "staff_id",
            "_entity_schema": SALON_PROMPT_SCHEMA,
        },
    }
    rr._inject_rendering_text_impl(result, decision, session_state={})
    instruction = captured.get("instruction") or ""
    assert "No preference" in instruction
    assert "staff_id" in instruction


def test_renderer_required_clarification_unchanged(monkeypatch):
    captured = {}

    def fake_render_llm(request):
        captured["instruction"] = request.render_instruction
        return "Which date?"

    monkeypatch.setattr(rr, "render_llm", fake_render_llm)
    result = {"status": "NEEDS_CLARIFICATION"}
    decision = {
        "intent_name": "CREATE_APPOINTMENT",
        "ask_next": "date",
        "facts": {
            "missing_slots": ["date", "time"],
            "promptable_slots": [],
            "ask_next": "date",
        },
    }
    rr._inject_rendering_text_impl(result, decision, session_state={})
    instruction = captured.get("instruction") or ""
    assert "Ask ONLY for these specific missing fields (nothing else): date." in instruction
    assert "No preference" not in instruction


def test_decision_blocks_search_until_stylist_resolved():
    from core.planning.pipeline.stage08_decision_plan import build_decision_plan_from_evidence
    from core.planning.pipeline.types import (
        AvailabilityDecision,
        CapabilityDecision,
        ConfirmationDecision,
        SlotTurnState,
        WorkingTurn,
    )
    from core.planning.pipeline.requests import AttachedRequest

    payload = {
        "facts": {},
        "slots": {
            "service_id": "premium-haircut",
            "date": "2026-07-02",
            "time": "10:00",
        },
        "_entity_schema": SALON_PROMPT_SCHEMA,
    }
    working = WorkingTurn(
        payload=payload,
        effective_collected_slots=dict(payload["slots"]),
    )
    slot_state = SlotTurnState(
        intent_name="CREATE_APPOINTMENT",
        missing_slots=[],
        effective_collected_slots=dict(payload["slots"]),
        base_status="READY",
        ask_next="staff_id",
        promptable_slots=["staff_id"],
        declined_slots=[],
    )
    attached = AttachedRequest(
        planning_intent="CREATE_APPOINTMENT",
        turn_operation="PROVIDE_SLOT_VALUE",
        session_reset_occurred=False,
        confirm_booking_continuation=False,
        gate_action=None,
    )
    decision = build_decision_plan_from_evidence(
        attached_request=attached,
        working_turn=working,
        slot_state=slot_state,
        availability=AvailabilityDecision(availability_ready=False),
        confirmation=ConfirmationDecision(
            confirmation_state=None,
            user_confirmation_satisfied=False,
            awaiting_user_confirmation=False,
        ),
        capability=CapabilityDecision(
            active_capability=None,
            awaiting_capability=False,
        ),
        session_state={},
        organization_id=1,
    )
    assert decision.plan.get("action") != "SEARCH_AVAILABILITY"
    assert decision.plan.get("status") == "NEEDS_CLARIFICATION"
    assert decision.plan.get("ask_next") == "staff_id"


def test_decision_searches_after_decline():
    from core.planning.pipeline.stage08_decision_plan import build_decision_plan_from_evidence
    from core.planning.pipeline.types import (
        AvailabilityDecision,
        CapabilityDecision,
        ConfirmationDecision,
        SlotTurnState,
        WorkingTurn,
    )
    from core.planning.pipeline.requests import AttachedRequest

    payload = {
        "facts": {},
        "slots": {
            "service_id": "premium-haircut",
            "date": "2026-07-02",
            "time": "10:00",
        },
        "_entity_schema": SALON_PROMPT_SCHEMA,
        "date_proposal": {"start": "2026-07-02", "mode": "day"},
        "time_proposal": {"value": "10:00", "mode": "exact"},
    }
    working = WorkingTurn(
        payload=payload,
        effective_collected_slots=dict(payload["slots"]),
    )
    slot_state = SlotTurnState(
        intent_name="CREATE_APPOINTMENT",
        missing_slots=[],
        effective_collected_slots=dict(payload["slots"]),
        base_status="READY",
        ask_next=None,
        promptable_slots=[],
        declined_slots=["staff_id"],
    )
    attached = AttachedRequest(
        planning_intent="CREATE_APPOINTMENT",
        turn_operation="PROVIDE_SLOT_VALUE",
        session_reset_occurred=False,
        confirm_booking_continuation=False,
        gate_action=None,
    )
    decision = build_decision_plan_from_evidence(
        attached_request=attached,
        working_turn=working,
        slot_state=slot_state,
        availability=AvailabilityDecision(availability_ready=False),
        confirmation=ConfirmationDecision(
            confirmation_state=None,
            user_confirmation_satisfied=False,
            awaiting_user_confirmation=False,
        ),
        capability=CapabilityDecision(
            active_capability=None,
            awaiting_capability=False,
        ),
        session_state={},
        organization_id=1,
    )
    assert decision.plan.get("action") == "SEARCH_AVAILABILITY"
    assert "staff_id" not in (decision.plan.get("promptable_slots") or [])


def test_staff_selection_after_decline_marks_revision_path():
    declined = apply_preference_decline(
        declined_slots=["staff_id"],
        turn_declined_slots=[],
        slots={"service_id": "premium-haircut", "staff_id": "staff-1"},
    )
    assert declined == []
    assert (
        derive_promptable_slots(
            SALON_PROMPT_SCHEMA,
            {"service_id": "premium-haircut", "staff_id": "staff-1"},
            declined,
        )
        == []
    )


# --- Bookable_item role prerequisite (schema-driven, not service_id hardcode) ---

CAR_SERVICE_PROMPT_SCHEMA = {
    "version": 1,
    "fields": [
        {
            "name": "service",
            "type": "catalog",
            "role": "bookable_item",
            "required": True,
            "catalog": {"Oil Change": "oil-1", "Brakes": "brake-1"},
        },
        {
            "name": "staff",
            "type": "catalog",
            "role": "staff",
            "prompt_if_missing": True,
            "catalog": {"Alex": "mech-1", "Sam": "mech-2"},
        },
    ],
}

HOTEL_PROMPT_SCHEMA = {
    "version": 1,
    "fields": [
        {
            "name": "room_type",
            "type": "catalog",
            "role": "bookable_item",
            "catalog": {"Deluxe": "deluxe-1", "Suite": "suite-1"},
        },
        {
            "name": "staff",
            "type": "catalog",
            "role": "staff",
            "prompt_if_missing": True,
            "catalog": {"Concierge A": "c-1", "Concierge B": "c-2"},
        },
    ],
}

CUSTOM_BOOKABLE_SCHEMA = {
    "version": 1,
    "fields": [
        {
            "name": "treatment",
            "type": "catalog",
            "role": "bookable_item",
            "catalog": {"Facial": "t-1", "Massage": "t-2"},
        },
        {
            "name": "staff",
            "type": "catalog",
            "role": "staff",
            "prompt_if_missing": True,
            "catalog": {"Lee": "s-1", "Pat": "s-2"},
        },
    ],
}


def test_bookable_role_helper_beauty_salon():
    assert planning_slot_key_for_role(SALON_PROMPT_SCHEMA, "bookable_item") == (
        "service_id"
    )
    assert bookable_item_slot_key(SALON_PROMPT_SCHEMA) == "service_id"
    assert derive_promptable_slots(SALON_PROMPT_SCHEMA, {}, []) == []
    assert derive_promptable_slots(
        SALON_PROMPT_SCHEMA, {"service_id": "premium-haircut"}, []
    ) == ["staff_id"]


def test_bookable_role_helper_car_service():
    assert planning_slot_key_for_role(CAR_SERVICE_PROMPT_SCHEMA, "bookable_item") == (
        "service_id"
    )
    assert derive_promptable_slots(CAR_SERVICE_PROMPT_SCHEMA, {}, []) == []
    assert derive_promptable_slots(
        CAR_SERVICE_PROMPT_SCHEMA, {"service_id": "oil-1"}, []
    ) == ["staff_id"]


def test_bookable_role_helper_hotel_room_type():
    assert planning_slot_key_for_role(HOTEL_PROMPT_SCHEMA, "bookable_item") == (
        "service_id"
    )
    assert derive_promptable_slots(HOTEL_PROMPT_SCHEMA, {}, []) == []
    assert derive_promptable_slots(
        HOTEL_PROMPT_SCHEMA, {"service_id": "deluxe-1"}, []
    ) == ["staff_id"]


def test_bookable_role_absent_schema_compatibility():
    assert planning_slot_key_for_role(None, "bookable_item") is None
    assert bookable_item_slot_key(None) == "service_id"
    assert derive_promptable_slots(None, {"service_id": "x"}, []) == []
    assert derive_promptable_slots({}, {"service_id": "x"}, []) == []


def test_bookable_role_custom_entity_name():
    """Entity name may differ; role bookable_item still drives the prerequisite."""
    assert planning_slot_key_for_role(CUSTOM_BOOKABLE_SCHEMA, "bookable_item") == (
        "service_id"
    )
    assert derive_promptable_slots(CUSTOM_BOOKABLE_SCHEMA, {}, []) == []
    assert derive_promptable_slots(
        CUSTOM_BOOKABLE_SCHEMA, {"service_id": "t-1"}, []
    ) == ["staff_id"]


def test_promptable_prerequisite_has_no_service_id_literal():
    """Planner prerequisite must not hardcode the platform slot name."""
    src = inspect.getsource(promptable_mod.derive_promptable_slots)
    assert "service_id" not in src
    helper_src = inspect.getsource(promptable_mod._bookable_item_collected)
    assert "service_id" not in helper_src
    assert "bookable_item_slot_key" in helper_src
