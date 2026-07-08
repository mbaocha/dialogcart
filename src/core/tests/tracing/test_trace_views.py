"""Tests for layered decision trace diagnostic views."""

from __future__ import annotations

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
from core.tracing.reason_codes import (
    MERGE_SKIPPED,
    NEEDS_CLARIFICATION,
    PAGINATION_HANDLED,
    SLOTS_INCOMPLETE,
    STEP_SELECTED,
)
from core.tracing.schema_validation import validate_decision_trace
from core.tracing.reasoning import (
    build_provenance,
    filter_reasoning_records,
    reasoning_line_count,
)
from core.tracing.views import (
    DEFAULT_TRACE_VIEW,
    build_trace_response_fields,
    enrich_forensic_trace,
    extract_session_changes,
    format_reasoning_text,
    format_summary_text,
    format_trace_view_text,
    parse_trace_view,
    project_trace,
    resolve_trace_view,
)


@pytest.fixture(autouse=True)
def _enable_trace(monkeypatch):
    monkeypatch.setenv(TRACE_ENV_VAR, "1")
    reset_decision_trace_state()
    yield
    reset_decision_trace_state()


def _booking_trace() -> dict:
    TurnTrace.begin(user_id="u1", text="book haircut")
    emit_evidence(
        "DURABLE_FLOW",
        subsystem="session",
        facts={"intent_name": "", "session_reset_occurred": False, "has_session_slots": False},
        node_id="evidence.session.durable_flow",
    )
    decide(
        "MERGE_ELIGIBILITY",
        subsystem="session",
        winner="skip",
        reason_code=MERGE_SKIPPED,
        reason_text="Session merge skipped (no durable flow or reset occurred)",
        node_id="decision.merge.eligibility",
        category="routing",
    )
    emit_evidence(
        "MISSING_SLOTS",
        subsystem="planning",
        facts={"missing_slots": ["service_id", "date", "time"]},
        node_id="evidence.planning.missing_slots",
    )
    decide(
        "PLAN_STATUS",
        subsystem="planning",
        winner="NEEDS_CLARIFICATION",
        reason_code=NEEDS_CLARIFICATION,
        reason_text="Required slots missing.",
        node_id="decision.planner.status",
        category="routing",
        inputs_evaluated={"missing_slots": ["service_id", "date", "time"]},
    )
    decide(
        "PLAN_ACTION",
        subsystem="planning",
        winner=None,
        reason_code=SLOTS_INCOMPLETE,
        reason_text="No action selected while slots are incomplete",
        node_id="decision.planner.select_action",
        category="routing",
        candidates=[
            Candidate(
                id="SEARCH_AVAILABILITY",
                matched=False,
                reason_code=SLOTS_INCOMPLETE,
                reason_text="Missing required slots",
                missing_slots=("service_id", "date", "time"),
            )
        ],
    )
    decide(
        "EXECUTE_PLAN_ACTION",
        subsystem="execution",
        winner="skip",
        reason_code=NEEDS_CLARIFICATION,
        reason_text="Turn needs clarification; execution not run",
        node_id="decision.execution.eligibility",
        category="routing",
    )
    decide(
        "TURN_OUTCOME",
        subsystem="api",
        winner={
            "status": "NEEDS_CLARIFICATION",
            "intent": "CREATE_APPOINTMENT",
            "action": None,
            "stage": None,
        },
        reason_code=NEEDS_CLARIFICATION,
        reason_text="Ask user to select service.",
        node_id="decision.turn.outcome",
        category="routing",
        is_root=True,
    )
    trace = TurnTrace.current()
    assert trace is not None
    trace.record_stage_timing("luma", 3810)
    trace.record_stage_timing("planner", 5)
    return trace_to_dict(finalize_turn_trace())


def test_parse_trace_view_values():
    assert parse_trace_view("summary") == "summary"
    assert parse_trace_view("reasoning") == "reasoning"
    assert parse_trace_view("forensic") == "forensic"
    assert parse_trace_view("decision") == "forensic"
    assert parse_trace_view(None) is None
    assert parse_trace_view("invalid") is None


def test_resolve_trace_view_defaults_to_summary():
    enabled, view = resolve_trace_view(header_value="true")
    assert enabled is True
    assert view == DEFAULT_TRACE_VIEW


def test_resolve_trace_view_query_overrides_default():
    enabled, view = resolve_trace_view(query_trace="reasoning")
    assert enabled is True
    assert view == "reasoning"


def test_extract_session_changes_from_slot_mutations():
    TurnTrace.begin(user_id="u1", text="follow up")
    decision_id = decide(
        "SLOT_ADDITIVE_MERGE",
        subsystem="session",
        winner={"added": ["service_id"]},
        reason_code="SLOT_ADDITIVE_MERGE",
        reason_text="merged",
        node_id="decision.merge.slot_additive",
    )
    assert decision_id
    emit_mutation(
        decision_id,
        subsystem="session",
        field="slots.service_id",
        previous=None,
        new="Premium Haircut",
        reason_code="SLOT_ADDITIVE_MERGE",
        reason_text="merged service",
    )
    trace = trace_to_dict(finalize_turn_trace())
    changes = extract_session_changes(trace)
    assert len(changes) == 1
    assert changes[0]["field"] == "service_id"
    assert changes[0]["previous"] is None
    assert changes[0]["new"] == "Premium Haircut"


def test_project_summary_fields():
    trace = _booking_trace()
    summary = project_trace(trace, "summary")
    assert summary["view"] == "summary"
    assert summary["user"] == "book haircut"
    assert summary["intent"] == "CREATE_APPOINTMENT"
    assert summary["planner_status"] == "NEEDS_CLARIFICATION"
    assert "service_id" in summary["missing"]
    assert summary["action"] is None


def test_project_reasoning_hides_persistence_and_noise():
    TurnTrace.begin(user_id="u1", text="book haircut")
    decide(
        "PLAN_STATUS",
        subsystem="planning",
        winner="NEEDS_CLARIFICATION",
        reason_code=NEEDS_CLARIFICATION,
        reason_text="Required slots missing.",
        node_id="decision.planner.status",
        category="routing",
    )
    decide(
        "PERSIST_SAVE",
        subsystem="session",
        winner="skip",
        reason_code="PERSIST_SAVE_SKIPPED_STATUS",
        reason_text="skipped",
        node_id="decision.persist.save",
        category="persistence",
    )
    decide(
        "AVAILABILITY_CHECK_REQUIRED",
        subsystem="planning",
        winner=False,
        reason_code="INPUT_IGNORED_NOT_APPLICABLE",
        reason_text="Availability check not required for current state",
        node_id="decision.facts.availability_check_required",
        category="inference",
    )
    trace = trace_to_dict(finalize_turn_trace())
    reasoning = project_trace(trace, "reasoning")
    record_ids = {record["id"] for record in reasoning["records"]}
    assert "decision.persist.save" not in record_ids
    assert "decision.facts.availability_check_required" not in record_ids
    assert "decision.planner.status" in record_ids
    assert "edges" not in reasoning
    assert "provenance" in reasoning
    assert "turn_context" in reasoning


def test_forensic_projection_validates_and_includes_changes():
    trace = enrich_forensic_trace(_booking_trace())
    validate_decision_trace(trace)
    forensic = project_trace(trace, "forensic")
    assert forensic["view"] == "forensic"
    assert "records" in forensic
    assert "session_changes" in forensic
    assert forensic["stage_timings"]["luma"] == 3810


def test_format_summary_text_is_compact():
    text = format_summary_text(_booking_trace())
    assert "User:\nbook haircut" in text
    assert "Intent:\nCREATE_APPOINTMENT" in text
    assert "Planner:\nNEEDS_CLARIFICATION" in text
    assert "Missing:" in text
    assert "service_id" in text
    assert "Action:\nNone" in text
    assert "Timing" in text
    line_count = len([line for line in text.splitlines() if line.strip()])
    assert 10 <= line_count <= 30


def test_format_reasoning_text_includes_rejected_candidates_and_provenance():
    trace = _booking_trace()
    text = format_reasoning_text(trace)
    assert "=== Reasoning Trace ===" in text
    assert "planner.select_action" in text
    assert "SEARCH_AVAILABILITY" in text
    assert "decision.persist.save" not in text
    assert "ignored inputs" not in text
    assert "INPUT_IGNORED_NOT_APPLICABLE" not in text
    assert "Key Values" in text
    assert "Causal Chain" in text
    assert "Timing:" in text


def test_reasoning_trace_is_shorter_than_legacy_style():
    trace = _booking_trace()
    line_count = reasoning_line_count(trace)
    assert line_count < 45


def test_filter_reasoning_records_excludes_derive_all():
    TurnTrace.begin(user_id="u2", text="x")
    decide(
        "DERIVE_BUSINESS_FACTS",
        subsystem="planning",
        winner={"availability_ready": False},
        reason_code="FACTS_DERIVED",
        reason_text="Derived planner business facts",
        node_id="decision.facts.derive_all",
        category="inference",
    )
    trace = trace_to_dict(finalize_turn_trace())
    ids = {r["id"] for r in filter_reasoning_records(trace)}
    assert "decision.facts.derive_all" not in ids


def test_build_trace_response_fields():
    fields = build_trace_response_fields(_booking_trace(), "summary")
    assert fields["trace_view"] == "summary"
    assert fields["decision_trace"]["view"] == "summary"
    assert fields["decision_trace_text"]
    assert "User:" in fields["decision_trace_text"]


def test_format_trace_view_text_from_projection():
    fields = build_trace_response_fields(_booking_trace(), "summary")
    text = format_trace_view_text(fields["decision_trace"], "summary")
    assert "book haircut" in text
