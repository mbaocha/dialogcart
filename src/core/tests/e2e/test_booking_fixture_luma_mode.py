"""Mode wiring tests for the real booking E2E dependency fixture."""

from unittest.mock import Mock

from core.tests.e2e.framework import fixtures
from core.tests.harness.recording_luma_client import RECACHE_ENV, RECORD_ENV


def test_booking_fixture_defaults_to_replay_without_constructing_live_client(
    monkeypatch,
):
    monkeypatch.delenv(RECORD_ENV, raising=False)
    monkeypatch.delenv(RECACHE_ENV, raising=False)
    monkeypatch.setattr(
        fixtures,
        "TestLumaClient",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("booking fixture constructed live Luma client")
        ),
    )

    _, _, luma_client = fixtures._wire_booking_deps(
        monkeypatch,
        availability_client=Mock(),
    )

    assert luma_client.mode == "replay"
    assert luma_client._inner is None


def test_booking_replay_fixture_does_not_probe_live_luma(monkeypatch):
    monkeypatch.delenv(RECORD_ENV, raising=False)
    monkeypatch.delenv(RECACHE_ENV, raising=False)
    monkeypatch.setattr(
        fixtures,
        "live_luma_available",
        lambda: (_ for _ in ()).throw(AssertionError("probed live Luma")),
    )

    assert fixtures.require_live_luma.__wrapped__() is None
