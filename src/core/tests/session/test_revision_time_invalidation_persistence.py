"""Revision invalidation must survive the session persistence boundary."""

from __future__ import annotations

import pytest

from core.planning.booking_revision import detect_booking_revision
from core.planning.pipeline.types import WorkingTurn
from core.planning.planning_mutations import apply_booking_revision_mutations
from core.planning.temporal_proposal import resolve_execution_proposals
from core.session.persist import assemble_session_projection_fields
from core.tests.harness.session_store import MockSessionStore


OLD_TIME = {"mode": "exact", "value": "10:00"}
NEW_TIME = {"mode": "exact", "value": "11:00"}


def _previous_session():
    return {
        "intent_name": "CREATE_APPOINTMENT",
        "status": "AWAITING_CONFIRMATION",
        "confirmation_state": "pending",
        "slots": {
            "service_id": "premium haircut",
            "date": "2026-07-03",
            "time": "10:00",
            "datetime_range": {
                "start": "2026-07-03T10:00:00Z",
                "end": "2026-07-03T10:30:00Z",
            },
        },
        "facts": {
            "times": ["10:00"],
            "time_proposal": dict(OLD_TIME),
            "time_constraint": {"start": "10:00", "end": "10:00"},
            "resolved_datetime_range": {
                "start": "2026-07-03T10:00:00Z",
                "end": "2026-07-03T10:30:00Z",
            },
        },
        "time_proposal": dict(OLD_TIME),
        "time_constraint": {"start": "10:00", "end": "10:00"},
        "temporal": {
            "mode": "single_day",
            "start_date": "2026-07-03",
            "start_date_expression": "Friday",
            "start_time": "10:00",
            "end_time": "10:00",
            "start_time_expression": "10am",
            "end_time_expression": "10am",
        },
        "resolved_datetime_range": {
            "start": "2026-07-03T10:00:00Z",
            "end": "2026-07-03T10:30:00Z",
        },
        "presented_availability": {"slots": [{"starts_at": "2026-07-03T10:00:00Z"}]},
        "availability_fingerprint": "old-fingerprint",
        "last_execution_result": {"type": "availability"},
    }


def _revision_payload(kind: str):
    previous = _previous_session()
    new_service = "flexi haircut" if kind == "service" else "premium haircut"
    new_date = "2026-07-04" if kind == "date" else "2026-07-03"
    payload = {
        "intent": {"name": "CREATE_APPOINTMENT"},
        "slots": dict(previous["slots"], service_id=new_service),
        "_effective_collected_slots": dict(previous["slots"], service_id=new_service),
        "facts": {
            **previous["facts"],
            "service_id": new_service,
            "dates": [new_date],
        },
        "date_proposal": {"mode": "single_day", "start": new_date},
        "time_proposal": dict(OLD_TIME),
        "time_constraint": {"start": "10:00", "end": "10:00"},
        "temporal": {
            **previous["temporal"],
            "start_date": new_date,
            "start_date_expression": "Saturday" if kind == "date" else "Friday",
        },
        "_current_turn_has_date": kind == "date",
        "_current_turn_date": new_date if kind == "date" else None,
        "_current_turn_has_time": False,
        "_current_turn_service_id": new_service,
        "confirmation_state": "pending",
        "resolved_datetime_range": dict(previous["resolved_datetime_range"]),
        "presented_availability": previous["presented_availability"],
        "availability_fingerprint": previous["availability_fingerprint"],
        "last_execution_result": previous["last_execution_result"],
    }
    return previous, payload


def _persist(payload, previous):
    has_time = bool((payload.get("slots") or {}).get("time"))
    outcome = {
        "intent_name": "CREATE_APPOINTMENT",
        "status": "READY",
        "slots": payload["slots"],
        "facts": payload.get("facts", {}),
        "missing_slots": [] if has_time else ["time"],
        "confirmation_state": payload.get("confirmation_state"),
    }
    return assemble_session_projection_fields(
        outcome=outcome,
        outcome_status="READY",
        organization_id=1,
        merged_luma_response=payload,
        previous_session_state=previous,
        user_id="revision-time-test",
    )


def _assert_old_time_absent(session):
    assert session is not None
    assert "time_proposal" not in session
    assert "time_constraint" not in session
    assert "time" not in session.get("slots", {})
    assert "datetime_range" not in session.get("slots", {})
    assert "resolved_datetime_range" not in session
    facts = session.get("facts", {})
    for key in ("times", "time_proposal", "time_constraint", "resolved_datetime_range"):
        assert key not in facts
    temporal = session.get("temporal", {})
    for key in (
        "start_time",
        "end_time",
        "start_time_expression",
        "end_time_expression",
    ):
        assert temporal.get(key) is None
    assert session.get("confirmation_state") is None
    for key in (
        "presented_availability",
        "availability_fingerprint",
        "last_execution_result",
    ):
        assert key not in session


@pytest.mark.parametrize("kind", ["date", "service"])
def test_revision_without_replacement_time_stays_deleted_after_persistence(kind):
    previous, payload = _revision_payload(kind)
    revision = detect_booking_revision(payload, previous)
    assert getattr(revision, kind) is True
    working = WorkingTurn(payload=payload, effective_collected_slots=payload["slots"])

    apply_booking_revision_mutations(working, revision)
    persisted = _persist(working.payload, previous)

    _assert_old_time_absent(persisted)
    if kind == "date":
        assert persisted["date_proposal"]["start"] == "2026-07-04"
    else:
        assert persisted["slots"]["service_id"] == "flexi haircut"


def test_revision_with_current_turn_replacement_persists_only_replacement():
    previous, payload = _revision_payload("date")
    payload["_current_turn_has_time"] = True
    payload["_current_turn_time"] = "11:00"
    payload["time_proposal"] = dict(NEW_TIME)
    payload["facts"]["times"] = ["11:00"]
    payload["facts"]["time_proposal"] = dict(NEW_TIME)
    payload["facts"].pop("time_constraint", None)
    payload["temporal"].update(
        start_time="11:00",
        end_time="11:00",
        start_time_expression="11am",
        end_time_expression="11am",
    )
    revision = detect_booking_revision(payload, previous)
    working = WorkingTurn(payload=payload, effective_collected_slots=payload["slots"])

    apply_booking_revision_mutations(working, revision)
    working.payload["slots"]["time"] = "11:00"
    working.payload["slots"]["date"] = "2026-07-04"
    working.payload["slots"]["datetime_range"] = {
        "start": "2026-07-04T11:00:00Z",
        "end": "2026-07-04T11:30:00Z",
    }
    working.payload["_effective_collected_slots"] = dict(working.payload["slots"])
    working.payload["resolved_datetime_range"] = dict(
        working.payload["slots"]["datetime_range"]
    )
    working.payload["confirmation_state"] = "pending"
    persisted = _persist(working.payload, previous)

    assert persisted["time_proposal"] == NEW_TIME
    assert persisted["slots"]["time"] == "11:00"
    assert persisted["facts"]["times"] == ["11:00"]
    assert persisted["temporal"]["start_time"] == "11:00"
    assert "10:00" not in repr(persisted)


def test_non_revision_proposal_fallback_is_unchanged():
    previous = _previous_session()
    merged = {
        "intent": {"name": "CREATE_APPOINTMENT"},
        "slots": dict(previous["slots"]),
        "facts": dict(previous["facts"]),
        "temporal": dict(previous["temporal"]),
    }

    persisted = _persist(merged, previous)

    assert persisted["time_proposal"] == OLD_TIME
    assert persisted["slots"]["time"] == "10:00"


def test_execution_proposals_cannot_reuse_revision_invalidated_time():
    previous, payload = _revision_payload("date")
    revision = detect_booking_revision(payload, previous)
    working = WorkingTurn(payload=payload, effective_collected_slots=payload["slots"])
    apply_booking_revision_mutations(working, revision)
    persisted = _persist(working.payload, previous)

    proposals = resolve_execution_proposals(
        plan={"_merged_luma_response": working.payload},
        session_state=persisted,
    )

    assert proposals["time_proposal"] is None


def test_invalidated_time_does_not_return_after_save_and_reload():
    previous, payload = _revision_payload("service")
    revision = detect_booking_revision(payload, previous)
    working = WorkingTurn(payload=payload, effective_collected_slots=payload["slots"])
    apply_booking_revision_mutations(working, revision)
    persisted = _persist(working.payload, previous)
    store = MockSessionStore()

    store.save_session(1, "revision-time-test", persisted)
    reloaded = store.get_session(1, "revision-time-test")

    _assert_old_time_absent(reloaded)
    assert resolve_execution_proposals(
        plan={"_merged_luma_response": working.payload},
        session_state=reloaded,
    )["time_proposal"] is None
