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
from core.planning.temporal_proposal import (
    apply_confirmed_datetime,
    apply_time_constraint_to_missing_slots,
    datetime_range_from_availability_result,
    enrich_last_execution_result,
    get_cached_availability_offers,
    resolve_execution_proposals,
    slots_for_availability_search,
    strip_unconfirmed_temporal_slots,
    temporal_slots_confirmed,
    try_bind_offered_time_selection,
)
from core.rendering.availability_renderer import build_presented_availability


# ---------------------------------------------------------------------------
# strip_unconfirmed_temporal_slots
# ---------------------------------------------------------------------------


class TestStripUnconfirmedTemporalSlots:
    def test_strips_date_and_time_for_create_appointment(self):
        slots = {
            "service_id": "haircut",
            "date": "2026-01-14",
            "time": "15:00",
        }
        result = strip_unconfirmed_temporal_slots(slots, "CREATE_APPOINTMENT", {})
        assert result == {"service_id": "haircut"}

    def test_preserves_temporal_when_confirmed_via_bound_slots(self):
        slots = {"service_id": "haircut", "date": "2026-01-14", "time": "15:00"}
        session = {
            "slots": {"service_id": "haircut", "date": "2026-01-14", "time": "15:00"},
            "resolved_datetime_range": {
                "start": "2026-01-14T15:00:00Z",
                "end": "2026-01-14T16:00:00Z",
            },
        }
        result = strip_unconfirmed_temporal_slots(slots, "CREATE_APPOINTMENT", session)
        assert result["date"] == "2026-01-14"
        assert result["time"] == "15:00"

    def test_strips_temporal_when_only_fingerprint_present(self):
        slots = {"service_id": "haircut", "date": "2026-01-14", "time": "15:00"}
        session = {"availability_fingerprint": "haircut|2026-01-14|15:00"}
        result = strip_unconfirmed_temporal_slots(slots, "CREATE_APPOINTMENT", session)
        assert result == {"service_id": "haircut"}

    def test_noop_for_other_intents(self):
        slots = {"service_id": "room", "start_date": "2026-01-14"}
        result = strip_unconfirmed_temporal_slots(slots, "CREATE_RESERVATION", {})
        assert result == slots

    def test_temporal_slots_confirmed_with_resolved_range(self):
        session = {"resolved_datetime_range": {"start": "2026-01-14T15:00:00Z"}}
        assert temporal_slots_confirmed(session) is True


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
# try_bind_offered_time_selection
# ---------------------------------------------------------------------------


class TestTryBindOfferedTimeSelection:
    def test_binds_matching_offered_time(self):
        session = {
            "last_execution_result": {
                "type": "availability",
                "status": "success",
                "slots": [
                    {
                        "starts_at": "2026-07-06T09:00:00Z",
                        "ends_at": "2026-07-06T09:30:00Z",
                    }
                ],
            }
        }
        result = try_bind_offered_time_selection(
            {"service_id": "premium haircut"},
            session,
            date_proposal={"mode": "single_day", "start": "2026-07-06"},
            time_proposal={"mode": "exact", "value": "9:00 AM"},
        )
        assert result is not None
        assert result["slots"]["date"] == "2026-07-06"
        assert result["slots"]["time"] == "09:00"
        assert result["resolved_datetime_range"]["start"] == "2026-07-06T09:00:00Z"

    def test_rejects_time_not_in_offers(self):
        session = {
            "last_execution_result": {
                "type": "availability",
                "status": "success",
                "slots": [
                    {
                        "starts_at": "2026-07-06T09:00:00Z",
                        "ends_at": "2026-07-06T09:30:00Z",
                    }
                ],
            }
        }
        result = try_bind_offered_time_selection(
            {"service_id": "premium haircut"},
            session,
            date_proposal={"mode": "single_day", "start": "2026-07-06"},
            time_proposal={"mode": "exact", "value": "2:00 PM"},
        )
        assert result is None

    def test_binds_latest_presented_date_not_stale_date_proposal(self):
        """User picks from July 3 offers; stale date_proposal must not force July 6."""
        session = {
            "last_execution_result": {
                "type": "availability",
                "status": "success",
                "search_date": "2026-07-03",
                "slots": [
                    {
                        "starts_at": "2026-07-03T09:00:00Z",
                        "ends_at": "2026-07-03T09:30:00Z",
                    },
                    {
                        "starts_at": "2026-07-03T09:30:00Z",
                        "ends_at": "2026-07-03T10:00:00Z",
                    },
                ],
            }
        }
        result = try_bind_offered_time_selection(
            {"service_id": "premium haircut", "date": "2026-07-06"},
            session,
            date_proposal={"mode": "single_day", "start": "2026-07-06"},
            time_proposal={"mode": "exact", "value": "9:00"},
        )
        assert result is not None
        assert result["slots"]["date"] == "2026-07-03"
        assert result["slots"]["time"] == "09:00"

    def test_new_search_replaces_historical_offers_for_binding(self):
        """After searching July 6, binding 9:00 must use July 6 not earlier July 3."""
        july6_slots = [
            {
                "starts_at": "2026-07-06T09:00:00Z",
                "ends_at": "2026-07-06T09:30:00Z",
            }
        ]
        session = {
            "last_execution_result": enrich_last_execution_result(
                {
                    "type": "availability",
                    "status": "success",
                    "slots": july6_slots,
                },
                search_date="2026-07-06",
            ),
            "presented_availability": build_presented_availability(
                july6_slots, search_date="2026-07-06"
            ),
        }
        result = try_bind_offered_time_selection(
            {"service_id": "premium haircut"},
            session,
            date_proposal={"mode": "single_day", "start": "2026-07-03"},
            time_proposal={"mode": "exact", "value": "9:00"},
        )
        assert result is not None
        assert result["slots"]["date"] == "2026-07-06"

    def test_binds_only_presented_slots_not_full_search_result(self):
        """Time in full API result but not shown must not auto-bind."""
        full_slots = [
            {"starts_at": "2026-07-09T02:00:00Z", "ends_at": "2026-07-09T02:30:00Z"},
            {"starts_at": "2026-07-09T03:00:00Z", "ends_at": "2026-07-09T03:30:00Z"},
            {"starts_at": "2026-07-09T11:00:00Z", "ends_at": "2026-07-09T11:30:00Z"},
            {"starts_at": "2026-07-09T17:00:00Z", "ends_at": "2026-07-09T17:30:00Z"},
        ]
        presented = build_presented_availability(
            [
                full_slots[0],
                full_slots[1],
                full_slots[3],
            ],
            search_date="2026-07-09",
        )
        session = {
            "last_execution_result": {
                "type": "availability",
                "status": "success",
                "search_date": "2026-07-09",
                "slots": full_slots,
            },
            "presented_availability": presented,
        }
        # 11am exists in full result but was not presented
        assert try_bind_offered_time_selection(
            {"service_id": "premium haircut"},
            session,
            time_proposal={"mode": "exact", "value": "11am"},
        ) is None
        # Presented 5pm binds
        result = try_bind_offered_time_selection(
            {"service_id": "premium haircut"},
            session,
            time_proposal={"mode": "exact", "value": "5pm"},
        )
        assert result is not None
        assert result["slots"]["date"] == "2026-07-09"
        assert result["slots"]["time"] == "17:00"

    def test_legacy_fallback_caps_to_presentation_size(self):
        """Without presented_availability, only the UI-sized prefix is selectable."""
        slots = [
            {
                "starts_at": f"2026-07-06T{h:02d}:00:00Z",
                "ends_at": f"2026-07-06T{h:02d}:30:00Z",
            }
            for h in range(9, 18)
        ]
        session = {
            "last_execution_result": {
                "type": "availability",
                "status": "success",
                "search_date": "2026-07-06",
                "slots": slots,
            }
        }
        # First 6 hours (9-14) are presented; 16:00 is not
        assert try_bind_offered_time_selection(
            {"service_id": "premium haircut"},
            session,
            time_proposal={"mode": "exact", "value": "4pm"},
        ) is None
        result = try_bind_offered_time_selection(
            {"service_id": "premium haircut"},
            session,
            time_proposal={"mode": "exact", "value": "9am"},
        )
        assert result is not None
        assert result["slots"]["time"] == "09:00"


class TestGetCachedAvailabilityOffers:
    def test_prefers_presented_availability(self):
        session = {
            "last_execution_result": {
                "type": "availability",
                "status": "success",
                "search_date": "2026-07-09",
                "slots": [
                    {"starts_at": "2026-07-09T09:00:00Z", "ends_at": "2026-07-09T09:30:00Z"},
                    {"starts_at": "2026-07-09T11:00:00Z", "ends_at": "2026-07-09T11:30:00Z"},
                ],
            },
            "presented_availability": {
                "search_date": "2026-07-09",
                "slots": [
                    {"starts_at": "2026-07-09T09:00:00Z", "ends_at": "2026-07-09T09:30:00Z"},
                ],
            },
        }
        offers = get_cached_availability_offers(session)
        assert len(offers) == 1
        assert offers[0]["starts_at"].startswith("2026-07-09T09:00")

    def test_filters_to_search_date_only(self):
        session = {
            "last_execution_result": {
                "type": "availability",
                "status": "success",
                "search_date": "2026-07-03",
                "slots": [
                    {"starts_at": "2026-07-06T09:00:00Z", "ends_at": "2026-07-06T09:30:00Z"},
                    {"starts_at": "2026-07-03T09:00:00Z", "ends_at": "2026-07-03T09:30:00Z"},
                    {"starts_at": "2026-07-03T09:30:00Z", "ends_at": "2026-07-03T10:00:00Z"},
                ],
            }
        }
        offers = get_cached_availability_offers(session)
        assert len(offers) == 2
        assert all("2026-07-03" in (o.get("starts_at") or "") for o in offers)

    def test_enrich_keeps_full_slots_and_safe_search_date(self):
        payload = enrich_last_execution_result(
            {
                "type": "availability",
                "status": "success",
                "slots": [
                    {"starts_at": "2026-07-06T09:00:00Z", "ends_at": "2026-07-06T09:30:00Z"},
                    {"starts_at": "2026-07-03T09:00:00Z", "ends_at": "2026-07-03T09:30:00Z"},
                ],
            },
            search_date="2026-07-03",
        )
        assert payload["search_date"] == "2026-07-03"
        # Full latest search retained for diagnostics; bind uses presented_availability.
        assert len(payload["slots"]) == 2

    def test_enrich_prefers_offer_date_when_plan_date_matches_nothing(self):
        payload = enrich_last_execution_result(
            {
                "type": "availability",
                "status": "success",
                "slots": [
                    {"starts_at": "2026-07-03T16:00:00Z", "ends_at": "2026-07-03T16:30:00Z"},
                ],
            },
            search_date="2026-07-06",
        )
        assert payload["search_date"] == "2026-07-03"
        assert len(payload["slots"]) == 1


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
