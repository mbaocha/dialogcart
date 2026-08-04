"""RecordingLumaClient: stable keys without test_now; forward on live only."""

from __future__ import annotations

from typing import Any, Dict, Optional

from core.tests.harness.recording_luma_client import (
    RecordingLumaClient,
    build_recording_key,
    recording_filename,
)
from core.tests.harness.test_clock import LUMA_TEST_NOW_ENV, TEST_NOW_ISO


class _CaptureInner:
    def __init__(self) -> None:
        self.calls: list = []

    def resolve(self, **kwargs: Any) -> Dict[str, Any]:
        self.calls.append(kwargs)
        return {
            "intent": {"name": "UNKNOWN"},
            "facts": {},
            "temporal": {"mode": "none"},
            "turn": {"understanding": "UNDERSTOOD"},
        }

    def notify_execution(self, **kwargs: Any) -> Dict[str, Any]:
        return {}


def test_recording_key_stable_without_test_now(monkeypatch, tmp_path):
    monkeypatch.setenv(LUMA_TEST_NOW_ENV, TEST_NOW_ISO)
    key = build_recording_key(
        text="premium",
        domain="service",
        timezone="UTC",
        tenant_context={},
        conversation_context={},
        test_now=None,
    )
    assert "test_now" not in key

    inner = _CaptureInner()
    client = RecordingLumaClient(inner, recordings_dir=tmp_path)
    # Force live path (empty dir).
    client.resolve(
        user_id="u",
        text="premium",
        domain="service",
        timezone="UTC",
        tenant_context={},
        conversation_context={},
    )
    assert inner.calls and inner.calls[0].get("test_now") == TEST_NOW_ISO
    # Saved recording key must not include test_now.
    path = tmp_path / recording_filename(key)
    assert path.is_file()
    import json

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert "test_now" not in (saved.get("key") or {})


def test_replay_ignores_env_test_now(monkeypatch, tmp_path):
    monkeypatch.setenv(LUMA_TEST_NOW_ENV, TEST_NOW_ISO)
    key = build_recording_key(
        text="premium",
        domain="service",
        timezone="UTC",
        tenant_context={},
        conversation_context={},
    )
    response = {
        "intent": {"name": "CREATE_APPOINTMENT"},
        "facts": {},
        "temporal": {"mode": "none"},
        "turn": {"understanding": "UNDERSTOOD"},
    }
    path = tmp_path / recording_filename(key)
    import json

    path.write_text(
        json.dumps({"key": key, "response": response}) + "\n", encoding="utf-8"
    )

    class _Boom:
        def resolve(self, **kwargs: Any) -> Dict[str, Any]:
            raise AssertionError("must not call live on replay")

        def notify_execution(self, **kwargs: Any) -> Dict[str, Any]:
            return {}

    client = RecordingLumaClient(_Boom(), recordings_dir=tmp_path)
    out = client.resolve(
        user_id="u",
        text="premium",
        domain="service",
        timezone="UTC",
        tenant_context={},
        conversation_context={},
    )
    assert client.last_cache_hit is True
    assert out == response
