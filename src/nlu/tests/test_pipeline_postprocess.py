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
    _fix_iso_weekday_mismatch,
    _ground_service_term_in_text,
    _normalize_booking_id,
    _normalize_fuzzy_time,
    _resolve_calendar_binding_intent,
    _resolve_slot_fill_intent,
    _strip_unmentioned_dates,
    _strip_unmentioned_service,
    _strip_unmentioned_times,
    _text_mentions_date,
    _text_mentions_service,
    _text_mentions_time,
)

@pytest.mark.parametrize(
    "text,expected",
    [
        ("book haircut at 10am", False),
        ("book massage at 3pm", False),
        ("at 3pm", False),
        ("book haircut tomorrow at 3pm", True),
        ("check availability for tomorow", True),
        ("check availability for tommorow", True),
        ("check availability for tmrw", True),
        ("check availability for tomorrow", True),
        ("book haircut friday", True),
        ("book room", False),
        ("march 5 to 10", True),
        # Named-month: month-first and day-first (with/without ordinal).
        ("July 24", True),
        ("July 24th", True),
        ("24 July", True),
        ("24th July", True),
        ("book me haircut on 24th july", True),
        ("book me haircut on july 24th", True),
        ("check availability for July 21", True),
        # Non-date numerics must not look like named-month dates.
        ("haircut for 2 people", False),
        ("service 24 premium", False),
        ("room 12", False),
        ("call me at extension 24", False),
    ],
)
def test_text_mentions_date(text, expected):
    assert _text_mentions_date(text) is expected


def test_normalize_utterance_aliases_maps_tomorow():
    from nlu.extraction.entity_loading import normalize_utterance_aliases

    assert "tomorrow" in normalize_utterance_aliases(
        "check availability for tomorow"
    ).lower()
    assert normalize_utterance_aliases("check availability for tomorrow").lower() == (
        "check availability for tomorrow"
    )


def _availability_slm_with_iso(iso: str, expression: str) -> dict:
    return {
        "intent": "AVAILABILITY",
        "confidence": 0.9,
        "facts": {
            "dates": [iso],
            "times": [],
            "date_time_pairs": [],
            "service_id": None,
            "booking_id": None,
        },
        "service_term": None,
        "service_candidates": [],
        "temporal": {
            "expression": expression,
            "start_date_expression": expression,
            "start_time_expression": None,
            "end_date_expression": None,
            "end_time_expression": None,
            "start_date": iso,
            "start_time": None,
            "end_date": None,
            "end_time": None,
            "mode": "single_day",
            "confidence": 0.9,
        },
        "time_constraint": None,
    }


def test_strip_unmentioned_dates_preserves_misspelled_tomorrow():
    """Stage2 ISO for 'tomorow' must not be cleared by mention detection."""
    slm = _availability_slm_with_iso("2026-01-14", "tomorrow")
    result = _strip_unmentioned_dates("check availability for tomorow", slm)
    assert result["facts"]["dates"] == ["2026-01-14"]
    assert result["temporal"]["start_date"] == "2026-01-14"


def test_strip_unmentioned_dates_preserves_correct_tomorrow():
    slm = _availability_slm_with_iso("2026-01-14", "tomorrow")
    result = _strip_unmentioned_dates("check availability for tomorrow", slm)
    assert result["facts"]["dates"] == ["2026-01-14"]
    assert result["temporal"]["start_date"] == "2026-01-14"


def test_strip_unmentioned_dates_preserves_named_july_21():
    slm = _availability_slm_with_iso("2026-07-21", "July 21")
    result = _strip_unmentioned_dates("check availability for July 21", slm)
    assert result["facts"]["dates"] == ["2026-07-21"]
    assert result["temporal"]["start_date"] == "2026-07-21"


def test_pipeline_preserves_tomorow_through_final():
    """Misspelled relative date survives Stage2 → strip → resolver → binder → Final."""
    from nlu.pipeline import NLUPipeline

    slm = _availability_slm_with_iso("2026-01-14", "tomorrow")
    pipeline = NLUPipeline()
    pipeline._slm_extract = lambda *a, **k: slm
    result = pipeline.run(
        "check availability for tomorow",
        {"booking_mode": "service", "aliases": {}},
        now="2026-01-13T10:00:00Z",
        timezone="UTC",
    )
    assert result.facts.get("dates") == ["2026-01-14"]
    assert result.temporal is not None
    assert result.temporal.get("start_date") == "2026-01-14"
    assert result.intent["name"] == "AVAILABILITY"


def test_pipeline_preserves_tomorrow_spelling_through_final():
    from nlu.pipeline import NLUPipeline

    slm = _availability_slm_with_iso("2026-01-14", "tomorrow")
    pipeline = NLUPipeline()
    pipeline._slm_extract = lambda *a, **k: slm
    result = pipeline.run(
        "check availability for tomorrow",
        {"booking_mode": "service", "aliases": {}},
        now="2026-01-13T10:00:00Z",
        timezone="UTC",
    )
    assert result.facts.get("dates") == ["2026-01-14"]


def test_pipeline_preserves_july_21_through_final():
    from nlu.pipeline import NLUPipeline

    slm = _availability_slm_with_iso("2026-07-21", "July 21")
    pipeline = NLUPipeline()
    pipeline._slm_extract = lambda *a, **k: slm
    result = pipeline.run(
        "check availability for July 21",
        {"booking_mode": "service", "aliases": {}},
        now="2026-01-13T10:00:00Z",
        timezone="UTC",
    )
    assert result.facts.get("dates") == ["2026-07-21"]


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
        "temporal": {
            "expression": "2026-01-13 at 10:00",
            "start_date_expression": None,
            "start_time_expression": None,
            "end_date_expression": None,
            "end_time_expression": None,
            "start_date": "2026-01-13",
            "start_time": "10:00",
            "end_date": None,
            "end_time": None,
            "mode": "single_day",
            "confidence": 0.9,
        },
    }
    result = _strip_unmentioned_dates("book haircut at 10am", slm)
    assert result["facts"]["dates"] == []
    assert result["facts"]["date_time_pairs"] == []
    assert result["facts"]["times"] == ["10:00"]
    assert result["temporal"]["start_date"] is None
    assert result["temporal"]["start_time"] == "10:00"


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


def test_strip_unmentioned_dates_preserves_day_first_named_month():
    """Stage2 ISO must survive when the user said '24th july' (day-first)."""
    slm = {
        "intent": "CREATE_APPOINTMENT",
        "confidence": 0.9,
        "facts": {
            "dates": ["2026-07-24"],
            "times": [],
            "date_time_pairs": [],
            "service_id": None,
            "booking_id": None,
        },
        "temporal": {
            "expression": "2026-07-24",
            "start_date_expression": None,
            "start_time_expression": None,
            "end_date_expression": None,
            "end_time_expression": None,
            "start_date": "2026-07-24",
            "start_time": None,
            "end_date": None,
            "end_time": None,
            "mode": "single_day",
            "confidence": 0.9,
        },
    }
    result = _strip_unmentioned_dates("book me haircut on 24th july", slm)
    assert result["facts"]["dates"] == ["2026-07-24"]
    assert result["temporal"]["start_date"] == "2026-07-24"
    assert result["temporal"]["mode"] == "single_day"


@pytest.mark.parametrize(
    "text,service_term,expected",
    [
        ("12pm", "premium", None),
        ("tomorrow", "premium", None),
        ("premium", "premium", "premium"),
        ("switch to premium spa", "premium spa", "premium spa"),
        ("premium", "premium haircut", "premium"),
    ],
)
def test_ground_service_term_in_text(text, service_term, expected):
    assert _ground_service_term_in_text(text, service_term) == expected


def test_text_mentions_service_time_only():
    assert _text_mentions_service("12pm", service_term="premium") is False


def test_text_mentions_service_explicit_premium():
    assert _text_mentions_service("premium", service_term="premium") is True


def test_strip_unmentioned_service_clears_context_leaked_premium_on_12pm():
    slm = {
        "intent": "CREATE_APPOINTMENT",
        "service_term": "premium",
        "service_candidates": [],
        "facts": {"service_id": None, "times": ["12:00"]},
    }
    result = _strip_unmentioned_service("12pm", slm)
    assert result["service_term"] is None
    assert result.get("service_candidates") == []


def test_strip_unmentioned_service_preserves_premium_in_utterance():
    slm = {
        "intent": "CREATE_APPOINTMENT",
        "service_term": "premium",
        "facts": {"service_id": None},
    }
    result = _strip_unmentioned_service("premium", slm)
    assert result["service_term"] == "premium"


def test_strip_unmentioned_service_clears_facts_service_id_on_tomorrow():
    slm = {
        "intent": "AVAILABILITY",
        "service_term": None,
        "facts": {"service_id": "premium haircut", "dates": ["2026-07-03"]},
    }
    result = _strip_unmentioned_service("tomorrow", slm)
    assert result["facts"]["service_id"] is None


@pytest.mark.parametrize(
    "text,expected",
    [
        ("show availability for flexi", False),
        ("book me a premium haircut", False),
        ("9am", True),
        ("at 9", True),
        ("9", True),
        ("9:30", True),
        ("switch to 10am", True),
        ("tomorrow morning", True),
        ("in the evening", True),
        ("at noon", True),
        ("book haircut at 10am", True),
        ("3 o'clock", True),
        ("by 12pm", True),
    ],
)
def test_text_mentions_time(text, expected):
    assert _text_mentions_time(text) is expected


def _slm_with_leaked_time(time_value: str = "09:00") -> dict:
    return {
        "intent": "AVAILABILITY",
        "confidence": 0.95,
        "facts": {
            "dates": [],
            "times": [time_value],
            "date_time_pairs": [],
            "service_id": "flexi haircut + prunning",
            "booking_id": None,
        },
        "temporal": {
            "expression": time_value,
            "start_date_expression": None,
            "start_time_expression": None,
            "end_date_expression": None,
            "end_time_expression": None,
            "start_date": None,
            "start_time": time_value,
            "end_date": None,
            "end_time": None,
            "mode": "none",
            "confidence": 0.75,
        },
        "time_constraint": {
            "mode": "exact",
            "start": time_value,
            "end": time_value,
            "label": None,
        },
    }


def test_strip_unmentioned_times_clears_context_leak_on_service_revision():
    """Prior-turn 9am must not remain as current-turn time for a service-only ask."""
    slm = _slm_with_leaked_time()
    result = _strip_unmentioned_times("show availability for flexi", slm)
    assert result["facts"]["times"] == []
    assert result["temporal"]["start_time"] is None
    assert result["temporal"]["expression"] is None
    assert result["time_constraint"] is None
    assert result["facts"]["service_id"] == "flexi haircut + prunning"


def test_strip_unmentioned_times_preserves_explicit_clock():
    slm = _slm_with_leaked_time("10:00")
    result = _strip_unmentioned_times("book haircut at 10am", slm)
    assert result["facts"]["times"] == ["10:00"]
    assert result["temporal"]["start_time"] == "10:00"


def test_strip_unmentioned_times_preserves_bare_hour_slot_fill():
    slm = _slm_with_leaked_time("09:00")
    result = _strip_unmentioned_times("9", slm)
    assert result["facts"]["times"] == ["09:00"]
    assert result["temporal"]["start_time"] == "09:00"


def test_strip_unmentioned_times_preserves_fuzzy_period():
    slm = {
        "intent": "CREATE_APPOINTMENT",
        "facts": {
            "dates": [],
            "times": [],
            "date_time_pairs": [],
            "service_id": None,
            "booking_id": None,
        },
        "temporal": {
            "expression": "evening",
            "start_date_expression": None,
            "start_time_expression": "evening",
            "end_date_expression": None,
            "end_time_expression": None,
            "start_date": None,
            "start_time": None,
            "end_date": None,
            "end_time": None,
            "mode": "none",
            "confidence": 0.9,
        },
        "time_constraint": {
            "mode": "fuzzy",
            "label": "evening",
            "start": "17:00",
            "end": "21:59",
        },
    }
    result = _strip_unmentioned_times("book something this evening", slm)
    assert result["temporal"]["start_time_expression"] == "evening"


def test_strip_unmentioned_times_keeps_date_clears_leaked_clock():
    slm = {
        "intent": "AVAILABILITY",
        "facts": {
            "dates": ["2026-07-03"],
            "times": ["09:00"],
            "date_time_pairs": [{"date": "2026-07-03", "time": "09:00"}],
            "service_id": None,
            "booking_id": None,
        },
        "temporal": {
            "expression": "tomorrow",
            "start_date_expression": "tomorrow",
            "start_time_expression": None,
            "end_date_expression": None,
            "end_time_expression": None,
            "start_date": "2026-07-03",
            "start_time": "09:00",
            "end_date": None,
            "end_time": None,
            "mode": "single_day",
            "confidence": 0.9,
        },
        "time_constraint": {
            "mode": "exact",
            "start": "09:00",
            "end": "09:00",
            "label": None,
        },
    }
    result = _strip_unmentioned_times("show availability tomorrow", slm)
    assert result["temporal"]["start_date"] == "2026-07-03"
    assert result["temporal"]["start_time"] is None
    assert result["facts"]["times"] == []
    assert result["time_constraint"] is None


def test_pipeline_strips_context_leaked_time_through_final():
    from nlu.pipeline import NLUPipeline

    slm = _slm_with_leaked_time()
    pipeline = NLUPipeline()
    pipeline._slm_extract = lambda *a, **k: slm
    result = pipeline.run(
        "show availability for a different service",
        {
            "booking_mode": "service",
            "aliases": {"flexi haircut + prunning": "haircut"},
        },
        now="2026-07-02T10:00:00Z",
        timezone="UTC",
    )
    assert result.facts.get("times") in ([], None)
    assert result.temporal is None or result.temporal.get("start_time") in (None, "")
    assert result.time_constraint is None


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


def test_pipeline_preserves_reject_action_despite_cancel_keyword():
    """Post-Stage-2 normalisation must not rewrite REJECT_ACTION → CANCEL_BOOKING."""
    from nlu.pipeline import NLUPipeline

    slm = {
        "intent": "REJECT_ACTION",
        "confidence": 0.95,
        "facts": {
            "dates": [],
            "times": [],
            "date_time_pairs": [],
            "service_id": None,
            "booking_id": None,
        },
        "temporal": {
            "expression": None,
            "start_date_expression": None,
            "start_time_expression": None,
            "end_date_expression": None,
            "end_time_expression": None,
            "start_date": None,
            "start_time": None,
            "end_date": None,
            "end_time": None,
            "mode": "none",
            "confidence": 0.0,
        },
        "service_term": None,
        "service_candidates": [],
        "time_constraint": None,
        "search_query": None,
    }
    pipeline = NLUPipeline()
    pipeline._slm_extract = lambda *a, **k: slm
    result = pipeline.run(
        "Cancel that",
        {"booking_mode": "service", "aliases": {}},
        now="2026-08-03T12:00:00",
        timezone="UTC",
    )
    assert result.intent["name"] == "REJECT_ACTION"


def test_pipeline_preserves_cancel_booking_when_stage2_emits_it():
    from nlu.pipeline import NLUPipeline

    slm = {
        "intent": "CANCEL_BOOKING",
        "confidence": 0.95,
        "facts": {
            "dates": [],
            "times": [],
            "date_time_pairs": [],
            "service_id": None,
            "booking_id": None,
        },
        "temporal": {
            "expression": None,
            "start_date_expression": None,
            "start_time_expression": None,
            "end_date_expression": None,
            "end_time_expression": None,
            "start_date": None,
            "start_time": None,
            "end_date": None,
            "end_time": None,
            "mode": "none",
            "confidence": 0.0,
        },
        "service_term": None,
        "service_candidates": [],
        "time_constraint": None,
        "search_query": None,
    }
    pipeline = NLUPipeline()
    pipeline._slm_extract = lambda *a, **k: slm
    result = pipeline.run(
        "Cancel my booking",
        {"booking_mode": "service", "aliases": {}},
        now="2026-08-03T12:00:00",
        timezone="UTC",
    )
    assert result.intent["name"] == "CANCEL_BOOKING"


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


class TestFixIsoWeekdayMismatch:
    def _slm(self, dates):
        return {"intent": "CORRECTION", "facts": {"dates": dates}}

    def test_corrects_saturday_after_friday_anchor(self):
        slm = self._slm(["2026-01-18"])
        ctx = {"last_date_proposal": {"mode": "single_day", "start": "2026-01-16"}}
        result = _fix_iso_weekday_mismatch("no saturday instead", slm, ctx)
        assert result["facts"]["dates"] == ["2026-01-17"]

    def test_noop_when_iso_matches_weekday(self):
        slm = self._slm(["2026-01-17"])
        ctx = {"last_date_proposal": {"start": "2026-01-16"}}
        result = _fix_iso_weekday_mismatch("no saturday instead", slm, ctx)
        assert result["facts"]["dates"] == ["2026-01-17"]

    def test_noop_without_weekday_in_text(self):
        slm = self._slm(["2026-01-18"])
        ctx = {"last_date_proposal": {"start": "2026-01-16"}}
        result = _fix_iso_weekday_mismatch("no make it later", slm, ctx)
        assert result["facts"]["dates"] == ["2026-01-18"]

    def test_resets_to_bare_weekday_without_anchor(self):
        slm = self._slm(["2026-01-18"])
        result = _fix_iso_weekday_mismatch("no saturday instead", slm, None)
        assert result["facts"]["dates"] == ["saturday"]


class TestResolveCalendarBindingIntent:
    def test_unknown_slot_fill_uses_session_create_reservation(self):
        facts = {"dates": ["march 10", "march 15"]}
        ctx = {"last_intent": "CREATE_RESERVATION"}
        assert (
            _resolve_calendar_binding_intent("UNKNOWN", facts, ctx)
            == "CREATE_RESERVATION"
        )

    def test_unknown_without_session_intent_unchanged(self):
        facts = {"dates": ["march 10", "march 15"]}
        assert _resolve_calendar_binding_intent("UNKNOWN", facts, None) == "UNKNOWN"

    def test_create_reservation_unchanged(self):
        facts = {"dates": ["march 10", "march 15"]}
        assert (
            _resolve_calendar_binding_intent("CREATE_RESERVATION", facts, None)
            == "CREATE_RESERVATION"
        )

    def test_unknown_without_dates_unchanged(self):
        ctx = {"last_intent": "CREATE_RESERVATION"}
        assert _resolve_calendar_binding_intent("UNKNOWN", {"dates": []}, ctx) == "UNKNOWN"

    def test_correction_date_update_uses_session_intent(self):
        facts = {"dates": ["saturday"]}
        ctx = {"last_intent": "CREATE_APPOINTMENT"}
        assert (
            _resolve_calendar_binding_intent("CORRECTION", facts, ctx)
            == "CREATE_APPOINTMENT"
        )

    def test_calendar_bind_uses_active_booking_intent_after_quote_detour(self):
        facts = {"dates": ["march 10", "march 15"]}
        ctx = {
            "last_intent": "QUOTE",
            "active_booking_intent": "CREATE_RESERVATION",
        }
        assert (
            _resolve_calendar_binding_intent("UNKNOWN", facts, ctx)
            == "CREATE_RESERVATION"
        )


class TestResolveSlotFillIntent:
    def test_promotes_unknown_with_booking_last_intent(self):
        slm = {
            "intent": "UNKNOWN",
            "facts": {"dates": ["tomorrow"], "times": []},
        }
        ctx = {"last_intent": "CREATE_APPOINTMENT"}
        result = _resolve_slot_fill_intent(slm, "tomorrow", ctx)
        assert result["intent"] == "CREATE_APPOINTMENT"

    def test_no_promotion_without_context(self):
        slm = {"intent": "UNKNOWN", "facts": {"dates": ["tomorrow"]}}
        assert _resolve_slot_fill_intent(slm, "tomorrow", None)["intent"] == "UNKNOWN"

    def test_no_promotion_after_quote_last_intent_without_active_booking(self):
        slm = {"intent": "UNKNOWN", "facts": {"dates": ["tomorrow"]}}
        ctx = {"last_intent": "QUOTE"}
        assert _resolve_slot_fill_intent(slm, "tomorrow", ctx)["intent"] == "UNKNOWN"

    def test_promotion_via_active_booking_after_quote_detour(self):
        slm = {
            "intent": "UNKNOWN",
            "facts": {"dates": ["tomorrow"], "times": ["17:00"]},
        }
        ctx = {
            "last_intent": "QUOTE",
            "active_booking_intent": "CREATE_APPOINTMENT",
        }
        result = _resolve_slot_fill_intent(slm, "tomorrow at 5pm", ctx)
        assert result["intent"] == "CREATE_APPOINTMENT"

    def test_no_promotion_with_booking_verb(self):
        slm = {"intent": "UNKNOWN", "facts": {"dates": ["tomorrow"]}}
        ctx = {"last_intent": "CREATE_APPOINTMENT"}
        result = _resolve_slot_fill_intent(slm, "book for tomorrow", ctx)
        assert result["intent"] == "UNKNOWN"

    def test_no_promotion_on_correction_phrase(self):
        slm = {"intent": "UNKNOWN", "facts": {"dates": ["friday"]}}
        ctx = {"last_intent": "CREATE_APPOINTMENT"}
        result = _resolve_slot_fill_intent(slm, "wait I meant friday", ctx)
        assert result["intent"] == "UNKNOWN"

    def test_promotes_unknown_in_flow_gibberish_without_slot_material(self):
        slm = {
            "intent": "UNKNOWN",
            "facts": {
                "dates": [],
                "times": [],
                "date_time_pairs": [],
                "service_id": None,
                "booking_id": None,
            },
        }
        ctx = {"last_intent": "CREATE_APPOINTMENT"}
        result = _resolve_slot_fill_intent(slm, "aaaa", ctx)
        assert result["intent"] == "CREATE_APPOINTMENT"
