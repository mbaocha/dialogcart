"""Tests for server-side decision trace logging helpers."""

from __future__ import annotations

import logging

from core.tracing.server_log import compact_session_snapshot, log_decision_trace_text


def test_compact_session_snapshot_strips_org_catalog():
    session = {
        "intent_name": "CREATE_APPOINTMENT",
        "status": "NEEDS_CLARIFICATION",
        "missing_slots": ["date"],
        "slots": {"service_id": "premium haircut"},
        "facts": {
            "dates": ["2026-07-02"],
            "org": {"success": True, "data": {"catalog": {"services": [{"id": 1}]}}},
        },
    }
    compact = compact_session_snapshot(session)
    assert compact is not None
    assert compact["intent_name"] == "CREATE_APPOINTMENT"
    assert compact["slots"] == {"service_id": "premium haircut"}
    assert compact["facts"] == {"dates": ["2026-07-02"]}
    assert "org" not in compact["facts"]


def test_log_decision_trace_text(caplog):
    caplog.set_level(logging.INFO)
    logger = logging.getLogger("test.decision_trace")
    log_decision_trace_text(
        logger,
        {
            "trace_view": "summary",
            "decision_trace_text": "User:\nbook haircut",
        },
    )
    assert "[decision_trace:summary]" in caplog.text
    assert "book haircut" in caplog.text
