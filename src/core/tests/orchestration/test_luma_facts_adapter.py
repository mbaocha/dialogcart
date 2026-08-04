"""Unit tests for luma_facts_adapter and temporal proposals (Phase 3)."""

from core.adapters.nlu.entity_schema_builder import (
    promotable_slot_keys_from_entity_schema,
)
from core.planning.luma_facts_adapter import (
    facts_to_slots,
    is_flexible_combined_utterance,
    merge_promoted_luma_slots,
)
from core.planning.temporal_proposal import (
    build_date_proposal,
    expand_slots_for_planning,
    proposal_satisfies_planning_time,
)
from core.session.durable_intents import filter_slots_for_intent
from core.session.session_schema_v2 import (
    hydrate_v1_compat_shims,
    normalize_session_to_v2,
    prepare_session_for_load,
    prepare_session_for_persist,
)
from core.session.slot_operations import filter_slots_by_domain


MULTI_ENTITY_SCHEMA = {
    "version": 1,
    "fields": [
        {
            "name": "service",
            "type": "catalog",
            "role": "bookable_item",
            "description": "Vehicle service.",
            "catalog": {"Oil Change": "oil-change"},
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
        },
        {
            "name": "registration_number",
            "type": "text",
            "description": "Vehicle registration.",
        },
    ],
}


def test_is_flexible_combined_requires_same_turn_service_and_temporal():
    temporal = {
        "mode": "flexible",
        "start_date": "2026-01-19",
        "end_date": "2026-01-25",
    }
    assert is_flexible_combined_utterance(temporal, {"service_id": "facial"})
    assert not is_flexible_combined_utterance(
        {"mode": "flexible"}, {"service_id": "facial"}
    )
    assert not is_flexible_combined_utterance(temporal, {})
    assert not is_flexible_combined_utterance(None, {"service_id": "facial"})


def test_facts_to_slots_does_not_promote_dates():
    facts = {
        "service_id": "facial",
        "dates": ["2026-01-19", "2026-01-25"],
        "times": ["15:00"],
    }
    slots = facts_to_slots(facts, intent_name="CREATE_APPOINTMENT")
    assert slots == {"service_id": "facial"}


def test_build_date_proposal_from_temporal():
    proposal = build_date_proposal(
        temporal={
            "mode": "single_day",
            "start_date": "2026-01-14",
        }
    )
    assert proposal == {"mode": "single_day", "start": "2026-01-14"}


def test_build_date_proposal_empty_without_temporal():
    proposal = build_date_proposal({"date": "2026-07-03"})
    assert proposal is None


def test_expand_slots_for_planning_uses_proposals():
    expanded = expand_slots_for_planning(
        {"service_id": "haircut"},
        date_proposal={"mode": "single_day", "start": "2026-01-14"},
        time_proposal={"mode": "exact", "value": "14:00"},
        intent_name="CREATE_APPOINTMENT",
    )
    assert expanded["date"] == "2026-01-14"
    assert expanded["time"] == "14:00"


def test_expand_slots_exact_time_overrides_stale_session_time():
    expanded = expand_slots_for_planning(
        {"service_id": "haircut", "date": "2026-01-14", "time": "14:00"},
        time_proposal={"mode": "exact", "value": "15:00"},
        intent_name="CREATE_APPOINTMENT",
    )
    assert expanded["time"] == "15:00"


def test_expand_slots_date_proposal_overrides_stale_session_date():
    expanded = expand_slots_for_planning(
        {"service_id": "haircut", "date": "2026-01-16", "time": "14:00"},
        date_proposal={"mode": "single_day", "start": "2026-01-17"},
        intent_name="CREATE_APPOINTMENT",
    )
    assert expanded["date"] == "2026-01-17"
    assert expanded["time"] == "14:00"


def test_proposal_satisfies_planning_time_bounded_fuzzy():
    assert proposal_satisfies_planning_time(
        {"mode": "fuzzy", "label": "afternoon", "start": "12:00", "end": "16:59"}
    )
    assert not proposal_satisfies_planning_time({"mode": "fuzzy", "label": "afternoon"})


def test_facts_to_slots_skips_null_booking_and_service_id():
    facts = {"service_id": None, "booking_id": None}
    assert facts_to_slots(facts) == {}


def test_merge_promoted_does_not_overwrite_durable_slots_with_null():
    nested = {
        "booking_id": "ABC12345",
        "date": "2026-01-14",
        "time": "15:00",
    }
    promoted = {"service_id": None, "booking_id": None}
    merged = merge_promoted_luma_slots(nested, promoted)
    assert merged == {
        "booking_id": "ABC12345",
        "date": "2026-01-14",
        "time": "15:00",
    }


def test_merge_strips_stale_date_when_fix4_applies():
    nested = {
        "service_id": "facial",
        "date": "2026-01-19",
        "date_range": {"start": "2026-01-19", "end": "2026-01-25"},
    }
    merged = merge_promoted_luma_slots(
        nested,
        {"service_id": "facial"},
        {"service_id": "facial"},
        temporal={
            "mode": "flexible",
            "start_date": "2026-01-19",
            "end_date": "2026-01-25",
        },
    )
    assert merged == {"service_id": "facial"}


# ── Schema-driven business fact promotion ─────────────────────────────────────


def test_facts_to_slots_service_id_unchanged_without_schema():
    facts = {
        "service_id": "premium haircut",
        "booking_id": "BK1",
        "engine_type": "diesel",
        "hallucinated": "nope",
    }
    assert facts_to_slots(facts) == {
        "service_id": "premium haircut",
        "booking_id": "BK1",
    }


def test_facts_to_slots_promotes_enum_business_entity():
    facts = {"service_id": "oil-change", "engine_type": "diesel"}
    slots = facts_to_slots(facts, entity_schema=MULTI_ENTITY_SCHEMA)
    assert slots["engine_type"] == "diesel"
    assert slots["service_id"] == "oil-change"


def test_facts_to_slots_promotes_text_business_entity():
    facts = {
        "service_id": "oil-change",
        "registration_number": "AB12 CDE",
    }
    slots = facts_to_slots(facts, entity_schema=MULTI_ENTITY_SCHEMA)
    assert slots["registration_number"] == "AB12 CDE"


def test_facts_to_slots_promotes_resolved_staff_id():
    facts = {
        "service_id": "oil-change",
        "technician": "James",
        "staff_id": "staff-8",
    }
    slots = facts_to_slots(facts, entity_schema=MULTI_ENTITY_SCHEMA)
    assert slots["staff_id"] == "staff-8"
    assert "technician" not in slots


def test_facts_to_slots_does_not_promote_catalog_role_entity_names():
    """Business catalog names stay in NLU facts; only canonical ids promote."""
    salon = {
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
                "catalog": {"Sarah": "s-1"},
            },
        ],
    }
    hotel = {
        "version": 1,
        "fields": [
            {
                "name": "room_type",
                "type": "catalog",
                "role": "bookable_item",
                "catalog": {"Deluxe": "deluxe-1"},
            },
        ],
    }
    salon_slots = facts_to_slots(
        {
            "service": "Cut",
            "service_id": "cut-1",
            "staff": "Sarah",
            "staff_id": "s-1",
        },
        entity_schema=salon,
    )
    assert salon_slots == {"service_id": "cut-1", "staff_id": "s-1"}
    hotel_slots = facts_to_slots(
        {"room_type": "Deluxe", "service_id": "deluxe-1"},
        entity_schema=hotel,
    )
    assert hotel_slots == {"service_id": "deluxe-1"}
    assert "room_type" not in hotel_slots


def test_facts_to_slots_rejects_undeclared_arbitrary_fact():
    facts = {
        "service_id": "oil-change",
        "engine_type": "diesel",
        "hallucinated_key": "should-not-promote",
        "dates": ["2026-07-02"],
    }
    slots = facts_to_slots(facts, entity_schema=MULTI_ENTITY_SCHEMA)
    assert "hallucinated_key" not in slots
    assert "dates" not in slots
    assert slots["engine_type"] == "diesel"


def test_null_fact_does_not_erase_durable_slot_via_merge():
    durable = {
        "service_id": "oil-change",
        "engine_type": "petrol",
        "registration_number": "AB12 CDE",
        "staff_id": "staff-8",
    }
    promoted = facts_to_slots(
        {
            "service_id": None,
            "engine_type": None,
            "registration_number": None,
            "staff_id": None,
        },
        entity_schema=MULTI_ENTITY_SCHEMA,
    )
    assert promoted == {}
    merged = merge_promoted_luma_slots(durable, promoted)
    assert merged == durable


def test_absent_entity_schema_preserves_legacy_promotion():
    facts = {
        "service_id": "premium haircut",
        "service": "Premium Haircut",
        "staff_id": "staff-8",
        "engine_type": "diesel",
    }
    assert facts_to_slots(facts, entity_schema=None) == {
        "service_id": "premium haircut"
    }


def test_promotable_keys_are_schema_derived_not_hardcoded():
    keys = promotable_slot_keys_from_entity_schema(MULTI_ENTITY_SCHEMA)
    assert "service" not in keys
    assert "service_id" in keys
    assert "technician" not in keys
    assert "staff_id" in keys
    assert "engine_type" in keys
    assert "registration_number" in keys
    assert "hallucinated_key" not in keys


def test_promotable_keys_hotel_and_car_service_categories():
    hotel = {
        "version": 1,
        "fields": [
            {
                "name": "room_type",
                "type": "catalog",
                "role": "bookable_item",
                "catalog": {"Deluxe": "d-1"},
            },
        ],
    }
    car = {
        "version": 1,
        "fields": [
            {
                "name": "service",
                "type": "catalog",
                "role": "bookable_item",
                "catalog": {"Oil": "oil-1"},
            },
            {
                "name": "staff",
                "type": "catalog",
                "role": "staff",
                "catalog": {"Alex": "m-1"},
            },
            {
                "name": "engine_type",
                "type": "enum",
                "values": ["petrol", "diesel"],
            },
            {"name": "registration_number", "type": "text"},
        ],
    }
    hotel_keys = promotable_slot_keys_from_entity_schema(hotel)
    assert hotel_keys == frozenset({"service_id"})
    assert "room_type" not in hotel_keys
    car_keys = promotable_slot_keys_from_entity_schema(car)
    assert car_keys == frozenset(
        {"service_id", "staff_id", "engine_type", "registration_number"}
    )
    assert "service" not in car_keys
    assert "staff" not in car_keys


def test_domain_filter_preserves_schema_business_slots():
    slots = {
        "service_id": "oil-change",
        "date": "2026-07-02",
        "engine_type": "diesel",
        "registration_number": "AB12 CDE",
        "staff_id": "staff-8",
        "hallucinated_key": "drop-me",
        "start_date": "2026-07-01",  # cross-domain leak
    }
    filtered = filter_slots_by_domain(
        slots,
        "CREATE_APPOINTMENT",
        entity_schema=MULTI_ENTITY_SCHEMA,
    )
    assert filtered["service_id"] == "oil-change"
    assert filtered["engine_type"] == "diesel"
    assert filtered["registration_number"] == "AB12 CDE"
    assert filtered["staff_id"] == "staff-8"
    assert "hallucinated_key" not in filtered
    assert "start_date" not in filtered


def test_domain_filter_absent_schema_preserves_legacy_allowlist():
    slots = {
        "service_id": "premium haircut",
        "date": "2026-07-02",
        "engine_type": "diesel",
        "staff_id": "staff-8",
    }
    filtered = filter_slots_by_domain(slots, "CREATE_APPOINTMENT", entity_schema=None)
    assert filtered == {"service_id": "premium haircut", "date": "2026-07-02"}


def test_business_slots_survive_session_persist_and_reload():
    facts = {
        "service": "Oil Change",
        "service_id": "oil-change",
        "technician": "James",
        "staff_id": "staff-8",
        "engine_type": "diesel",
        "registration_number": "AB12 CDE",
        "hallucinated_key": "drop-me",
    }
    promoted = facts_to_slots(facts, entity_schema=MULTI_ENTITY_SCHEMA)
    domain_kept = filter_slots_by_domain(
        promoted,
        "CREATE_APPOINTMENT",
        entity_schema=MULTI_ENTITY_SCHEMA,
    )
    durable = filter_slots_for_intent("CREATE_APPOINTMENT", domain_kept)
    assert durable["service_id"] == "oil-change"
    assert durable["staff_id"] == "staff-8"
    assert durable["engine_type"] == "diesel"
    assert durable["registration_number"] == "AB12 CDE"
    assert "service" not in durable
    assert "technician" not in durable
    assert "hallucinated_key" not in durable

    working = {
        "intent_name": "CREATE_APPOINTMENT",
        "status": "NEEDS_CLARIFICATION",
        "slots": durable,
        "missing_slots": ["date", "time"],
    }
    persisted = prepare_session_for_persist(working)
    assert persisted["planning"]["slots"]["engine_type"] == "diesel"
    assert persisted["planning"]["slots"]["registration_number"] == "AB12 CDE"
    assert persisted["planning"]["slots"]["staff_id"] == "staff-8"

    reloaded = prepare_session_for_load(persisted)
    assert reloaded["slots"]["engine_type"] == "diesel"
    assert reloaded["slots"]["staff_id"] == "staff-8"
    assert reloaded["slots"]["service_id"] == "oil-change"
    assert reloaded["slots"]["registration_number"] == "AB12 CDE"

    # Compat hydrate path also retains business slots
    hydrated = hydrate_v1_compat_shims(normalize_session_to_v2(persisted))
    assert hydrated["slots"]["engine_type"] == "diesel"
