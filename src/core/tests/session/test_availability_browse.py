"""Tests for transient availability browse detection from structured operations."""

import pytest

from core.workflows.availability.browse import (
    extract_availability_browse,
    normalize_availability_operation,
    resolve_availability_browse,
)
from core.session.turn_persistence import project_and_persist_turn_result
from core.workflows.availability.presentation import (
    availability_cache_from_session,
    availability_fingerprint_from_session,
    availability_pagination_from_session,
    build_availability_presentation,
    build_presented_availability_page,
    presented_availability_from_session,
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
            ("BROWSE_NEXT", None),
            ("browse-next", None),
            ("next", None),
            ("browse_next_times", None),
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

    def test_does_not_read_facts_operation(self):
        response = {
            "intent": {"name": "AVAILABILITY"},
            "facts": {"operation": "browse_previous"},
        }
        assert extract_availability_browse(response) is None

    def test_does_not_read_derived_availability_browse_field(self):
        response = {
            "intent": {"name": "AVAILABILITY"},
            "availability_browse": {"direction": "next"},
        }
        assert extract_availability_browse(response) is None

    def test_resolves_structured_browse_next(self):
        merged = {
            "intent": {"name": "AVAILABILITY"},
            "operation": "browse_next",
        }
        assert resolve_availability_browse(merged)["direction"] == "next"

    def test_resolves_structured_browse_previous(self):
        merged = {
            "intent": {"name": "AVAILABILITY"},
            "operation": "browse_previous",
        }
        assert resolve_availability_browse(merged)["direction"] == "previous"

    @pytest.mark.parametrize("source_text", ("show more", "previous"))
    def test_missing_operation_never_infers_from_source_text(self, source_text):
        merged = {"intent": {"name": "AVAILABILITY"}, "_source_text": source_text}
        session = {
            "intent_name": "CREATE_APPOINTMENT",
            "last_execution_result": {
                "type": "availability",
                "status": "success",
                "slots": [{"starts_at": "2026-07-09T09:00:00Z"}],
            },
        }
        assert resolve_availability_browse(merged, session) is None

    @pytest.mark.parametrize(
        "operation",
        [
            "search",
            "next",
            "browse-next",
            "browse_next_times",
            "unrelated",
            None,
        ],
    )
    def test_invalid_or_unrelated_operation_does_not_browse(self, operation):
        merged = {
            "intent": {"name": "AVAILABILITY"},
            "operation": operation,
            "_source_text": "show more",
        }
        assert resolve_availability_browse(merged) is None


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
        from core.workflows.availability.presentation import (
            presented_availability_from_session,
            availability_pagination_from_session,
            availability_cache_from_session,
            availability_fingerprint_from_session,
        )
        presented_before = dict(presented_availability_from_session(session) or {})
        slots_before = dict(session["slots"])
        luma = {
            "intent": {"name": "CREATE_APPOINTMENT"},
            "operation": "browse_previous",
            "slots": {},
        }
        merged = merge_luma_with_session(luma, session)
        assert merged.get("slots") == slots_before
        assert presented_availability_from_session(session) == presented_before
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
        assert (availability_pagination_from_session(session) or {}).get("page_index") == 1
        assert len((presented_availability_from_session(session) or {}).get("slots") or []) == 3
        assert (
            (presented_availability_from_session(session) or {})["slots"][0]["starts_at"]
            .endswith("T15:00:00Z")
        )
        assert len((availability_cache_from_session(session) or {}).get("slots") or []) == len(raw)
        assert "availability_pagination" not in session
