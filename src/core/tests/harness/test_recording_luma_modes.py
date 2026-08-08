"""Fail-closed mode contract for the E2E recording Luma client."""

from __future__ import annotations

import json

import pytest

from core.tests.harness.recording_luma_client import (
    RECACHE_ENV,
    RECORD_ENV,
    LumaRecordingMissError,
    RecordingLumaClient,
    build_recording_key,
    recording_filename,
    recording_luma_mode,
)


def _request(client: RecordingLumaClient):
    return client.resolve(
        user_id="u",
        text="book a haircut",
        domain="service",
        timezone="UTC",
        tenant_context={},
        conversation_context={},
    )


def _path(tmp_path):
    key = build_recording_key(
        text="book a haircut",
        domain="service",
        timezone="UTC",
        tenant_context={},
        conversation_context={},
    )
    return tmp_path / recording_filename(key), key


def test_replay_hit_does_not_construct_live_client(tmp_path):
    path, key = _path(tmp_path)
    response = {"intent": {"name": "CREATE_APPOINTMENT"}}
    path.write_text(json.dumps({"key": key, "response": response}), encoding="utf-8")

    client = RecordingLumaClient(
        recordings_dir=tmp_path,
        live_client_factory=lambda: pytest.fail("constructed live client"),
        mode="replay",
    )

    assert _request(client) == response
    assert client.last_cache_hit is True


def test_replay_miss_fails_closed_without_creating_file(tmp_path):
    path, _ = _path(tmp_path)
    client = RecordingLumaClient(
        recordings_dir=tmp_path,
        live_client_factory=lambda: pytest.fail("constructed live client"),
        mode="replay",
    )

    with pytest.raises(LumaRecordingMissError, match="--record-luma"):
        _request(client)

    assert not path.exists()
    assert list(tmp_path.iterdir()) == []


def test_explicit_record_mode_delegates_and_saves(tmp_path):
    calls = []

    class Live:
        def resolve(self, **kwargs):
            calls.append(kwargs)
            return {"intent": {"name": "CREATE_APPOINTMENT"}}

    client = RecordingLumaClient(
        recordings_dir=tmp_path,
        live_client_factory=Live,
        mode="record",
    )

    assert _request(client)["intent"]["name"] == "CREATE_APPOINTMENT"
    assert len(calls) == 1
    assert client.last_recording_path.is_file()


def test_mode_defaults_to_replay_and_explicit_envs_are_mutually_exclusive(monkeypatch):
    monkeypatch.delenv(RECORD_ENV, raising=False)
    monkeypatch.delenv(RECACHE_ENV, raising=False)
    monkeypatch.setenv("DIALOGCART_LUMA_MODE", "live")
    assert recording_luma_mode() == "replay"

    monkeypatch.setenv(RECORD_ENV, "1")
    assert recording_luma_mode() == "record"

    monkeypatch.setenv(RECACHE_ENV, "1")
    with pytest.raises(ValueError, match="cannot both be enabled"):
        recording_luma_mode()


def test_recache_is_explicitly_gated(monkeypatch):
    monkeypatch.delenv(RECORD_ENV, raising=False)
    monkeypatch.delenv(RECACHE_ENV, raising=False)
    assert recording_luma_mode() == "replay"
    monkeypatch.setenv(RECACHE_ENV, "true")
    assert recording_luma_mode() == "recache"
