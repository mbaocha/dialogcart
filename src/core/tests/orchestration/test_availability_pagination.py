"""Tests for cached availability pagination (PR3)."""

from core.workflows.availability.pagination import try_handle_availability_browse_turn
from core.session.turn_persistence import project_and_persist_turn_result
from core.workflows.availability.presentation import (
    build_availability_presentation,
    build_presented_availability_page,
    compute_target_page_index,
)


def _cached_slots(hours=range(9, 18)):
    return [
        {
            "starts_at": f"2026-07-09T{h:02d}:00:00Z",
            "ends_at": f"2026-07-09T{h:02d}:30:00Z",
        }
        for h in hours
    ]


class TestPaginationHelpers:
    def test_page_slice_second_page(self):
        raw = _cached_slots()
        page0 = build_presented_availability_page(raw, page_index=0, page_size=6)
        page1 = build_presented_availability_page(raw, page_index=1, page_size=6)
        assert len(page0["slots"]) == 6
        assert len(page1["slots"]) == 3
        assert page0["slots"][0]["starts_at"].endswith("T09:00:00Z")
        assert page1["slots"][0]["starts_at"].endswith("T15:00:00Z")
        overlap = {s["starts_at"] for s in page0["slots"]} & {
            s["starts_at"] for s in page1["slots"]
        }
        assert not overlap

    def test_compute_target_page_index_next_exhausted(self):
        idx, exhausted = compute_target_page_index(1, "next", 9, 6)
        assert idx == 1
        assert exhausted is True

    def test_presentation_flags(self):
        raw = _cached_slots()
        pres0 = build_availability_presentation(raw, page_index=0, page_size=6)
        pres1 = build_availability_presentation(raw, page_index=1, page_size=6)
        assert pres0["has_next"] is True
        assert pres0["has_previous"] is False
        assert pres1["has_next"] is False
        assert pres1["has_previous"] is True


class TestTryHandleAvailabilityBrowseTurn:
    class _Store:
        def __init__(self, session):
            self.sessions = {(1, "u1"): dict(session)}

        def get_session(self, organization_id, user_id):
            return dict(self.sessions[(organization_id, user_id)])

        def save_session(self, organization_id, user_id, session):
            self.sessions[(organization_id, user_id)] = dict(session)

    def _plan(self, operation=None, *, availability_browse=None, source_text=None, raw_intent=None):
        merged = {
            "intent": {"name": "CREATE_APPOINTMENT"},
            "slots": {"service_id": "premium haircut"},
        }
        if operation:
            merged["operation"] = operation
        if availability_browse is not None:
            merged["availability_browse"] = availability_browse
        if source_text is not None:
            merged["_source_text"] = source_text
        if raw_intent is not None:
            merged["_raw_luma_response"] = {"intent": {"name": raw_intent}}
        return {
            "_merged_luma_response": merged,
            "_decision": {
                "intent_name": "CREATE_APPOINTMENT",
                "plan": {
                    "status": "READY",
                    "stage": "AVAILABILITY",
                    "action": "SEARCH_AVAILABILITY",
                },
                "facts": {
                    "slots": {"service_id": "premium haircut"},
                    "missing_slots": ["time"],
                },
            },
        }

    def _session(self):
        slots = _cached_slots()
        return {
            "intent_name": "CREATE_APPOINTMENT",
            "slots": {"service_id": "premium haircut"},
            "last_execution_result": {
                "type": "availability",
                "status": "success",
                "search_date": "2026-07-09",
                "slots": slots,
            },
            "presented_availability": build_presented_availability_page(
                slots, page_index=0, page_size=6, search_date="2026-07-09"
            ),
            "availability_presentation": build_availability_presentation(
                slots, page_index=0, page_size=6
            ),
            "availability_fingerprint": "fp-unchanged",
        }

    @staticmethod
    def _project(response, previous_session, store):
        return project_and_persist_turn_result(
            result=response,
            organization_id=1,
            user_id="u1",
            previous_session_state=previous_session,
            working_session_state=previous_session,
            session_store=store,
        )

    def test_advances_page_without_search(self):
        previous_session = self._session()
        store = self._Store(previous_session)
        response = try_handle_availability_browse_turn(
            plan=self._plan("browse_next"),
            session_state=store.get_session(1, "u1"),
            session_store=store,
            organization_id=1,
            user_id="u1",
        )
        assert response is not None
        assert response["availability_pagination"]["page_index"] == 1
        assert response["availability_pagination"]["exhausted"] is False
        assert response["outcome"]["plan"]["action"] is None
        self._project(response, previous_session, store)
        persisted = store.get_session(1, "u1")
        assert persisted["availability_presentation"]["page_index"] == 1
        assert len(persisted["presented_availability"]["slots"]) == 3
        assert persisted["availability_fingerprint"] == "fp-unchanged"

    def test_previous_page_returns_earlier_page(self):
        store = self._Store(self._session())
        session = store.get_session(1, "u1")
        session["availability_presentation"] = build_availability_presentation(
            session["last_execution_result"]["slots"], page_index=1, page_size=6
        )
        session["presented_availability"] = build_presented_availability_page(
            session["last_execution_result"]["slots"],
            page_index=1,
            page_size=6,
            search_date="2026-07-09",
        )
        store.save_session(1, "u1", session)
        page1_starts = [
            s["starts_at"]
            for s in store.get_session(1, "u1")["presented_availability"]["slots"]
        ]

        response = try_handle_availability_browse_turn(
            plan=self._plan("browse_previous"),
            session_state=store.get_session(1, "u1"),
            session_store=store,
            organization_id=1,
            user_id="u1",
        )
        assert response is not None
        assert response["availability_pagination"]["page_index"] == 0
        assert response["availability_pagination"]["exhausted"] is False
        self._project(response, session, store)
        page0_starts = [
            s["starts_at"]
            for s in store.get_session(1, "u1")["presented_availability"]["slots"]
        ]
        assert page0_starts != page1_starts
        assert page0_starts[0].endswith("T09:00:00Z")

    def test_browse_nulls_planned_search_action(self):
        store = self._Store(self._session())
        response = try_handle_availability_browse_turn(
            plan=self._plan("browse_next"),
            session_state=store.get_session(1, "u1"),
            session_store=store,
            organization_id=1,
            user_id="u1",
        )
        assert response is not None
        assert response["outcome"]["plan"]["action"] is None

    def test_no_more_does_not_repeat_page(self):
        store = self._Store(self._session())
        session = store.get_session(1, "u1")
        session["availability_presentation"] = build_availability_presentation(
            session["last_execution_result"]["slots"], page_index=1, page_size=6
        )
        session["presented_availability"] = build_presented_availability_page(
            session["last_execution_result"]["slots"],
            page_index=1,
            page_size=6,
            search_date="2026-07-09",
        )
        store.save_session(1, "u1", session)
        before_slots = list(
            store.get_session(1, "u1")["presented_availability"]["slots"]
        )

        response = try_handle_availability_browse_turn(
            plan=self._plan("browse_next"),
            session_state=store.get_session(1, "u1"),
            session_store=store,
            organization_id=1,
            user_id="u1",
        )
        assert response is not None
        assert response["availability_pagination"]["exhausted"] is True
        after_slots = store.get_session(1, "u1")["presented_availability"]["slots"]
        assert after_slots == before_slots

    def test_returns_none_without_browse(self):
        assert (
            try_handle_availability_browse_turn(
                plan=self._plan(),
                session_state=self._session(),
                session_store=None,
                organization_id=1,
                user_id="u1",
            )
            is None
        )

    def test_advances_page_via_availability_browse_field(self):
        store = self._Store(self._session())
        response = try_handle_availability_browse_turn(
            plan=self._plan(availability_browse={"direction": "next"}),
            session_state=store.get_session(1, "u1"),
            session_store=store,
            organization_id=1,
            user_id="u1",
        )
        assert response is not None
        assert response["availability_pagination"]["page_index"] == 1

    def test_advances_page_via_text_fallback(self):
        store = self._Store(self._session())
        response = try_handle_availability_browse_turn(
            plan=self._plan(
                source_text="show me additional times",
                raw_intent="AVAILABILITY",
            ),
            session_state=store.get_session(1, "u1"),
            session_store=store,
            organization_id=1,
            user_id="u1",
        )
        assert response is not None
        assert response["availability_pagination"]["page_index"] == 1
