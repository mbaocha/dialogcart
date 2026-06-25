"""Unit tests for Phase 2 temporal proposal execution helpers.

Covers:
- slots_for_availability_search  (with/without proposals, with/without confirmed slots)
- apply_confirmed_datetime
- resolve_execution_proposals  (priority order)
- Multi-turn proposal durability: date_proposal persists when next turn provides only time
- Fuzzy time_proposal durability: survives when next turn provides date
- apply_time_constraint_to_missing_slots (exact vs fuzzy vs None)
"""

import pytest
from core.orchestration.temporal_proposal import (
    apply_confirmed_datetime,
    apply_time_constraint_to_missing_slots,
    datetime_range_from_availability_result,
    resolve_execution_proposals,
    slots_for_availability_search,
)


# ---------------------------------------------------------------------------
# slots_for_availability_search
# ---------------------------------------------------------------------------


class TestSlotsForAvailabilitySearch:
    def _date_p(self, start, end=None):
        p = {"mode": "single_day" if end is None else "range", "start": start}
        if end:
            p["end"] = end
        return p

    def _time_p(self, value=None, label=None):
        if label:
            return {"mode": "fuzzy", "label": label}
        return {"mode": "exact", "value": value}

    def test_no_proposals_passthrough(self):
        slots = {"service_id": "haircut"}
        result = slots_for_availability_search(slots)
        assert result == {"service_id": "haircut"}

    def test_date_proposal_fills_missing_date(self):
        slots = {"service_id": "haircut"}
        result = slots_for_availability_search(slots, self._date_p("2026-01-14"))
        assert result["date"] == "2026-01-14"
        assert "date_range" not in result

    def test_range_proposal_fills_date_and_date_range(self):
        slots = {"service_id": "haircut"}
        result = slots_for_availability_search(slots, self._date_p("2026-01-19", "2026-01-25"))
        assert result["date"] == "2026-01-19"
        assert result["date_range"] == {"start": "2026-01-19", "end": "2026-01-25"}

    def test_confirmed_date_not_overwritten_by_proposal(self):
        slots = {"service_id": "haircut", "date": "2026-01-14"}
        result = slots_for_availability_search(slots, self._date_p("2026-01-20"))
        assert result["date"] == "2026-01-14"  # confirmed wins

    def test_exact_time_proposal_fills_missing_time(self):
        slots = {"service_id": "haircut", "date": "2026-01-14"}
        result = slots_for_availability_search(slots, time_proposal=self._time_p("15:00"))
        assert result["time"] == "15:00"

    def test_fuzzy_time_proposal_fills_label(self):
        slots = {"service_id": "haircut", "date": "2026-01-14"}
        result = slots_for_availability_search(slots, time_proposal=self._time_p(label="afternoon"))
        assert result["time"] == "afternoon"

    def test_confirmed_time_not_overwritten_by_proposal(self):
        slots = {"service_id": "haircut", "date": "2026-01-14", "time": "10:00"}
        result = slots_for_availability_search(slots, time_proposal=self._time_p("15:00"))
        assert result["time"] == "10:00"  # confirmed wins

    def test_does_not_mutate_input(self):
        slots = {"service_id": "haircut"}
        original = dict(slots)
        slots_for_availability_search(slots, self._date_p("2026-01-14"))
        assert slots == original


# ---------------------------------------------------------------------------
# apply_confirmed_datetime
# ---------------------------------------------------------------------------


class TestApplyConfirmedDatetime:
    def test_single_day_proposal_sets_date(self):
        confirmed = apply_confirmed_datetime(
            {"service_id": "haircut"},
            date_proposal={"mode": "single_day", "start": "2026-01-14"},
        )
        assert confirmed["date"] == "2026-01-14"
        assert "date_range" not in confirmed

    def test_range_proposal_sets_date_and_date_range(self):
        confirmed = apply_confirmed_datetime(
            {"service_id": "haircut"},
            date_proposal={"mode": "range", "start": "2026-01-19", "end": "2026-01-25"},
        )
        assert confirmed["date"] == "2026-01-19"
        assert confirmed["date_range"] == {"start": "2026-01-19", "end": "2026-01-25"}

    def test_exact_time_proposal_sets_time(self):
        confirmed = apply_confirmed_datetime(
            {"service_id": "haircut"},
            time_proposal={"mode": "exact", "value": "15:00"},
        )
        assert confirmed["time"] == "15:00"

    def test_fuzzy_time_proposal_does_not_set_confirmed_time(self):
        confirmed = apply_confirmed_datetime(
            {"service_id": "haircut", "time": "afternoon"},
            time_proposal={"mode": "fuzzy", "label": "afternoon"},
        )
        assert "time" not in confirmed

    def test_no_proposals_passthrough(self):
        slots = {"service_id": "haircut"}
        confirmed = apply_confirmed_datetime(slots)
        assert confirmed == {"service_id": "haircut"}

    def test_does_not_mutate_input(self):
        slots = {"service_id": "haircut"}
        original = dict(slots)
        apply_confirmed_datetime(slots, date_proposal={"mode": "single_day", "start": "2026-01-14"})
        assert slots == original


# ---------------------------------------------------------------------------
# resolve_execution_proposals — priority order
# ---------------------------------------------------------------------------


class TestResolveExecutionProposals:
    def _plan(self, **kw):
        return kw

    def _session(self, **kw):
        return kw

    def test_plan_takes_priority_over_session(self):
        plan = self._plan(
            date_proposal={"mode": "single_day", "start": "2026-01-14"},
            time_proposal={"mode": "exact", "value": "15:00"},
        )
        session = self._session(
            date_proposal={"mode": "single_day", "start": "2026-01-20"},
            time_proposal={"mode": "exact", "value": "10:00"},
        )
        result = resolve_execution_proposals(plan, session)
        assert result["date_proposal"]["start"] == "2026-01-14"
        assert result["time_proposal"]["value"] == "15:00"

    def test_falls_back_to_session_when_plan_empty(self):
        plan = {}
        session = self._session(date_proposal={"mode": "single_day", "start": "2026-01-14"})
        result = resolve_execution_proposals(plan, session)
        assert result["date_proposal"]["start"] == "2026-01-14"

    def test_falls_back_to_session_facts(self):
        plan = {}
        session = {"facts": {"date_proposal": {"mode": "single_day", "start": "2026-01-16"}}}
        result = resolve_execution_proposals(plan, session)
        assert result["date_proposal"]["start"] == "2026-01-16"

    def test_returns_none_when_no_proposals(self):
        result = resolve_execution_proposals({}, {})
        assert result["date_proposal"] is None
        assert result["time_proposal"] is None

    def test_partial_plan_fills_gap_from_session(self):
        plan = {"date_proposal": {"mode": "single_day", "start": "2026-01-14"}}
        session = {"time_proposal": {"mode": "exact", "value": "15:00"}}
        result = resolve_execution_proposals(plan, session)
        assert result["date_proposal"]["start"] == "2026-01-14"
        assert result["time_proposal"]["value"] == "15:00"


# ---------------------------------------------------------------------------
# Multi-turn durability: date_proposal persists when next turn gives only time
# ---------------------------------------------------------------------------


class TestMultiTurnProposalDurability:
    """
    Simulates what resolve_execution_proposals sees across turns by checking
    that session_state carries the date_proposal from turn 1 into turn 2.
    """

    def test_date_proposal_survives_time_only_followup(self):
        # Turn 1 state: user said "book massage tomorrow" → date_proposal set in session
        turn1_session = {
            "date_proposal": {"mode": "single_day", "start": "2026-01-14"},
        }
        # Turn 2 plan: user said "at 3pm" → NLU gives time_proposal only; no new date
        turn2_plan = {
            "time_proposal": {"mode": "exact", "value": "15:00"},
        }
        result = resolve_execution_proposals(turn2_plan, turn1_session)
        assert result["date_proposal"]["start"] == "2026-01-14", "date from turn 1 must survive"
        assert result["time_proposal"]["value"] == "15:00", "time from turn 2 must be used"

    def test_time_proposal_survives_date_only_followup(self):
        # Turn 1 state: user said "book haircut evening" → time_proposal set in session
        turn1_session = {
            "time_proposal": {"mode": "fuzzy", "label": "evening"},
        }
        # Turn 2 plan: user said "tomorrow" → NLU gives date_proposal only
        turn2_plan = {
            "date_proposal": {"mode": "single_day", "start": "2026-01-14"},
        }
        result = resolve_execution_proposals(turn2_plan, turn1_session)
        assert result["date_proposal"]["start"] == "2026-01-14", "date from turn 2 must be used"
        assert result["time_proposal"]["label"] == "evening", "fuzzy time from turn 1 must survive"


# ---------------------------------------------------------------------------
# apply_time_constraint_to_missing_slots
# ---------------------------------------------------------------------------


class TestApplyTimeConstraintToMissingSlots:
    def test_exact_removes_time_for_appointment(self):
        result = apply_time_constraint_to_missing_slots(
            "CREATE_APPOINTMENT",
            ["date", "time"],
            {"mode": "exact", "start": "15:00"},
        )
        assert result == ["date"]

    def test_fuzzy_does_not_remove_time(self):
        result = apply_time_constraint_to_missing_slots(
            "CREATE_APPOINTMENT",
            ["date", "time"],
            {"mode": "fuzzy", "label": "afternoon"},
        )
        assert "time" in result

    def test_none_constraint_passthrough(self):
        missing = ["date", "time"]
        result = apply_time_constraint_to_missing_slots("CREATE_APPOINTMENT", missing, None)
        assert result == missing

    def test_non_appointment_intent_passthrough(self):
        missing = ["date", "time"]
        result = apply_time_constraint_to_missing_slots(
            "CREATE_RESERVATION", missing, {"mode": "exact", "start": "15:00"}
        )
        assert result == missing

    def test_time_not_in_missing_is_noop(self):
        result = apply_time_constraint_to_missing_slots(
            "CREATE_APPOINTMENT",
            ["date"],
            {"mode": "exact", "start": "15:00"},
        )
        assert result == ["date"]


# ---------------------------------------------------------------------------
# datetime_range_from_availability_result
# ---------------------------------------------------------------------------


class TestDatetimeRangeFromAvailabilityResult:
    def test_extracts_from_starts_at_ends_at(self):
        result = datetime_range_from_availability_result(
            {
                "slots": [
                    {
                        "starts_at": "2026-03-05T15:00:00Z",
                        "ends_at": "2026-03-08T11:00:00Z",
                    }
                ]
            }
        )
        assert result == {
            "start": "2026-03-05T15:00:00Z",
            "end": "2026-03-08T11:00:00Z",
        }

    def test_extracts_from_start_end_keys(self):
        result = datetime_range_from_availability_result(
            {"slots": [{"start": "2026-01-16T15:00:00Z", "end": "2026-01-16T16:00:00Z"}]}
        )
        assert result["start"] == "2026-01-16T15:00:00Z"
        assert result["end"] == "2026-01-16T16:00:00Z"

    def test_empty_slots_returns_none(self):
        assert datetime_range_from_availability_result({"slots": []}) is None
        assert datetime_range_from_availability_result(None) is None
