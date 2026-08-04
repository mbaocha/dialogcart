"""LumaClient test_now wire behaviour (production omit vs test include)."""

from __future__ import annotations

from typing import Any, Dict, Optional
from unittest.mock import MagicMock

import pytest

from core.adapters.nlu.luma_client import LumaClient
from core.tests.harness.test_clock import LUMA_TEST_NOW_ENV, TEST_NOW_ISO


class _FakeResponse:
    def __init__(self, payload: Dict[str, Any]):
        self._payload = payload
        self.status_code = 200
        self.text = ""

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Dict[str, Any]:
        return self._payload


def _client_with_capture() -> tuple[LumaClient, list]:
    captured: list = []
    client = LumaClient(base_url="http://luma.test")
    mock_http = MagicMock()

    def _post(url: str, json: Optional[Dict[str, Any]] = None, **_kwargs: Any):
        captured.append({"url": url, "json": json})
        return _FakeResponse({"intent": {"name": "UNKNOWN"}, "facts": {}})

    mock_http.post.side_effect = _post
    client._client = mock_http
    return client, captured


def test_luma_client_omits_test_now_when_unset(monkeypatch):
    monkeypatch.delenv(LUMA_TEST_NOW_ENV, raising=False)
    client, captured = _client_with_capture()
    client.resolve(user_id="u1", text="hello")
    assert len(captured) == 1
    assert "test_now" not in captured[0]["json"]


def test_luma_client_sends_test_now_from_env(monkeypatch):
    monkeypatch.setenv(LUMA_TEST_NOW_ENV, TEST_NOW_ISO)
    client, captured = _client_with_capture()
    client.resolve(user_id="u1", text="hello")
    assert captured[0]["json"]["test_now"] == TEST_NOW_ISO


def test_luma_client_explicit_test_now_overrides_env(monkeypatch):
    monkeypatch.setenv(LUMA_TEST_NOW_ENV, "1999-01-01T00:00:00Z")
    client, captured = _client_with_capture()
    client.resolve(user_id="u1", text="hello", test_now=TEST_NOW_ISO)
    assert captured[0]["json"]["test_now"] == TEST_NOW_ISO


def test_shared_clock_matches_e2e_frozen_time():
    from core.tests.e2e.framework.conversation import FROZEN_TIME
    from core.tests.harness.test_clock import FROZEN_TIME as SHARED, TEST_NOW

    assert FROZEN_TIME == SHARED == TEST_NOW
