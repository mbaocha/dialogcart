"""Unit tests for availability fingerprint normalization."""

from core.orchestration.availability_fingerprint import (
    compute_availability_fingerprint,
    slots_match_availability_fingerprint,
)


def test_fingerprint_time_normalization_2pm_matches_14_00():
    base = {
        "organization_id": 1,
        "service_id": "haircut",
        "date": "2026-01-14",
    }
    fp_a = compute_availability_fingerprint({**base, "time": "2pm"})
    fp_b = compute_availability_fingerprint({**base, "time": "14:00"})
    assert fp_a == fp_b
    assert slots_match_availability_fingerprint(
        {**base, "time": "14:00"}, fp_a
    )


def test_date_proposal_override_invalidates_stored_fingerprint():
    from core.orchestration.temporal_proposal import expand_slots_for_planning

    stored = compute_availability_fingerprint(
        {
            "organization_id": 1,
            "service_id": "haircut",
            "date": "2026-01-16",
            "time": "14:00",
        }
    )
    fingerprint_slots = expand_slots_for_planning(
        {"service_id": "haircut", "date": "2026-01-16", "time": "14:00", "organization_id": 1},
        date_proposal={"mode": "single_day", "start": "2026-01-17"},
        intent_name="CREATE_APPOINTMENT",
    )
    assert not slots_match_availability_fingerprint(fingerprint_slots, stored)


def test_confirm_continuation_forces_resolved_when_fingerprint_stored():
    stored = compute_availability_fingerprint(
        {
            "organization_id": 1,
            "service_id": "haircut",
            "date": "2026-01-14",
            "time": "14:00",
        }
    )
    # Different string form but same normalized fingerprint
    assert slots_match_availability_fingerprint(
        {
            "organization_id": 1,
            "service_id": "haircut",
            "date": "2026-01-14",
            "time": "2pm",
        },
        stored,
    )
