"""Focused tests: schema-aware revision, fingerprint, clarification, candidates."""

from core.adapters.nlu.entity_schema_builder import (
    catalog_candidates_for_slot,
    description_for_planning_slot,
    search_criteria_slot_keys_from_entity_schema,
)
from core.planning.booking_revision import detect_booking_revision
from core.planning.pipeline.stage03_revision import apply_revision_policy
from core.planning.pipeline.stage09_outcome import (
    derive_clarification_reason_from_missing_slots,
)
from core.planning.pipeline.types import WorkingTurn
from core.rendering.workflow_resume import _slot_ask_clause
from core.session.invalidation import InvalidationTrigger, apply_invalidation
from core.workflows.availability.fingerprint import compute_availability_fingerprint


MULTI_SCHEMA = {
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
            "values": ["petrol", "diesel"],
            "required": True,
        },
        {
            "name": "registration_number",
            "type": "text",
            "description": "Vehicle registration plate.",
            "required": True,
        },
    ],
}


def test_search_criteria_keys_exclude_attribute_entities():
    keys = search_criteria_slot_keys_from_entity_schema(MULTI_SCHEMA)
    assert "service_id" in keys
    assert "staff_id" in keys
    assert "engine_type" not in keys
    assert "registration_number" not in keys


def test_fingerprint_uses_staff_id_not_legacy_staff_key():
    base = {
        "organization_id": 1,
        "service_id": "oil-change",
        "date": "2026-07-02",
    }
    with_staff_id = compute_availability_fingerprint({**base, "staff_id": "staff-8"})
    with_legacy_staff = compute_availability_fingerprint({**base, "staff": "staff-8"})
    without = compute_availability_fingerprint(base)
    assert with_staff_id is not None
    assert with_staff_id == with_legacy_staff
    assert with_staff_id != without


def test_fingerprint_ignores_non_search_attributes():
    base = {
        "organization_id": 1,
        "service_id": "oil-change",
        "date": "2026-07-02",
    }
    with_attrs = compute_availability_fingerprint(
        {
            **base,
            "engine_type": "diesel",
            "registration_number": "AB12 CDE",
        }
    )
    assert with_attrs == compute_availability_fingerprint(base)


def test_salon_fingerprint_unchanged_without_staff():
    fp = compute_availability_fingerprint(
        {
            "organization_id": 1,
            "service_id": "premium haircut",
            "date": "2026-07-02",
        }
    )
    assert fp is not None


def test_service_revision_still_invalidates_availability():
    session = {"slots": {"service_id": "premium haircut", "date": "2026-07-02"}}
    luma = {
        "facts": {"service_id": "flexi haircut"},
        "_entity_schema": MULTI_SCHEMA,
    }
    revision = detect_booking_revision(luma, session, entity_schema=MULTI_SCHEMA)
    assert revision.service is True
    assert revision.invalidates_availability is True


def test_staff_revision_invalidates_availability():
    session = {
        "slots": {
            "service_id": "oil-change",
            "staff_id": "staff-8",
            "date": "2026-07-02",
        }
    }
    luma = {
        "facts": {"service_id": "oil-change", "staff_id": "staff-9"},
        "slots": {"service_id": "oil-change", "staff_id": "staff-9"},
        "_entity_schema": MULTI_SCHEMA,
    }
    revision = detect_booking_revision(luma, session, entity_schema=MULTI_SCHEMA)
    assert revision.criteria is True
    assert revision.service is False
    assert revision.invalidates_availability is True

    state = {
        "slots": dict(session["slots"]),
        "availability_fingerprint": "old-fp",
        "last_execution_result": {"slots": []},
        "confirmation_state": "pending",
    }
    apply_invalidation(
        state, InvalidationTrigger.BOOKING_REVISION, revision=revision
    )
    assert "availability_fingerprint" not in state
    assert state["slots"].get("staff_id") is None


def test_attribute_change_does_not_invalidate_availability():
    session = {
        "slots": {
            "service_id": "oil-change",
            "engine_type": "petrol",
            "registration_number": "AB12 CDE",
        }
    }
    luma = {
        "facts": {
            "service_id": "oil-change",
            "engine_type": "diesel",
            "registration_number": "ZZ99 ZZZ",
        },
        "_entity_schema": MULTI_SCHEMA,
    }
    revision = detect_booking_revision(luma, session, entity_schema=MULTI_SCHEMA)
    assert revision.any is False
    assert revision.invalidates_availability is False


def test_stage03_staff_revision_sets_availability_flag():
    working = WorkingTurn(
        payload={
            "facts": {"service_id": "oil-change", "staff_id": "staff-9"},
            "slots": {"service_id": "oil-change", "staff_id": "staff-9"},
            "_entity_schema": MULTI_SCHEMA,
            "_effective_collected_slots": {
                "service_id": "oil-change",
                "staff_id": "staff-9",
            },
        },
        effective_collected_slots={
            "service_id": "oil-change",
            "staff_id": "staff-9",
        },
    )
    session = {
        "slots": {
            "service_id": "oil-change",
            "staff_id": "staff-8",
            "date": "2026-07-02",
        }
    }
    result = apply_revision_policy(working, session)
    assert result.revision.criteria is True
    assert working.payload.get("_revision_invalidated_availability") is True
    assert working.payload["slots"].get("staff_id") == "staff-9"


def test_clarification_service_id_reason_unchanged():
    assert derive_clarification_reason_from_missing_slots(["service_id"]) == (
        "MISSING_SERVICE"
    )


def test_clarification_business_slot_uses_generic_reason():
    assert derive_clarification_reason_from_missing_slots(["engine_type"]) == (
        "NEEDS_CLARIFICATION"
    )


def test_slot_ask_clause_uses_schema_description_and_candidates():
    desc = description_for_planning_slot(MULTI_SCHEMA, "registration_number")
    assert "registration" in desc.lower()
    clause = _slot_ask_clause(
        "registration_number", candidates=[], description=desc
    )
    assert "Vehicle registration plate" in clause

    staff_clause = _slot_ask_clause(
        "staff_id",
        candidates=["James", "Alex"],
        description=description_for_planning_slot(MULTI_SCHEMA, "staff_id"),
    )
    assert "James" in staff_clause
    assert "Preferred technician" in staff_clause


def test_salon_ask_clause_unchanged():
    assert "service" in _slot_ask_clause("service_id", candidates=[]).lower()
    with_opts = _slot_ask_clause(
        "service_id", candidates=["premium haircut", "flexi haircut"]
    )
    assert "premium haircut" in with_opts


def test_catalog_candidates_for_staff_slot():
    sources = {
        "technician_candidates": ["James", "Alex"],
        "service_candidates": ["oil-change"],
    }
    assert catalog_candidates_for_slot(
        sources, "staff_id", entity_schema=MULTI_SCHEMA
    ) == ["James", "Alex"]
    assert catalog_candidates_for_slot(
        sources, "service_id", entity_schema=MULTI_SCHEMA
    ) == ["oil-change"]


def test_absent_schema_revision_matches_legacy_service_only():
    session = {"slots": {"service_id": "a", "date": "2026-07-02"}}
    luma = {"facts": {"service_id": "b"}}
    revision = detect_booking_revision(luma, session, entity_schema=None)
    assert revision.service is True
    assert revision.criteria is False
    assert revision.invalidates_availability is True
