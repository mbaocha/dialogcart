"""Tests for transient availability browse detection from structured operations."""

import pytest

from core.workflows.availability.browse import (
    extract_availability_browse,
    infer_browse_direction_from_text,
    normalize_availability_operation,
    resolve_availability_browse,
)
from core.session.turn_persistence import project_and_persist_turn_result
from core.workflows.availability.presentation import (
    build_availability_presentation,
    build_presented_availability_page,
)
from core.session.merge import merge_luma_with_session


def _cached_slots(hours=range(9, 18)):
    return [
        {
            "starts_at": f"2026-07-09T{h:02d}:00:00Z",
            "ends_at": f"2026-07-09T{h:02d}:30:00Z",
        }
        for h in hours
    ]


class TestNormalizeAvailabilityOperation:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("browse_next", {"direction": "next", "axis_hint": "any"}),
            ("browse_previous", {"direction": "previous", "axis_hint": "any"}),
            ("BROWSE_NEXT", {"direction": "next", "axis_hint": "any"}),
            ("browse-next", {"direction": "next", "axis_hint": "any"}),
            ("search", None),
            (None, None),
            ("", None),
        ],
    )
    def test_normalize_operation_values(self, raw, expected):
        assert normalize_availability_operation(raw) == expected


class TestExtractAvailabilityBrowse:
    def test_reads_top_level_operation(self):
        response = {
            "intent": {"name": "AVAILABILITY"},
            "operation": "browse_next",
        }
        assert extract_availability_browse(response) == {"direction": "next", "axis_hint": "any"}

    def test_reads_facts_operation(self):
        response = {
            "intent": {"name": "AVAILABILITY"},
            "facts": {"operation": "browse_previous"},
        }
        assert extract_availability_browse(response) == {"direction": "previous", "axis_hint": "any"}

    def test_reads_availability_browse_field(self):
        response = {
            "intent": {"name": "AVAILABILITY"},
            "availability_browse": {"direction": "next"},
        }
        assert extract_availability_browse(response) == {"direction": "next", "axis_hint": "any"}

    def test_resolve_falls_back_to_text_with_availability_intent(self):
        merged = {
            "intent": {"name": "CREATE_APPOINTMENT"},
            "_raw_luma_response": {"intent": {"name": "AVAILABILITY"}},
            "_source_text": "show more",
        }
        session = {
            "last_execution_result": {
                "type": "availability",
                "status": "success",
                "slots": [{"starts_at": "2026-07-09T09:00:00Z"}],
            }
        }
        assert resolve_availability_browse(merged, session)["direction"] == "next"

    def test_resolve_does_not_infer_without_cached_availability(self):
        merged = {
            "_raw_luma_response": {"intent": {"name": "AVAILABILITY"}},
            "_source_text": "show more",
        }
        assert resolve_availability_browse(merged, {}) is None

    def test_resolve_falls_back_to_text_with_create_appointment_live_luma_shape(self):
        """Live Luma browse: CREATE_APPOINTMENT intent, no operation, cached availability."""
        merged = {
            "intent": {"name": "CREATE_APPOINTMENT"},
            "_raw_luma_response": {"intent": {"name": "CREATE_APPOINTMENT"}},
            "_source_text": "show more",
        }
        session = {
            "intent_name": "CREATE_APPOINTMENT",
            "last_execution_result": {
                "type": "availability",
                "status": "success",
                "slots": [{"starts_at": "2026-07-09T09:00:00Z"}],
            },
        }
        assert resolve_availability_browse(merged, session)["direction"] == "next"

    def test_resolve_does_not_infer_create_appointment_without_cached_availability(self):
        merged = {
            "intent": {"name": "CREATE_APPOINTMENT"},
            "_raw_luma_response": {"intent": {"name": "CREATE_APPOINTMENT"}},
            "_source_text": "show more",
        }
        session = {"intent_name": "CREATE_APPOINTMENT"}
        assert resolve_availability_browse(merged, session) is None
        assert resolve_availability_browse(merged, {}) is None

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("show more", "next"),
            ("more", "next"),
            ("next", "next"),
            ("previous", "previous"),
            ("show previous", "previous"),
            ("back", "previous"),
            ("next day", None),
            ("previous day", None),
            ("later date", None),
            ("book premium", None),
        ],
    )
    def test_infer_browse_direction_from_text(self, text, expected):
        inferred = infer_browse_direction_from_text(text)
        if expected is None:
            assert inferred is None
        else:
            assert inferred is not None and inferred["direction"] == expected
            assert inferred.get("axis_hint") == "any"


class TestMergeAvailabilityBrowse:
    def _session(self):
        return {
            "intent_name": "CREATE_APPOINTMENT",
            "status": "NEEDS_CLARIFICATION",
            "slots": {"service_id": "premium haircut"},
            "presented_availability": {
                "search_date": "2026-07-03",
                "slots": [{"starts_at": "2026-07-03T10:00:00Z"}],
            },
            "availability_presentation": {
                "page_index": 0,
                "page_size": 6,
                "has_next": True,
                "has_previous": False,
            },
        }

    def test_merge_attaches_transient_browse_signal(self):
        # Post–Stage 01 shape: durable planning intent + browse via operation.
        luma = {
            "intent": {"name": "CREATE_APPOINTMENT"},
            "operation": "browse_next",
            "slots": {},
        }
        merged = merge_luma_with_session(luma, self._session())
        assert merged.get("availability_browse", {}).get("direction") == "next"

    def test_merge_does_not_modify_slots_or_presented_availability(self):
        session = self._session()
        presented_before = dict(session["presented_availability"])
        slots_before = dict(session["slots"])
        luma = {
            "intent": {"name": "CREATE_APPOINTMENT"},
            "operation": "browse_previous",
            "slots": {},
        }
        merged = merge_luma_with_session(luma, session)
        assert merged.get("slots") == slots_before
        assert session["presented_availability"] == presented_before
        assert session["slots"] == slots_before

    def test_merge_clears_browse_when_absent_this_turn(self):
        session = self._session()
        session["availability_browse"] = {"direction": "next"}
        luma = {
            "intent": {"name": "CREATE_APPOINTMENT"},
            "slots": {},
        }
        merged = merge_luma_with_session(luma, session)
        assert "availability_browse" not in merged


class TestBrowseNotPersistedToSession:
    def test_build_session_state_strips_transient_browse(self):
        outcome = {
            "intent_name": "CREATE_APPOINTMENT",
            "status": "READY",
            "facts": {
                "slots": {"service_id": "premium haircut"},
                "missing_slots": ["date", "time"],
                "availability_browse": {"direction": "next"},
            },
        }
        merged = {
            "intent": {"name": "CREATE_APPOINTMENT"},
            "slots": {"service_id": "premium haircut"},
            "availability_browse": {"direction": "next"},
            "operation": "browse_next",
        }
        session = project_and_persist_turn_result(
            result={
                "outcome": outcome,
                "plan": outcome,
                "_merged_luma_response": merged,
            },
            outcome_status="READY",
            organization_id=1,
            user_id="u1",
            save=False,
        )
        assert session is not None
        assert "availability_browse" not in session
        assert "operation" not in session
        assert "availability_browse" not in (session.get("facts") or {})
        assert "operation" not in (session.get("facts") or {})


class TestBrowsePaginationSessionPersistence:
    def test_build_session_preserves_page_index_and_full_cache(self):
        raw = _cached_slots()
        page1_presented = build_presented_availability_page(
            raw, page_index=1, page_size=6, search_date="2026-07-09"
        )
        page1_presentation = build_availability_presentation(
            raw, page_index=1, page_size=6
        )
        previous = {
            "intent_name": "CREATE_APPOINTMENT",
            "status": "READY",
            "slots": {"service_id": "premium haircut"},
            "last_execution_result": {
                "type": "availability",
                "status": "success",
                "search_date": "2026-07-09",
                "slots": raw,
            },
            "presented_availability": build_presented_availability_page(
                raw, page_index=0, page_size=6, search_date="2026-07-09"
            ),
            "availability_presentation": build_availability_presentation(
                raw, page_index=0, page_size=6
            ),
        }
        outcome = {
            "intent_name": "CREATE_APPOINTMENT",
            "status": "success",
            "type": "availability",
            "slots": page1_presented["slots"],
            "availability_pagination": {
                "direction": "next",
                "exhausted": False,
                "page_index": 1,
            },
            "facts": {
                "slots": {"service_id": "premium haircut"},
                "missing_slots": ["time"],
            },
        }
        merged = {
            "intent": {"name": "CREATE_APPOINTMENT"},
            "slots": {"service_id": "premium haircut"},
            "operation": "browse_next",
        }
        session = project_and_persist_turn_result(
            result={
                "outcome": outcome,
                "plan": {
                    "intent_name": "CREATE_APPOINTMENT",
                    "status": "READY",
                    "missing_slots": ["time"],
                },
                "_merged_luma_response": merged,
                "_workflow_result": {
                    "kind": "availability_pagination",
                    "presented_availability": page1_presented,
                    "availability_presentation": page1_presentation,
                    "page_index": 1,
                    "page_size": 6,
                },
            },
            outcome_status="success",
            organization_id=1,
            previous_session_state=previous,
            working_session_state=previous,
            user_id="u1",
            save=False,
        )
        assert session is not None
        assert session["availability_presentation"]["page_index"] == 1
        assert len(session["presented_availability"]["slots"]) == 3
        assert (
            session["presented_availability"]["slots"][0]["starts_at"]
            .endswith("T15:00:00Z")
        )
        assert len(session["last_execution_result"]["slots"]) == len(raw)
        assert "availability_pagination" not in session
