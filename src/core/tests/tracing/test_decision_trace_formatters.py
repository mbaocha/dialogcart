"""Unit tests for decision trace formatters and graph export."""

from __future__ import annotations

import json

import pytest

from core.tracing.decision_trace import (
    TRACE_ENV_VAR,
    Candidate,
    TurnTrace,
    decide,
    emit_evidence,
    emit_mutation,
    finalize_turn_trace,
    reset_decision_trace_state,
    trace_to_dict,
)
from core.tracing.formatters import (
    decision_trace_to_mermaid,
    format_decision_failure_context,
    format_decision_summary,
)
from core.tracing.reason_codes import BROWSE_PREVIOUS, PAGINATION_HANDLED


@pytest.fixture(autouse=True)
def _enable_trace(monkeypatch):
    monkeypatch.setenv(TRACE_ENV_VAR, "1")
    reset_decision_trace_state()
    yield
    reset_decision_trace_state()


def _sample_trace() -> dict:
    TurnTrace.begin(user_id="u1", text="show more")
    evidence_id = emit_evidence(
        "SESSION_SNAPSHOT",
        subsystem="session",
        facts={"page_index": 0},
        node_id="evidence.session.presentation",
    )
    decide(
        "PAGINATION_HANDLE",
        subsystem="orchestration",
        winner="handled",
        reason_code=PAGINATION_HANDLED,
        reason_text="Paginated without re-searching availability",
        node_id="decision.pagination.handle_turn",
        depends_on=[evidence_id],
        category="presentation",
        candidates=[
            Candidate(
                id="SEARCH_AVAILABILITY",
                matched=False,
                reason_code="PAGINATION_ACTIVE",
                reason_text="Browse pagination short-circuits execution",
            )
        ],
    )
    decide(
        "EXECUTION_ELIGIBILITY",
        subsystem="orchestration",
        winner="skip",
        reason_code="PAGINATION_SHORT_CIRCUIT",
        reason_text="Browse pagination handled the turn",
        node_id="decision.execution.eligibility",
        category="routing",
    )
    mutation_id = emit_mutation(
        "decision.pagination.handle_turn",
        subsystem="orchestration",
        field="availability_presentation.page_index",
        previous=0,
        new=1,
        reason_code=BROWSE_PREVIOUS,
        reason_text="Advanced to next page",
        presentation_only=True,
    )
    decide(
        "TURN_OUTCOME",
        subsystem="api",
        winner={"status": "success"},
        reason_code="TURN_OUTCOME_HANDLER_DELEGATED",
        reason_text="Pagination presentation updated",
        node_id="decision.turn.outcome",
        category="routing",
    )
    payload = trace_to_dict(finalize_turn_trace())
    payload["first_failed_invariant"] = "availability.page_index_monotonic"
    payload["summary"]["outcome"] = {"status": "success", "action": None}
    payload["summary"]["why_text"] = [
        "Paginated without re-searching availability.",
        "No SEARCH_AVAILABILITY executed.",
    ]
    payload["summary"]["why_chain"] = [
        {
            "step": "decision",
            "id": "decision.pagination.handle_turn",
            "reason_code": PAGINATION_HANDLED,
        },
        {
            "step": "decision",
            "id": "decision.execution.eligibility",
            "reason_code": "PAGINATION_SHORT_CIRCUIT",
        },
    ]
    return payload


def test_format_decision_summary_sections():
    text = format_decision_summary(_sample_trace())
    assert "=== Decision Trace Summary ===" in text
    assert "Outcome" in text
    assert "Why" in text
    assert "Paginated without re-searching availability." in text
    assert "Why chain (reason_code)" in text
    assert PAGINATION_HANDLED in text
    assert "Winning decisions" in text
    assert "decision.pagination.handle_turn" in text
    assert "Rejected candidates" in text
    assert "SEARCH_AVAILABILITY" in text
    assert "Mutations" in text
    assert "availability_presentation.page_index" in text
    assert "Failed invariants" in text
    assert "availability.page_index_monotonic" in text


def test_format_decision_summary_category_filter():
    text = format_decision_summary(_sample_trace(), categories=["presentation"])
    assert "decision.pagination.handle_turn" in text
    assert "decision.execution.eligibility" not in text


def test_format_decision_failure_context_from_body():
    body = {"decision_trace": _sample_trace()}
    text = format_decision_failure_context(body=body)
    assert "Key rejected candidates" in text
    assert "SEARCH_AVAILABILITY" in text


def test_decision_trace_to_mermaid():
    chart = decision_trace_to_mermaid(_sample_trace())
    assert chart.startswith("flowchart TD")
    assert "evidence.session.presentation" in chart
    assert "decision.pagination.handle_turn" in chart
    assert "-->" in chart


def test_decision_trace_to_mermaid_category_filter():
    chart = decision_trace_to_mermaid(_sample_trace(), categories=["presentation"])
    assert "decision.pagination.handle_turn" in chart
    assert "decision.execution.eligibility" not in chart


def test_format_decision_summary_empty():
    assert format_decision_summary(None) == ""
    assert format_decision_summary({}) == ""
