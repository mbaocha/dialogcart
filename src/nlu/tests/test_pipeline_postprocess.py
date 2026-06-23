"""Unit tests for NLU pipeline post-processing helpers."""

import sys
from unittest.mock import MagicMock

import pytest

sys.modules.setdefault("anthropic", MagicMock())

from nlu.config.booking_id import (
    get_booking_id_settings,
    is_valid_booking_id,
    scan_booking_id_from_text,
)
from nlu.pipeline import (  # noqa: E402
    _apply_booking_mode_intent,
    _normalize_booking_id,
    _normalize_cancel_intent,
    _normalize_fuzzy_time,
    _strip_unmentioned_dates,
    _text_mentions_date,
)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("book haircut at 10am", False),
        ("book massage at 3pm", False),
        ("at 3pm", False),
        ("book haircut tomorrow at 3pm", True),
        ("book haircut friday", True),
        ("book room", False),
        ("march 5 to 10", True),
    ],
)
def test_text_mentions_date(text, expected):
    assert _text_mentions_date(text) is expected


def test_strip_unmentioned_dates_removes_hallucinated_date():
    slm = {
        "intent": "CREATE_APPOINTMENT",
        "facts": {
            "dates": ["2026-01-13"],
            "times": ["10:00"],
            "date_time_pairs": [{"date": "2026-01-13", "time": "10:00"}],
            "service_id": "haircut",
            "booking_id": None,
        },
    }
    result = _strip_unmentioned_dates("book haircut at 10am", slm)
    assert result["facts"]["dates"] == []
    assert result["facts"]["date_time_pairs"] == []
    assert result["facts"]["times"] == ["10:00"]


def test_strip_unmentioned_dates_preserves_explicit_date():
    slm = {
        "intent": "CREATE_APPOINTMENT",
        "facts": {
            "dates": ["tomorrow"],
            "times": ["15:00"],
            "date_time_pairs": [],
            "service_id": "massage",
            "booking_id": None,
        },
    }
    result = _strip_unmentioned_dates("book massage tomorrow at 3pm", slm)
    assert result["facts"]["dates"] == ["tomorrow"]


def test_apply_booking_mode_promotes_book_room():
    slm = {
        "intent": "UNKNOWN",
        "facts": {
            "service_id": "room",
            "dates": [],
            "times": [],
            "date_time_pairs": [],
            "booking_id": None,
        },
    }
    ctx = {"booking_mode": "reservation", "aliases": {"room": "room"}}
    result = _apply_booking_mode_intent("book room", slm, ctx)
    assert result["intent"] == "CREATE_RESERVATION"


def test_apply_booking_mode_ignored_for_service():
    slm = {"intent": "UNKNOWN", "facts": {"service_id": "haircut"}}
    ctx = {"booking_mode": "service", "aliases": {"haircut": "haircut"}}
    result = _apply_booking_mode_intent("book haircut", slm, ctx)
    assert result["intent"] == "UNKNOWN"


class TestNormalizeFuzzyTime:
    def _slm(self, times=None, tc=None):
        return {
            "intent": "CREATE_APPOINTMENT",
            "facts": {"dates": [], "times": times or [], "booking_id": None},
            "time_constraint": tc,
        }

    def test_evening_clears_concrete_time(self):
        slm = self._slm(
            times=["20:00"],
            tc={"mode": "exact", "start": "20:00", "end": "20:00", "label": None},
        )
        result = _normalize_fuzzy_time("friday evening", slm)
        assert result["facts"]["times"] == []
        assert result["time_constraint"]["mode"] == "fuzzy"
        assert result["time_constraint"]["label"] == "evening"

    def test_explicit_clock_with_evening_unchanged(self):
        slm = self._slm(times=["19:00"])
        result = _normalize_fuzzy_time("friday evening at 7pm", slm)
        assert result is slm


class TestNormalizeCancelIntent:
    def _slm(self, intent="UNKNOWN"):
        return {"intent": intent, "facts": {}}

    def test_cancel_promoted(self):
        result = _normalize_cancel_intent("nevermind cancel", self._slm())
        assert result["intent"] == "CANCEL_BOOKING"

    def test_negated_cancel_passthrough(self):
        slm = self._slm("CREATE_APPOINTMENT")
        result = _normalize_cancel_intent("please do not cancel it", slm)
        assert result is slm


class TestBookingIdConfig:
    def test_default_pattern_accepts_abc123_case_insensitive(self):
        validate_re, scan_re, _ = get_booking_id_settings({})
        assert is_valid_booking_id("ABC123", validate_re)
        assert is_valid_booking_id("abc123", validate_re)
        assert scan_booking_id_from_text("cancel booking abc123", validate_re, scan_re) == "abc123"

    def test_tenant_pattern_override(self):
        ctx = {"booking_id": {"pattern": r"^BK-\d{4}$", "scan_pattern": r"\bBK-\d{4}\b"}}
        validate_re, scan_re, examples = get_booking_id_settings(ctx)
        assert is_valid_booking_id("BK-1234", validate_re)
        assert examples == []
        found = scan_booking_id_from_text("my ref is BK-1234 please", validate_re, scan_re)
        assert found == "BK-1234"


class TestNormalizeBookingId:
    def _slm(self, booking_id=None):
        return {
            "intent": "CANCEL_BOOKING",
            "facts": {
                "dates": [],
                "times": [],
                "date_time_pairs": [],
                "service_id": None,
                "booking_id": booking_id,
            },
            "time_constraint": None,
        }

    def test_no_booking_id_is_passthrough(self):
        slm = self._slm(None)
        result = _normalize_booking_id("cancel my booking", slm, {})
        assert result is slm

    def test_standalone_valid_id_kept(self):
        result = _normalize_booking_id("ABC123", self._slm("ABC123"), {})
        assert result["facts"]["booking_id"] == "ABC123"

    def test_standalone_lowercase_id_kept(self):
        result = _normalize_booking_id("abc123", self._slm("abc123"), {})
        assert result["facts"]["booking_id"] == "abc123"

    def test_bare_booking_phrase_keeps_valid_id_any_case(self):
        result = _normalize_booking_id("booking abc123", self._slm("abc123"), {})
        assert result["facts"]["booking_id"] == "abc123"

    def test_bare_booking_phrase_prefers_text_casing_over_haiku(self):
        result = _normalize_booking_id("booking abc123", self._slm("ABC123"), {})
        assert result["facts"]["booking_id"] == "abc123"

    def test_bare_booking_phrase_keeps_valid_id(self):
        result = _normalize_booking_id("booking ABC123", self._slm("ABC123"), {})
        assert result["facts"]["booking_id"] == "ABC123"

    def test_regex_scan_without_haiku(self):
        result = _normalize_booking_id("booking ABC123", self._slm(None), {})
        assert result["facts"]["booking_id"] == "ABC123"

    def test_hash_anchor_keeps_id(self):
        result = _normalize_booking_id("cancel #ABC123", self._slm("ABC123"), {})
        assert result["facts"]["booking_id"] == "ABC123"

    def test_booking_id_colon_anchor_keeps_id(self):
        result = _normalize_booking_id("booking id: ABC123", self._slm("ABC123"), {})
        assert result["facts"]["booking_id"] == "ABC123"

    def test_tenant_override_allows_custom_format(self):
        ctx = {"booking_id": {"pattern": r"^BK-\d{4}$", "scan_pattern": r"\bBK-\d{4}\b"}}
        result = _normalize_booking_id("booking BK-9999", self._slm(None), ctx)
        assert result["facts"]["booking_id"] == "BK-9999"

    def test_does_not_mutate_input(self):
        slm = self._slm("ABC123")
        before = slm["facts"]["booking_id"]
        _normalize_booking_id("booking ABC123", slm, {})
        assert slm["facts"]["booking_id"] == before
