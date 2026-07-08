"""Unit tests for availability fingerprint normalization."""

from core.orchestration.availability_fingerprint import (
    build_availability_fingerprint_slots,
    compute_availability_fingerprint,
    slots_match_availability_fingerprint,
)


def test_fingerprint_ignores_time_selection():
    """Time selection must not change the search-criteria fingerprint."""
    base = {
        "organization_id": 1,
        "service_id": "haircut",
        "date": "2026-01-14",
    }
    fp_without_time = compute_availability_fingerprint(base)
    fp_with_time = compute_availability_fingerprint({**base, "time": "5pm"})
    fp_with_other_time = compute_availability_fingerprint({**base, "time": "14:00"})
    assert fp_without_time == fp_with_time == fp_with_other_time
    assert slots_match_availability_fingerprint(
        {**base, "time": "17:00"}, fp_without_time
    )


def test_date_proposal_override_invalidates_stored_fingerprint():
    stored = compute_availability_fingerprint(
        build_availability_fingerprint_slots(
            {
                "service_id": "haircut",
                "date": "2026-01-16",
            },
            organization_id=1,
            intent_name="CREATE_APPOINTMENT",
        )
    )
    fingerprint_slots = build_availability_fingerprint_slots(
        {"service_id": "haircut", "date": "2026-01-16"},
        organization_id=1,
        date_proposal={"mode": "single_day", "start": "2026-01-17"},
        intent_name="CREATE_APPOINTMENT",
    )
    assert not slots_match_availability_fingerprint(fingerprint_slots, stored)


def test_time_proposal_does_not_invalidate_stored_fingerprint():
    """Selecting a time must not invalidate the search fingerprint."""
    stored = compute_availability_fingerprint(
        build_availability_fingerprint_slots(
            {"service_id": "haircut", "date": "2026-07-09"},
            organization_id=1,
            intent_name="CREATE_APPOINTMENT",
        )
    )
    fingerprint_slots = build_availability_fingerprint_slots(
        {"service_id": "haircut", "date": "2026-07-09", "time": "17:00"},
        organization_id=1,
        intent_name="CREATE_APPOINTMENT",
        time_proposal={"mode": "exact", "value": "5pm"},
        luma_response={
            "time_proposal": {"mode": "exact", "value": "5pm"},
            "facts": {"time": "17:00"},
        },
        session_state={
            "availability_presentation": {"page_index": 1},
            "presented_availability": {
                "search_date": "2026-07-09",
                "slots": [{"starts_at": "2026-07-09T17:00:00Z"}],
            },
        },
    )
    assert slots_match_availability_fingerprint(fingerprint_slots, stored)
    assert "time" not in fingerprint_slots


def test_organization_id_parity_when_planning_slots_omit_org():
    """Stored fingerprint (execution path) must match compare path without org in slots."""
    planning_slots = {"service_id": "haircut", "date": "2026-07-09"}
    session = {
        "date_proposal": {"mode": "single_day", "start": "2026-07-09"},
    }
    stored = compute_availability_fingerprint(
        build_availability_fingerprint_slots(
            planning_slots,
            organization_id=1,
            intent_name="CREATE_APPOINTMENT",
            session_state=session,
        )
    )
    current_slots = build_availability_fingerprint_slots(
        planning_slots,
        organization_id=1,
        intent_name="CREATE_APPOINTMENT",
        luma_response={"intent": {"name": "AVAILABILITY"}, "operation": "browse_next"},
        session_state=session,
    )
    assert slots_match_availability_fingerprint(current_slots, stored)
    assert current_slots.get("organization_id") == 1
    assert "organization_id" not in planning_slots


def test_confirm_continuation_matches_without_time_in_fingerprint():
    stored = compute_availability_fingerprint(
        {
            "organization_id": 1,
            "service_id": "haircut",
            "date": "2026-01-14",
        }
    )
    assert slots_match_availability_fingerprint(
        {
            "organization_id": 1,
            "service_id": "haircut",
            "date": "2026-01-14",
            "time": "2pm",
        },
        stored,
    )
