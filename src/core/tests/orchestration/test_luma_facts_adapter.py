"""Unit tests for luma_facts_adapter and temporal proposals (Phase 2)."""

from core.orchestration.luma_facts_adapter import (
    facts_to_slots,
    is_flexible_combined_utterance,
    merge_promoted_luma_slots,
)
from core.orchestration.temporal_proposal import (
    build_date_proposal,
    expand_slots_for_planning,
)


def test_is_flexible_combined_requires_same_turn_service_id():
    dc = {"mode": "flexible"}
    assert is_flexible_combined_utterance(dc, {"service_id": "facial"})
    assert not is_flexible_combined_utterance(dc, {})
    assert not is_flexible_combined_utterance(None, {"service_id": "facial"})


def test_facts_to_slots_does_not_promote_dates():
    # Phase 2: facts_to_slots only promotes service_id and booking_id.
    # Dates/times are handled by temporal_proposal (date_constraint no longer a param).
    facts = {
        "service_id": "facial",
        "dates": ["2026-01-19", "2026-01-25"],
        "times": ["15:00"],
    }
    slots = facts_to_slots(facts, intent_name="CREATE_APPOINTMENT")
    assert slots == {"service_id": "facial"}


def test_build_date_proposal_from_facts():
    proposal = build_date_proposal(
        {"dates": ["2026-01-14"]},
        {"mode": "single_day"},
    )
    assert proposal == {"mode": "single_day", "start": "2026-01-14"}


def test_expand_slots_for_planning_uses_proposals():
    expanded = expand_slots_for_planning(
        {"service_id": "haircut"},
        date_proposal={"mode": "single_day", "start": "2026-01-14"},
        time_proposal={"mode": "exact", "value": "14:00"},
        intent_name="CREATE_APPOINTMENT",
    )
    assert expanded["date"] == "2026-01-14"
    assert expanded["time"] == "14:00"


def test_merge_strips_stale_date_when_fix4_applies():
    nested = {
        "service_id": "facial",
        "date": "2026-01-19",
        "date_range": {"start": "2026-01-19", "end": "2026-01-25"},
    }
    merged = merge_promoted_luma_slots(
        nested,
        {"service_id": "facial"},
        {"mode": "flexible"},
        {"service_id": "facial", "dates": ["2026-01-19", "2026-01-25"]},
    )
    assert merged == {"service_id": "facial"}
