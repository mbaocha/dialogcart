"""Unit tests for Core Temporal contract helpers."""

from __future__ import annotations

from core.planning.temporal_contract import (
    empty_temporal,
    merge_temporals,
    normalize_temporal,
)


def test_merge_time_only_preserves_session_date():
    session = {
        "start_date": "2026-07-23",
        "start_date_expression": "23rd july",
        "start_time": None,
        "start_time_expression": None,
        "mode": "single_day",
        "confidence": 1.0,
    }
    current = {
        "start_time": "09:30",
        "start_time_expression": "9.30am",
        "mode": "none",
        "confidence": 1.0,
    }
    merged = merge_temporals(session, current)
    assert merged["start_date"] == "2026-07-23"
    assert merged["start_date_expression"] == "23rd july"
    assert merged["start_time"] == "09:30"
    assert merged["start_time_expression"] == "9.30am"
    assert merged["mode"] == "single_day"


def test_merge_date_only_preserves_session_time():
    session = {
        "start_date": "2026-07-23",
        "start_time": "09:30",
        "start_time_expression": "9:30am",
        "mode": "single_day",
    }
    current = {
        "start_date": "2026-07-24",
        "start_date_expression": "24th july",
        "mode": "single_day",
    }
    merged = merge_temporals(session, current)
    assert merged["start_date"] == "2026-07-24"
    assert merged["start_date_expression"] == "24th july"
    assert merged["start_time"] == "09:30"
    assert merged["start_time_expression"] == "9:30am"


def test_merge_empty_current_keeps_session():
    session = normalize_temporal(
        {
            "start_date": "2026-07-23",
            "mode": "single_day",
        }
    )
    merged = merge_temporals(session, empty_temporal())
    assert merged["start_date"] == "2026-07-23"


def test_merge_presented_option_resolution_replaces_prior_time_material():
    session = {
        "start_date": "2026-07-23",
        "start_time": "10:00",
        "start_time_expression": "10am",
        "mode": "single_day",
    }
    current = {
        "expression": "1:30 PM",
        "resolution": {
            "kind": "presented_option",
            "presentation_ref": "avp1_test",
            "option": 1,
        },
        "mode": "none",
    }

    merged = merge_temporals(session, current)

    assert merged["start_date"] == "2026-07-23"
    assert merged["start_time"] is None
    assert merged["start_time_expression"] is None
    assert merged["resolution"] == current["resolution"]


def test_merge_new_date_replaces_old_date_fields_as_pair():
    session = {
        "start_date": "2026-07-23",
        "start_date_expression": "23rd july",
        "mode": "single_day",
    }
    current = {
        "start_date": "2026-07-24",
        "start_date_expression": "24th july",
        "mode": "single_day",
    }
    merged = merge_temporals(session, current)
    assert merged["start_date"] == "2026-07-24"
    assert merged["start_date_expression"] == "24th july"
    assert merged["start_time"] is None
