from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from unittest.mock import Mock, patch

import pytest

from core.clock import reset_core_clock, set_core_clock
from core.config.session_freshness import (
    DEFAULT_SESSION_TTL_SECONDS,
    load_session_freshness_settings,
)
from core.session.freshness import (
    AVAILABILITY_REFRESH_REASON_EXPIRED,
    AVAILABILITY_REFRESH_REASON_KEY,
    apply_load_freshness,
    stamp_availability_created,
    sync_confirmation_freshness,
)
from core.session.session_schema_v2 import (
    empty_session_v2,
    hydrate_v1_compat_shims,
    prepare_session_for_persist,
)
from core.session import session_manager
from core.workflows.availability.browse import cache_satisfiable_browse_request


class _FixedClock:
    def __init__(self, value: datetime):
        self.value = value

    def now(self) -> datetime:
        return self.value


def _at(value: datetime) -> _FixedClock:
    clock = _FixedClock(value)
    set_core_clock(clock)
    return clock


def teardown_function() -> None:
    reset_core_clock()


def _session_with_executable_evidence() -> dict:
    session = empty_session_v2()
    session["planning"].update({
        "intent_name": "CREATE_APPOINTMENT",
        "status": "AWAITING_CONFIRMATION",
        "slots": {"service_id": "26", "date": "2026-08-18", "time": "09:30"},
        "bound_datetime": {"start": "2026-08-18T09:30:00Z"},
    })
    session["availability"].update({
        "fingerprint": "fp",
        "cache": {"search_result": {"slots": [{"time": "09:30"}]}},
        "presentation": {
            "presented": {"times": ["09:30"], "_cursor": {"page_index": 2}},
            "page_index": 2,
            "page_size": 6,
        },
    })
    session["confirmation_state"] = "pending"
    return session


def test_default_and_invalid_session_ttl_are_safe(monkeypatch):
    monkeypatch.delenv("DIALOGCART_SESSION_TTL_SECONDS", raising=False)
    assert load_session_freshness_settings().session_ttl_seconds == 1200
    assert DEFAULT_SESSION_TTL_SECONDS == 1200
    for value in ("bad", "0", "-1"):
        monkeypatch.setenv("DIALOGCART_SESSION_TTL_SECONDS", value)
        assert load_session_freshness_settings().session_ttl_seconds == 1200


def test_configured_ttls_are_loaded_from_one_boundary(monkeypatch):
    monkeypatch.setenv("DIALOGCART_SESSION_TTL_SECONDS", "172800")
    monkeypatch.setenv("DIALOGCART_AVAILABILITY_TTL_SECONDS", "600")
    monkeypatch.setenv("DIALOGCART_CONFIRMATION_TTL_SECONDS", "300")
    settings = load_session_freshness_settings()
    assert settings.session_ttl_seconds == 172800
    assert settings.availability_ttl_seconds == 600
    assert settings.confirmation_ttl_seconds == 300


def test_configured_redis_ttl_is_applied_and_each_save_refreshes(monkeypatch):
    monkeypatch.setenv("DIALOGCART_SESSION_TTL_SECONDS", "172800")
    mock_redis = Mock()
    session = empty_session_v2()
    with patch.object(session_manager, "_get_redis_client", return_value=mock_redis):
        session_manager.save_session(2, "ttl-user", session)
        session_manager.save_session(2, "ttl-user", session)

    assert mock_redis.setex.call_count == 2
    assert [call.args[1] for call in mock_redis.setex.call_args_list] == [172800, 172800]


def test_redis_read_does_not_refresh_ttl(monkeypatch):
    monkeypatch.setenv("DIALOGCART_SESSION_TTL_SECONDS", "172800")
    mock_redis = Mock()
    mock_redis.get.return_value = json.dumps(empty_session_v2()).encode("utf-8")
    with patch.object(session_manager, "_get_redis_client", return_value=mock_redis):
        assert session_manager.get_session(2, "read-only") is not None

    mock_redis.get.assert_called_once()
    mock_redis.setex.assert_not_called()
    mock_redis.expire.assert_not_called()


def test_fallback_uses_configured_ttl_and_save_refreshes(monkeypatch):
    monkeypatch.setenv("DIALOGCART_SESSION_TTL_SECONDS", "60")
    start = datetime(2026, 8, 17, 10, 0, tzinfo=timezone.utc)
    clock = _at(start)
    session_manager._in_memory_sessions.clear()
    with patch.object(session_manager, "_get_redis_client", return_value=None):
        session_manager.save_session(2, "fallback-user", empty_session_v2())
        first = session_manager._in_memory_sessions["session:2:fallback-user"]["_stored_at"]
        clock.value = start + timedelta(seconds=50)
        session_manager.save_session(2, "fallback-user", empty_session_v2())
        second = session_manager._in_memory_sessions["session:2:fallback-user"]["_stored_at"]
        clock.value = start + timedelta(seconds=100)
        assert session_manager.get_session(2, "fallback-user") is not None
        clock.value = start + timedelta(seconds=111)
        assert session_manager.get_session(2, "fallback-user") is None

    assert second > first
    session_manager._in_memory_sessions.clear()


def test_fresh_availability_and_confirmation_survive_load(monkeypatch):
    monkeypatch.setenv("DIALOGCART_AVAILABILITY_TTL_SECONDS", "600")
    monkeypatch.setenv("DIALOGCART_CONFIRMATION_TTL_SECONDS", "300")
    now = datetime(2026, 8, 17, 10, 0, tzinfo=timezone.utc)
    _at(now)
    session = empty_session_v2()
    session["planning"].update({
        "intent_name": "CREATE_APPOINTMENT",
        "status": "AWAITING_CONFIRMATION",
        "slots": {"service_id": "26", "engine_type": "ev"},
        "bound_datetime": {"start": "2026-08-18T09:30:00Z"},
    })
    session["availability"].update({
        "fingerprint": "fp",
        "cache": {"search_result": {"slots": [{"start": "2026-08-18T09:30:00Z"}]}},
        "presentation": {"presented": {"times": ["09:30"]}, "page_index": 0, "page_size": 6},
    })
    session["confirmation_state"] = "pending"
    stamp_availability_created(session)
    sync_confirmation_freshness(session)

    apply_load_freshness(session)

    assert session["availability"]["fingerprint"] == "fp"
    assert session["confirmation_state"] == "pending"
    assert session["planning"]["bound_datetime"] is not None


def test_presentation_has_no_independent_freshness_record():
    session = empty_session_v2()
    assert "presentation" not in session["metadata"]["artifacts"]
    session["metadata"]["artifacts"]["presentation"] = {
        "created_at": "2099-01-01T00:00:00Z",
        "expires_at": "2099-01-01T00:20:00Z",
    }

    normalized = session_manager._normalize_loaded_session(session)

    assert "presentation" not in normalized["metadata"]["artifacts"]


@pytest.mark.parametrize(
    "record",
    [
        {"expires_at": "2026-08-17T10:10:00Z"},
        {"created_at": "not-a-date", "expires_at": "2026-08-17T10:10:00Z"},
        {
            "created_at": "2026-08-17T10:00:01Z",
            "expires_at": "2026-08-17T10:10:00Z",
        },
        {"created_at": "2026-08-17T10:00:00Z"},
        {"created_at": "2026-08-17T10:00:00Z", "expires_at": "not-a-date"},
        {
            "created_at": "2026-08-17T10:00:00Z",
            "expires_at": "2026-08-17T10:00:00Z",
        },
        {
            "created_at": "2026-08-17T10:00:00Z",
            "expires_at": "2026-08-17T09:59:59Z",
        },
    ],
    ids=[
        "missing-created-at",
        "malformed-created-at",
        "future-created-at",
        "missing-expires-at",
        "malformed-expires-at",
        "expires-equal-created",
        "expires-before-created",
    ],
)
def test_invalid_artifact_records_fail_closed_for_availability_and_confirmation(record):
    _at(datetime(2026, 8, 17, 10, 0, tzinfo=timezone.utc))
    session = _session_with_executable_evidence()
    session["metadata"]["artifacts"]["availability"] = dict(record)
    session["metadata"]["artifacts"]["confirmation"] = dict(record)

    apply_load_freshness(session)

    assert session["availability"]["cache"]["search_result"] is None
    assert session["availability"]["presentation"] == {
        "presented": None, "page_index": 0, "page_size": None
    }
    assert session["confirmation_state"] is None


def test_expiry_boundary_is_stale_and_valid_record_before_expiry_survives():
    now = datetime(2026, 8, 17, 10, 0, tzinfo=timezone.utc)
    clock = _at(now)
    fresh = _session_with_executable_evidence()
    stamp_availability_created(fresh)
    sync_confirmation_freshness(fresh)

    apply_load_freshness(fresh)
    assert fresh["availability"]["fingerprint"] == "fp"
    assert fresh["confirmation_state"] == "pending"

    boundary = _session_with_executable_evidence()
    boundary["metadata"]["artifacts"]["availability"] = {
        "created_at": "2026-08-17T09:59:00Z",
        "expires_at": "2026-08-17T10:00:00Z",
    }
    boundary["metadata"]["artifacts"]["confirmation"] = {
        "created_at": "2026-08-17T09:59:00Z",
        "expires_at": "2026-08-17T10:00:00Z",
    }
    clock.value = now

    apply_load_freshness(boundary)

    assert boundary["availability"]["fingerprint"] is None
    assert boundary["confirmation_state"] is None
    assert boundary[AVAILABILITY_REFRESH_REASON_KEY] == (
        AVAILABILITY_REFRESH_REASON_EXPIRED
    )


def test_expiry_refresh_reason_is_same_turn_only():
    _at(datetime(2026, 8, 17, 10, 0, tzinfo=timezone.utc))
    session = _session_with_executable_evidence()
    session["metadata"]["artifacts"]["availability"] = {
        "created_at": "2026-08-17T09:58:00Z",
        "expires_at": "2026-08-17T09:59:00Z",
    }

    loaded = session_manager._normalize_loaded_session(session)

    assert loaded[AVAILABILITY_REFRESH_REASON_KEY] == (
        AVAILABILITY_REFRESH_REASON_EXPIRED
    )
    persisted = prepare_session_for_persist(loaded)
    assert AVAILABILITY_REFRESH_REASON_KEY not in persisted


def test_stale_availability_fails_closed_and_preserves_non_temporal_inputs(monkeypatch):
    monkeypatch.setenv("DIALOGCART_AVAILABILITY_TTL_SECONDS", "60")
    start = datetime(2026, 8, 17, 10, 0, tzinfo=timezone.utc)
    clock = _at(start)
    session = empty_session_v2()
    session["planning"].update({
        "intent_name": "CREATE_APPOINTMENT",
        "status": "AWAITING_CONFIRMATION",
        "slots": {
            "service_id": "26",
            "engine_type": "ev",
            "registration_number": "AA21PP",
            "date": "2026-08-18",
            "time": "09:30",
        },
        "bound_datetime": {"start": "2026-08-18T09:30:00Z"},
    })
    session["availability"].update({
        "fingerprint": "fp",
        "cache": {"search_result": {"slots": [{"time": "09:30"}]}},
        "presentation": {"presented": {"times": ["09:30"]}, "page_index": 2, "page_size": 6},
    })
    session["confirmation_state"] = "pending"
    stamp_availability_created(session)
    sync_confirmation_freshness(session)
    clock.value = start + timedelta(seconds=61)

    apply_load_freshness(session)

    assert session["availability"]["fingerprint"] is None
    assert session["availability"]["cache"]["search_result"] is None
    assert session["availability"]["presentation"] == {
        "presented": None, "page_index": 0, "page_size": None
    }
    assert session["planning"]["bound_datetime"] is None
    assert session["confirmation_state"] is None
    assert session["planning"]["slots"] == {
        "service_id": "26",
        "engine_type": "ev",
        "registration_number": "AA21PP",
    }
    hydrated = hydrate_v1_compat_shims(session)
    assert "resolved_datetime_range" not in hydrated
    assert cache_satisfiable_browse_request(
        {"operation": "browse_next"}, hydrated
    ) is None


def test_stale_confirmation_cannot_authorize_execution(monkeypatch):
    monkeypatch.setenv("DIALOGCART_CONFIRMATION_TTL_SECONDS", "30")
    start = datetime(2026, 8, 17, 10, 0, tzinfo=timezone.utc)
    clock = _at(start)
    session = empty_session_v2()
    session["planning"].update({
        "intent_name": "CREATE_APPOINTMENT",
        "status": "AWAITING_CONFIRMATION",
        "slots": {"service_id": "26", "engine_type": "ev"},
    })
    session["confirmation_state"] = "pending"
    sync_confirmation_freshness(session)
    created = dict(session["metadata"]["artifacts"]["confirmation"])
    sync_confirmation_freshness(session)
    assert session["metadata"]["artifacts"]["confirmation"] == created
    clock.value = start + timedelta(seconds=31)

    apply_load_freshness(session)

    assert session["confirmation_state"] is None
    assert session["planning"]["slots"] == {"service_id": "26", "engine_type": "ev"}


def test_projector_timestamps_new_search_but_not_ordinary_save(monkeypatch):
    from core.session.session_projector import SessionProjectorV2

    monkeypatch.setenv("DIALOGCART_AVAILABILITY_TTL_SECONDS", "600")
    start = datetime(2026, 8, 17, 10, 0, tzinfo=timezone.utc)
    clock = _at(start)
    working = empty_session_v2()
    SessionProjectorV2._apply_optional_inputs(
        working,
        working_session_state=None,
        merged_luma_response=None,
        workflow_result={
            "kind": "availability_search",
            "status": "succeeded",
            "availability_fingerprint": "fp",
            "last_execution_result": {"slots": [{"time": "09:30"}]},
            "presented_availability": {"times": ["09:30"]},
            "availability_presentation": {"page_index": 0, "page_size": 6},
        },
        post_commit_transition=None,
        capability_result=None,
        handler_conversation_update=None,
        conversation_messages=None,
        assistant_proposals=None,
        assistant_proposal_updates=None,
    )
    created = dict(working["metadata"]["artifacts"]["availability"])
    clock.value = start + timedelta(seconds=30)
    SessionProjectorV2._apply_optional_inputs(
        working,
        working_session_state=working,
        merged_luma_response=None,
        workflow_result=None,
        post_commit_transition=None,
        capability_result=None,
        handler_conversation_update=None,
        conversation_messages=None,
        assistant_proposals=None,
        assistant_proposal_updates=None,
    )
    assert working["metadata"]["artifacts"]["availability"] == created


def test_session_save_refreshes_activity_but_not_availability_age():
    start = datetime(2026, 8, 17, 10, 0, tzinfo=timezone.utc)
    clock = _at(start)
    session = empty_session_v2()
    stamp_availability_created(session)
    provenance = dict(session["metadata"]["artifacts"]["availability"])
    mock_redis = Mock()

    with patch.object(session_manager, "_get_redis_client", return_value=mock_redis):
        session_manager.save_session(2, "artifact-age", session)
        clock.value = start + timedelta(seconds=30)
        session_manager.save_session(2, "artifact-age", session)

    first = json.loads(mock_redis.setex.call_args_list[0].args[2])
    second = json.loads(mock_redis.setex.call_args_list[1].args[2])
    assert first["metadata"]["artifacts"]["availability"] == provenance
    assert second["metadata"]["artifacts"]["availability"] == provenance
    assert first["metadata"]["last_activity_at"] != second["metadata"]["last_activity_at"]


def test_only_typed_new_availability_search_replaces_availability_age():
    from core.session.session_projector import SessionProjectorV2

    start = datetime(2026, 8, 17, 10, 0, tzinfo=timezone.utc)
    clock = _at(start)
    working = empty_session_v2()
    common = dict(
        merged_luma_response=None,
        post_commit_transition=None,
        capability_result=None,
        handler_conversation_update=None,
        conversation_messages=None,
        assistant_proposals=None,
        assistant_proposal_updates=None,
    )
    SessionProjectorV2._apply_optional_inputs(
        working,
        working_session_state=None,
        workflow_result={
            "kind": "availability_search",
            "status": "succeeded",
            "last_execution_result": {"slots": [{"time": "09:30"}]},
        },
        **common,
    )
    original = dict(working["metadata"]["artifacts"]["availability"])

    clock.value = start + timedelta(seconds=30)
    for workflow_result in (
        {"kind": "availability_pagination", "presented_availability": {"times": ["10:00"]}},
        {"kind": "customer_persistence", "last_execution_result": {"customer_id": 7}},
        {"kind": "booking_creation", "last_execution_result": {"booking_id": 42}},
        {"kind": "booking_hold", "last_execution_result": {"hold_id": 8}},
        {"kind": "booking_cancellation", "last_execution_result": {"cancelled": True}},
        {"kind": "booking_modification", "last_execution_result": {"modified": True}},
    ):
        SessionProjectorV2._apply_optional_inputs(
            working,
            working_session_state=working,
            workflow_result=workflow_result,
            **common,
        )
        assert working["metadata"]["artifacts"]["availability"] == original

    # Slot collection, confirmation entry, FAQ/off-topic compatibility projection,
    # and ordinary persistence supply no authoritative availability-search result.
    working["confirmation_state"] = "pending"
    SessionProjectorV2._apply_optional_inputs(
        working,
        working_session_state=working,
        workflow_result=None,
        **common,
    )
    assert working["metadata"]["artifacts"]["availability"] == original

    clock.value = start + timedelta(seconds=60)
    SessionProjectorV2._apply_optional_inputs(
        working,
        working_session_state=working,
        workflow_result={
            "kind": "availability_search",
            "status": "succeeded",
            "last_execution_result": {"slots": [{"time": "11:00"}]},
        },
        **common,
    )
    assert working["metadata"]["artifacts"]["availability"] != original
    assert working["metadata"]["artifacts"]["availability"]["created_at"] == (
        "2026-08-17T10:01:00Z"
    )


@pytest.mark.parametrize(
    "workflow_result",
    [
        {"kind": "availability_search", "status": "failed"},
        {"kind": "availability_search", "status": "succeeded"},
    ],
    ids=["failed-search", "malformed-missing-cache-result"],
)
def test_failed_or_malformed_search_clears_old_availability_without_refresh(
    workflow_result,
):
    from core.session.session_projector import SessionProjectorV2

    start = datetime(2026, 8, 17, 10, 0, tzinfo=timezone.utc)
    clock = _at(start)
    working = _session_with_executable_evidence()
    stamp_availability_created(working)
    old_provenance = dict(working["metadata"]["artifacts"]["availability"])
    clock.value = start + timedelta(seconds=30)

    SessionProjectorV2._apply_optional_inputs(
        working,
        working_session_state=working,
        merged_luma_response=None,
        workflow_result=workflow_result,
        post_commit_transition=None,
        capability_result=None,
        handler_conversation_update=None,
        conversation_messages=None,
        assistant_proposals=None,
        assistant_proposal_updates=None,
    )

    assert working["availability"]["fingerprint"] is None
    assert working["availability"]["cache"]["search_result"] is None
    assert working["availability"]["presentation"] == {
        "presented": None, "page_index": 0, "page_size": None
    }
    assert working["metadata"]["artifacts"]["availability"] is None
    assert old_provenance["created_at"] == "2026-08-17T10:00:00Z"


@pytest.mark.parametrize(
    ("replacement_slots", "presented"),
    [
        ([], {"slots": [], "_cursor": {"page_index": 0}}),
        (
            [{"starts_at": "2026-08-18T11:00:00Z", "ends_at": "2026-08-18T11:30:00Z"}],
            {"slots": [{"starts_at": "2026-08-18T11:00:00Z"}], "_cursor": {"page_index": 0}},
        ),
    ],
    ids=["successful-empty", "successful-non-empty"],
)
def test_successful_search_replaces_old_cache_presentation_and_provenance(
    replacement_slots, presented
):
    from core.session.session_projector import SessionProjectorV2

    start = datetime(2026, 8, 17, 10, 0, tzinfo=timezone.utc)
    clock = _at(start)
    working = _session_with_executable_evidence()
    stamp_availability_created(working)
    old_provenance = dict(working["metadata"]["artifacts"]["availability"])
    clock.value = start + timedelta(seconds=30)
    replacement = {"type": "availability", "status": "success", "slots": replacement_slots}

    SessionProjectorV2._apply_optional_inputs(
        working,
        working_session_state=working,
        merged_luma_response=None,
        workflow_result={
            "kind": "availability_search",
            "status": "succeeded",
            "availability_fingerprint": "new-fp",
            "last_execution_result": replacement,
            "presented_availability": presented,
            "availability_presentation": {"page_index": 0, "page_size": 6},
        },
        post_commit_transition=None,
        capability_result=None,
        handler_conversation_update=None,
        conversation_messages=None,
        assistant_proposals=None,
        assistant_proposal_updates=None,
    )

    assert working["availability"]["fingerprint"] == "new-fp"
    assert working["availability"]["cache"]["search_result"] == replacement
    assert working["availability"]["presentation"]["presented"] == presented
    assert working["availability"]["presentation"]["page_index"] == 0
    assert working["metadata"]["artifacts"]["availability"] != old_provenance
    assert working["metadata"]["artifacts"]["availability"]["created_at"] == (
        "2026-08-17T10:00:30Z"
    )


@pytest.mark.parametrize("outcome_status", ["HANDLER_DELEGATED", "OFF_TOPIC"])
def test_faq_and_off_topic_projection_do_not_refresh_availability_age(outcome_status):
    from core.session.session_projector import SessionProjectorV2

    start = datetime(2026, 8, 17, 10, 0, tzinfo=timezone.utc)
    clock = _at(start)
    working = _session_with_executable_evidence()
    stamp_availability_created(working)
    original = dict(working["metadata"]["artifacts"]["availability"])
    clock.value = start + timedelta(seconds=30)

    projected = SessionProjectorV2().project(
        outcome={},
        outcome_status=outcome_status,
        organization_id=2,
        working_session_state=working,
        workflow_result=None,
    )

    assert projected is not None
    assert projected["metadata"]["artifacts"]["availability"] == original


def test_committed_booking_is_not_reopened_by_freshness_cleanup():
    now = datetime(2026, 8, 17, 10, 0, tzinfo=timezone.utc)
    _at(now)
    session = empty_session_v2()
    session["booking"] = {"booking_id": 42, "booking_code": "ORG2-42"}

    apply_load_freshness(session)

    assert session["booking"] == {"booking_id": 42, "booking_code": "ORG2-42"}
    assert session["planning"]["intent_name"] is None
    assert session["confirmation_state"] is None
