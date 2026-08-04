"""Unit tests for Temporal ownership + legacy projection."""

import sys
from unittest.mock import MagicMock

sys.modules.setdefault("anthropic", MagicMock())

from nlu.temporal import (
    Temporal,
    build_temporal_from_stage2,
    materialize_temporal_ownership,
    project_legacy_from_temporal,
)
from nlu.stages.stage2.groups import availability as availability_group
from nlu.stages.stage2.groups import create as create_group


def test_temporal_to_dict_preserves_nulls():
    t = Temporal(start_date="2026-07-19", confidence=0.9, mode="single_day")
    d = t.to_dict()
    assert d["start_date"] == "2026-07-19"
    assert d["end_date"] is None
    assert d["start_time"] is None
    assert d["mode"] == "single_day"
    assert d["confidence"] == 0.9


def test_project_prefers_iso_over_expression():
    t = Temporal(
        start_date_expression="tomorrow",
        start_date="2026-07-08",
        start_time="09:00",
        mode="single_day",
    )
    legacy = project_legacy_from_temporal(t)
    assert legacy["dates"] == ["2026-07-08"]
    assert legacy["date_time_pairs"] == [{"date": "2026-07-08", "time": "09:00"}]


def test_project_relative_date_and_exact_time():
    t = Temporal(
        expression="tomorrow at 09:00",
        start_date_expression="tomorrow",
        start_time="09:00",
        confidence=0.95,
    )
    legacy = project_legacy_from_temporal(t)
    assert legacy["dates"] == ["tomorrow"]
    assert legacy["times"] == ["09:00"]
    assert legacy["date_time_pairs"] == [{"date": "tomorrow", "time": "09:00"}]
    assert legacy["time_constraint"] == {
        "mode": "exact",
        "start": "09:00",
        "end": "09:00",
        "label": None,
    }


def test_project_iso_range():
    t = Temporal(
        start_date="2026-07-09",
        end_date="2026-07-11",
        confidence=0.9,
    )
    legacy = project_legacy_from_temporal(t)
    assert legacy["dates"] == ["2026-07-09", "2026-07-11"]
    assert legacy["times"] == []
    assert legacy["date_time_pairs"] == []
    assert legacy["time_constraint"] is None


def test_project_fuzzy_afternoon():
    t = Temporal(
        start_date="2026-07-19",
        start_time_expression="afternoon",
        confidence=0.7,
    )
    legacy = project_legacy_from_temporal(t)
    assert legacy["dates"] == ["2026-07-19"]
    assert legacy["times"] == []
    assert legacy["date_time_pairs"] == []
    assert legacy["time_constraint"]["mode"] == "fuzzy"
    assert legacy["time_constraint"]["label"] == "afternoon"
    assert legacy["time_constraint"]["start"] == "12:00"
    assert legacy["time_constraint"]["end"] == "16:59"


def test_roundtrip_legacy_through_temporal():
    facts = {
        "dates": ["tomorrow"],
        "times": ["09:00"],
        "date_time_pairs": [{"date": "tomorrow", "time": "09:00"}],
    }
    tc = {"mode": "exact", "start": "09:00", "end": "09:00", "label": None}
    temporal = build_temporal_from_stage2(facts, tc, confidence=0.9)
    legacy = project_legacy_from_temporal(temporal)
    assert legacy["dates"] == ["tomorrow"]
    assert legacy["times"] == ["09:00"]
    assert legacy["date_time_pairs"] == [{"date": "tomorrow", "time": "09:00"}]
    assert legacy["time_constraint"]["mode"] == "exact"
    assert legacy["time_constraint"]["start"] == "09:00"


def test_materialize_prefers_temporal_tool_payload():
    raw = {
        "confidence": 0.91,
        "temporal": {
            "expression": "tomorrow at 09:00",
            "start_date_expression": "tomorrow",
            "start_time_expression": None,
            "end_date_expression": None,
            "end_time_expression": None,
            "start_date": None,
            "start_time": "09:00",
            "end_date": None,
            "end_time": None,
            "confidence": 0.91,
        },
        # Deliberately conflicting legacy — must be ignored when temporal present.
        "facts": {"dates": ["2020-01-01"], "times": [], "date_time_pairs": []},
        "time_constraint": None,
    }
    temporal, facts_frag, tc = materialize_temporal_ownership(raw, confidence=0.91)
    assert facts_frag["dates"] == ["tomorrow"]
    assert facts_frag["times"] == ["09:00"]
    assert tc["start"] == "09:00"
    assert temporal["start_date_expression"] == "tomorrow"


def test_materialize_without_temporal_is_empty():
    raw = {
        "confidence": 0.8,
        "facts": {
            "dates": ["2026-07-08"],
            "times": [],
            "date_time_pairs": [],
        },
        "time_constraint": None,
    }
    temporal, facts_frag, tc = materialize_temporal_ownership(raw, confidence=0.8)
    assert facts_frag["dates"] == []
    assert temporal["start_date"] is None
    assert temporal["mode"] in (None, "none")
    assert tc is None


def test_materialize_repairs_by_end_time_only_to_exact_point():
    """LLM sometimes emits by-X as end_time only; contract requires start=end."""
    raw = {
        "confidence": 0.95,
        "temporal": {
            "expression": "tomorrow by 12pm",
            "start_date_expression": "tomorrow",
            "start_time_expression": None,
            "end_date_expression": None,
            "end_time_expression": None,
            "start_date": "2026-07-02",
            "start_time": None,
            "end_date": None,
            "end_time": "12:00",
            "mode": "single_day",
            "confidence": 0.95,
        },
    }
    temporal, facts_frag, tc = materialize_temporal_ownership(raw, confidence=0.95)
    assert temporal["start_time"] == "12:00"
    assert temporal["end_time"] == "12:00"
    assert facts_frag["times"] == ["12:00"]
    assert facts_frag["date_time_pairs"] == [
        {"date": "2026-07-02", "time": "12:00"}
    ]
    assert tc == {
        "mode": "exact",
        "start": "12:00",
        "end": "12:00",
        "label": None,
    }


def test_materialize_repairs_from_using_source_text_when_expression_omits_from():
    raw = {
        "confidence": 0.9,
        "temporal": {
            "expression": None,
            "start_date_expression": None,
            "start_time_expression": None,
            "end_date_expression": None,
            "end_time_expression": None,
            "start_date": None,
            "start_time": None,
            "end_date": None,
            "end_time": "15:00",
            "mode": "none",
            "confidence": 0.9,
        },
    }
    temporal, facts_frag, tc = materialize_temporal_ownership(
        raw, confidence=0.9, source_text="book from 3pm"
    )
    assert temporal["start_time"] == "15:00"
    assert temporal["end_time"] == "15:00"
    assert facts_frag["times"] == ["15:00"]
    assert tc["start"] == "15:00"
    assert tc["end"] == "15:00"


def test_materialize_does_not_treat_after_as_exact_point():
    raw = {
        "confidence": 0.9,
        "temporal": {
            "expression": "after 12pm",
            "start_date_expression": None,
            "start_time_expression": None,
            "end_date_expression": None,
            "end_time_expression": None,
            "start_date": None,
            "start_time": "12:00",
            "end_date": None,
            "end_time": "23:59",
            "mode": "none",
            "confidence": 0.9,
        },
    }
    temporal, facts_frag, tc = materialize_temporal_ownership(raw, confidence=0.9)
    assert temporal["start_time"] == "12:00"
    assert temporal["end_time"] == "23:59"
    assert facts_frag["times"] == ["12:00"]
    assert tc["start"] == "12:00"
    assert tc["end"] == "23:59"


def test_materialize_does_not_invent_start_for_end_only_without_by_from():
    raw = {
        "confidence": 0.9,
        "temporal": {
            "expression": "until noon",
            "start_date_expression": None,
            "start_time_expression": None,
            "end_date_expression": None,
            "end_time_expression": None,
            "start_date": None,
            "start_time": None,
            "end_date": None,
            "end_time": "12:00",
            "mode": "none",
            "confidence": 0.9,
        },
    }
    temporal, facts_frag, tc = materialize_temporal_ownership(raw, confidence=0.9)
    assert temporal["start_time"] is None
    assert temporal["end_time"] == "12:00"
    assert facts_frag["times"] == []
    assert tc is None


def test_materialize_does_not_treat_before_as_by_from_exact():
    raw = {
        "confidence": 0.9,
        "temporal": {
            "expression": "before 12pm",
            "start_date_expression": None,
            "start_time_expression": None,
            "end_date_expression": None,
            "end_time_expression": None,
            "start_date": None,
            "start_time": None,
            "end_date": None,
            "end_time": "12:00",
            "mode": "none",
            "confidence": 0.9,
        },
    }
    temporal, _, tc = materialize_temporal_ownership(raw, confidence=0.9)
    assert temporal["start_time"] is None
    assert temporal["end_time"] == "12:00"
    assert tc is None


def test_create_tool_schema_is_temporal_first():
    schema = create_group._TOOL["input_schema"]["properties"]
    assert "temporal" in schema
    assert "time_constraint" not in schema
    facts_props = schema["facts"]["properties"]
    assert "service_term" in facts_props
    assert "dates" not in facts_props
    assert "times" not in facts_props


def test_availability_tool_schema_is_temporal_first():
    schema = availability_group._TOOL["input_schema"]["properties"]
    assert "temporal" in schema
    assert "operation" in schema
    facts_props = schema["facts"]["properties"]
    assert "service_term" in facts_props
    assert "service_id" not in facts_props
    assert "dates" not in facts_props


def test_availability_merge_from_temporal_payload():
    raw = {
        "validated_intent": "AVAILABILITY",
        "confidence": 0.92,
        "operation": None,
        "temporal": {
            "expression": None,
            "start_date_expression": None,
            "start_time_expression": None,
            "end_date_expression": None,
            "end_time_expression": None,
            "start_date": "2026-07-08",
            "start_time": None,
            "end_date": None,
            "end_time": None,
            "confidence": 0.92,
        },
        "facts": {"service_term": "haircut"},
        "service_candidates": [],
    }
    result = availability_group._merge(raw, "AVAILABILITY")
    assert result["facts"]["dates"] == ["2026-07-08"]
    assert result["facts"]["service_id"] is None
    assert result["service_term"] == "haircut"
    assert result["temporal"]["start_date"] == "2026-07-08"
    assert "operation" not in result
