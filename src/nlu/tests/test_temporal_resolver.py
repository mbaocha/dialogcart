"""Unit tests for TemporalResolver closed-vocabulary verify/repair."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from nlu.temporal import Temporal, resolve_named_month_phrase, resolve_temporal
from nlu.temporal.resolver import resolve_closed_phrase


NOW = datetime(2026, 7, 7, 10, 0, tzinfo=ZoneInfo("UTC"))  # Tuesday


def test_tomorrow_fills_iso():
    t = Temporal(start_date_expression="tomorrow", mode="single_day")
    out = resolve_temporal(t, NOW)
    assert out.start_date == "2026-07-08"
    assert out.start_date_expression == "tomorrow"
    assert out.mode == "single_day"


def test_mismatch_repair_logs_and_fixes():
    t = Temporal(
        start_date_expression="tomorrow",
        start_date="2026-07-09",  # wrong
        mode="single_day",
    )
    out = resolve_temporal(t, NOW)
    assert out.start_date == "2026-07-08"


def test_next_wednesday_calendar_week():
    # Tue 7 Jul → next Wed is 15 Jul (next Mon-based week)
    start, end, mode = resolve_closed_phrase("next wednesday", NOW)
    assert start == "2026-07-15"
    assert end is None
    assert mode == "single_day"


def test_this_friday():
    start, end, mode = resolve_closed_phrase("this friday", NOW)
    assert start == "2026-07-10"
    assert mode == "single_day"


def test_this_weekend_flexible():
    t = Temporal(start_date_expression="this weekend")
    out = resolve_temporal(t, NOW)
    assert out.start_date == "2026-07-11"
    assert out.end_date == "2026-07-12"
    assert out.mode == "flexible"


def test_next_week_flexible():
    t = Temporal(start_date_expression="next week")
    out = resolve_temporal(t, NOW)
    assert out.start_date == "2026-07-13"
    assert out.end_date == "2026-07-19"
    assert out.mode == "flexible"


def test_open_vocab_next_month_not_repaired():
    # Resolver must not invent ISO for open vocabulary
    t = Temporal(
        start_date_expression="next month",
        start_date="2026-08-01",
        mode="single_day",
    )
    out = resolve_temporal(t, NOW)
    assert out.start_date == "2026-08-01"
    assert out.start_date_expression == "next month"


def test_invalid_iso_dropped_then_refilled():
    t = Temporal(
        start_date_expression="today",
        start_date="2026-02-30",
    )
    out = resolve_temporal(t, NOW)
    assert out.start_date == "2026-07-07"


def test_timezone_boundary_tomorrow():
    # 23:30 in New York on July 7 → local date still July 7; tomorrow = July 8
    now = datetime(2026, 7, 8, 3, 30, tzinfo=ZoneInfo("UTC"))  # 23:30 EDT July 7
    local = now.astimezone(ZoneInfo("America/New_York"))
    t = Temporal(start_date_expression="tomorrow")
    out = resolve_temporal(t, local)
    assert out.start_date == "2026-07-08"


def test_iso_only_passthrough():
    t = Temporal(start_date="2026-07-22", mode="single_day")
    out = resolve_temporal(t, NOW)
    assert out.start_date == "2026-07-22"
    assert out.mode == "single_day"


@pytest.mark.parametrize(
    "expression,expected",
    [
        ("23rd july", "2026-07-23"),
        ("July 23", "2026-07-23"),
        ("23 July", "2026-07-23"),
        ("July 23rd", "2026-07-23"),
        ("23rd of july", "2026-07-23"),
        ("july the 23rd", "2026-07-23"),
    ],
)
def test_named_month_fills_iso(expression, expected):
    t = Temporal(start_date_expression=expression, mode="single_day")
    out = resolve_temporal(t, NOW)
    assert out.start_date == expected
    assert out.start_date_expression == expression
    assert out.mode == "single_day"
    assert out.end_date is None


def test_named_month_year_rollover_when_day_passed():
    # 15 Jan when today is 7 Jul → next year
    t = Temporal(start_date_expression="15th january")
    out = resolve_temporal(t, NOW)
    assert out.start_date == "2027-01-15"


def test_named_month_explicit_year():
    t = Temporal(start_date_expression="23 july 2027")
    out = resolve_temporal(t, NOW)
    assert out.start_date == "2027-07-23"


def test_named_month_mismatch_repairs_iso():
    t = Temporal(
        start_date_expression="23rd july",
        start_date="2026-07-20",
        mode="single_day",
    )
    out = resolve_temporal(t, NOW)
    assert out.start_date == "2026-07-23"


def test_named_month_phrase_helper():
    iso, mode = resolve_named_month_phrase("23rd july", NOW)
    assert iso == "2026-07-23"
    assert mode == "single_day"


@pytest.mark.parametrize(
    "text,day,expr",
    [
        ("23rd", 23, "23rd"),
        ("24th", 24, "24th"),
        ("15th", 15, "15th"),
        ("show slots for 23rd", 23, "23rd"),
        ("Show slots for 24th please", 24, "24th"),
    ],
)
def test_extract_bare_ordinal_from_utterance(text, day, expr):
    from nlu.temporal.bare_ordinal import extract_bare_ordinal_from_utterance

    assert extract_bare_ordinal_from_utterance(text) == (day, expr)


def test_extract_bare_ordinal_skips_named_month():
    from nlu.temporal.bare_ordinal import extract_bare_ordinal_from_utterance

    assert extract_bare_ordinal_from_utterance("july 23rd") is None
    assert extract_bare_ordinal_from_utterance("23rd of july") is None


def test_inject_bare_ordinal_into_empty_temporal():
    from nlu.temporal.bare_ordinal import inject_bare_ordinal_expression
    from nlu.temporal.pipeline_sync import get_temporal

    slm = {
        "intent": "AVAILABILITY",
        "temporal": {
            "mode": "none",
            "start_date": None,
            "start_date_expression": None,
            "end_date": None,
            "end_date_expression": None,
            "start_time": None,
            "start_time_expression": None,
            "end_time": None,
            "end_time_expression": None,
            "expression": None,
            "confidence": None,
        },
        "facts": {"dates": [], "times": [], "date_time_pairs": []},
    }
    out = inject_bare_ordinal_expression("show slots for 23rd", slm)
    temporal = get_temporal(out)
    assert temporal.start_date_expression == "23rd"
    assert temporal.start_date is None


def test_bare_ordinal_revision_july22_to_23rd():
    """Canonical expression + last_date_proposal → 2026-07-23."""
    from nlu.temporal.resolver import apply_bare_ordinal_revision

    ctx = {"last_date_proposal": {"start": "2026-07-22", "end": None}}
    out = apply_bare_ordinal_revision(
        Temporal(start_date_expression="23rd", mode="none"),
        conversation_context=ctx,
        now=NOW,
    )
    assert out.start_date == "2026-07-23"
    assert out.start_date_expression == "23rd"
    assert out.mode == "single_day"


@pytest.mark.parametrize(
    "expr,expected",
    [
        ("24th", "2026-07-24"),
        ("15th", "2026-07-15"),
        ("23rd", "2026-07-23"),
    ],
)
def test_bare_ordinal_revision_variants(expr, expected):
    from nlu.temporal.resolver import apply_bare_ordinal_revision

    ctx = {"last_date_proposal": {"start": "2026-07-22"}}
    out = apply_bare_ordinal_revision(
        Temporal(start_date_expression=expr, mode="none"),
        conversation_context=ctx,
        now=NOW,
    )
    assert out.start_date == expected
    assert out.mode == "single_day"


def test_bare_ordinal_from_stage2_expression_without_iso():
    from nlu.temporal.resolver import apply_bare_ordinal_revision

    ctx = {"last_date_proposal": {"start": "2026-07-22"}}
    out = apply_bare_ordinal_revision(
        Temporal(start_date_expression="23rd", mode="none"),
        conversation_context=ctx,
        now=NOW,
    )
    assert out.start_date == "2026-07-23"


def test_july_23rd_still_named_month_path():
    """Named month phrases must not depend on last_date_proposal."""
    t = Temporal(start_date_expression="july 23rd", mode="single_day")
    out = resolve_temporal(t, NOW)
    assert out.start_date == "2026-07-23"
    from nlu.temporal.resolver import apply_bare_ordinal_revision

    # Even with a different anchor, named-month ISO must win (already filled).
    out2 = apply_bare_ordinal_revision(
        out,
        conversation_context={"last_date_proposal": {"start": "2026-06-01"}},
        now=NOW,
    )
    assert out2.start_date == "2026-07-23"


def test_bare_ordinal_noop_without_anchor():
    from nlu.temporal.resolver import apply_bare_ordinal_revision

    out = apply_bare_ordinal_revision(
        Temporal(start_date_expression="23rd", mode="none"),
        conversation_context={},
        now=NOW,
    )
    assert out.start_date is None
    assert out.mode == "none"


def test_bare_ordinal_invalid_day_for_month():
    from nlu.temporal.resolver import apply_bare_ordinal_revision

    # Feb 30 is invalid
    out = apply_bare_ordinal_revision(
        Temporal(start_date_expression="30th", mode="none"),
        conversation_context={"last_date_proposal": {"start": "2026-02-10"}},
        now=NOW,
    )
    assert out.start_date is None


def test_bare_ordinal_resolver_ignores_empty_expression():
    """Resolver must not invent ordinals without a canonical expression."""
    from nlu.temporal.resolver import apply_bare_ordinal_revision

    out = apply_bare_ordinal_revision(
        Temporal(mode="none"),
        conversation_context={"last_date_proposal": {"start": "2026-07-22"}},
        now=NOW,
    )
    assert out.start_date is None


def test_bind_calendar_bare_ordinal_revision():
    """Preprocess inject + bind path: empty Stage2 temporal → ISO date."""
    from nlu.pipeline import NLUPipeline
    from nlu.temporal.bare_ordinal import inject_bare_ordinal_expression

    pipeline = NLUPipeline()
    slm = {
        "intent": "AVAILABILITY",
        "confidence": 0.9,
        "operation": None,
        "temporal": {
            "mode": "none",
            "start_date": None,
            "start_date_expression": None,
            "end_date": None,
            "end_date_expression": None,
            "start_time": None,
            "start_time_expression": None,
            "end_time": None,
            "end_time_expression": None,
            "expression": None,
            "confidence": None,
        },
        "facts": {
            "dates": [],
            "times": [],
            "date_time_pairs": [],
            "service_id": None,
            "booking_id": None,
        },
        "time_constraint": None,
        "search_query": None,
        "service_candidates": [],
        "service_term": None,
    }
    slm = inject_bare_ordinal_expression("show slots for 23rd", slm)
    result = pipeline._bind_calendar(
        slm,
        {"booking_mode": "service", "aliases": {}},
        "2026-07-07T10:00:00Z",
        conversation_context={"last_date_proposal": {"start": "2026-07-22"}},
    )
    assert "2026-07-23" in (result.facts.get("dates") or [])
