"""Unit tests for luma_facts_adapter and temporal proposals (Phase 3)."""

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
