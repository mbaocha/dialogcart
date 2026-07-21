"""Regression coverage for ScriptedLumaClient key normalisation and miss behaviour."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core.tests.harness.clients import ScriptedLumaClient, normalize_script_key


_JULY_21_SCRIPT = {
    "success": True,
    "intent": {"name": "AVAILABILITY"},
    "facts": {"service_id": "premium haircut"},
}


def test_normalize_script_key_strips_and_lowercases():
    assert normalize_script_key("  SHOW AVAILABILITY FOR JULY 21  ") == (
        "show availability for july 21"
    )


def test_scripted_luma_matches_case_and_whitespace_variants():
    client = ScriptedLumaClient(
        {"show availability for July 21": dict(_JULY_21_SCRIPT)}
    )
    for utterance in (
        "show availability for July 21",
        "show availability for july 21",
        "  SHOW AVAILABILITY FOR JULY 21  ",
    ):
        response = client.resolve("user-1", utterance)
        assert response["intent"]["name"] == "AVAILABILITY"
        assert response["facts"]["service_id"] == "premium haircut"


def test_scripted_luma_missing_script_raises_without_live_call(monkeypatch):
    client = ScriptedLumaClient(
        {"show availability for July 21": dict(_JULY_21_SCRIPT)}
    )
    live = MagicMock(side_effect=AssertionError("live Luma must not be called"))
    monkeypatch.setattr(
        "core.adapters.nlu.LumaClient.resolve",
        live,
    )
    with pytest.raises(AssertionError) as exc_info:
        client.resolve("user-1", "completely unknown utterance")
    message = str(exc_info.value)
    assert "completely unknown utterance" in message
    assert "completely unknown utterance".strip().lower() in message
    assert "show availability for july 21" in message
    live.assert_not_called()


def test_scripted_luma_duplicate_normalised_keys_raise():
    with pytest.raises(ValueError) as exc_info:
        ScriptedLumaClient(
            {
                "Premium": {"success": True},
                "premium": {"success": True},
            }
        )
    message = str(exc_info.value)
    assert "normalised_key='premium'" in message or 'normalised_key="premium"' in message
    assert "Premium" in message
    # Both the first and conflicting originals appear; second is lowercase premium.
    assert "first_original='Premium'" in message or 'first_original="Premium"' in message
    assert (
        "conflicting_original='premium'" in message
        or 'conflicting_original="premium"' in message
    )


def test_scripted_luma_explicit_fallback_still_used():
    fallback = MagicMock()
    fallback.resolve.return_value = {"success": True, "intent": {"name": "UNKNOWN"}}
    client = ScriptedLumaClient({}, fallback=fallback)
    response = client.resolve("user-1", "unscripted")
    assert response["intent"]["name"] == "UNKNOWN"
    fallback.resolve.assert_called_once()
