"""Unit tests for the invariant tracing framework."""

from __future__ import annotations

import os

import pytest

from core.tracing.invariant_trace import (
    TRACE_ENV_VAR,
    TurnInvariantTrace,
    finalize_turn_trace,
    format_invariant_summary,
    is_trace_enabled,
    trace_stage,
)
from core.tracing.stage_checks import check_merge, check_planner


@pytest.fixture(autouse=True)
def _clear_trace_env(monkeypatch):
    monkeypatch.delenv(TRACE_ENV_VAR, raising=False)
    TurnInvariantTrace.current()  # noqa: B018 - ensure clean


def test_trace_disabled_by_default():
    assert is_trace_enabled() is False
    assert TurnInvariantTrace.begin("u1", "hello") is None


def test_trace_enabled_via_env(monkeypatch):
    monkeypatch.setenv(TRACE_ENV_VAR, "1")
    assert is_trace_enabled() is True
    trace = TurnInvariantTrace.begin("user-1", "book haircut")
    assert trace is not None
    trace.record_stage(
        "session_load",
        invariant_id="session.load_complete",
        invariant_ok=True,
        state_snapshot={"found": False},
    )
    summary = trace.finalize()
    assert summary["user_id"] == "user-1"
    assert summary["text"] == "book haircut"
    assert summary["first_failed_invariant"] is None
    assert any(s["stage"] == "session_load" for s in summary["stages"])


def test_first_failure_and_downstream_affected(monkeypatch):
    monkeypatch.setenv(TRACE_ENV_VAR, "1")
    TurnInvariantTrace.begin("u2", "turn")
    trace_stage(
        "merge",
        lambda: [
            __import__(
                "core.tracing.invariant_trace", fromlist=["InvariantResult"]
            ).InvariantResult(
                invariant_id="merge.missing_slots_is_list",
                invariant_ok=False,
                message="missing_slots is None",
            )
        ],
    )
    trace_stage(
        "planner",
        lambda: check_planner(plan={"status": "READY", "missing_slots": []}),
    )
    summary = finalize_turn_trace()
    assert summary is not None
    assert summary["first_failed_invariant"] == "merge.missing_slots_is_list"
    assert summary["first_failed_owner"] == "session"
    assert summary["first_failed_stage"] == "merge"
    assert "planner" in summary["downstream_stages_affected"]

    formatted = format_invariant_summary(summary)
    assert "FIRST FAILURE: merge.missing_slots_is_list" in formatted
    assert "downstream affected: planner" in formatted


def test_merge_check_detects_missing_slots_type():
    results = check_merge(
        effective_response={"missing_slots": None, "slots": {}},
        session_state=None,
    )
    assert results[0].invariant_id == "merge.missing_slots_is_list"
    assert results[0].invariant_ok is False


def test_unvisited_stages_marked_on_finalize(monkeypatch):
    monkeypatch.setenv(TRACE_ENV_VAR, "1")
    trace = TurnInvariantTrace.begin("u3", "x")
    trace.record_stage(
        "session_load",
        invariant_id="session.load_complete",
        invariant_ok=True,
    )
    summary = trace.finalize()
    stages = {s["stage"] for s in summary["stages"]}
    assert "reload_session" in stages
    not_reached = [
        s for s in summary["stages"] if s["invariant_id"].endswith(".not_reached")
    ]
    assert not_reached
