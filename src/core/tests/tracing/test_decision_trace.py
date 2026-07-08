"""Unit tests for the decision trace framework (Phase 0)."""

from __future__ import annotations

import json
import os
from types import MappingProxyType

import pytest

from core.tracing.decision_trace import (
    TRACE_ENV_VAR,
    TRACE_HEADER,
    Candidate,
    DecisionTraceError,
    FailedPredicate,
    IgnoredInput,
    TurnTrace,
    build_decision_trace_for_request,
    clear_request_decision_trace_enabled,
    decide,
    emit_evidence,
    emit_mutation,
    empty_decision_trace,
    finalize_turn_trace,
    is_decision_trace_enabled,
    reset_decision_trace_state,
    set_request_decision_trace_enabled,
    trace_to_dict,
)
from core.tracing.reason_codes import BROWSE_PREVIOUS, TRACE_EMPTY
from core.tracing.schema_validation import validate_decision_trace


@pytest.fixture(autouse=True)
def _reset_decision_trace_context(monkeypatch):
    monkeypatch.delenv(TRACE_ENV_VAR, raising=False)
    reset_decision_trace_state()


def test_disabled_by_default_no_ops():
    assert is_decision_trace_enabled() is False
    assert TurnTrace.begin(user_id="u1", text="hi") is None
    assert emit_evidence("X", subsystem="api", facts={}) is None
    assert decide(
        "Y",
        subsystem="api",
        winner=True,
        reason_code="TEST",
        reason_text="test",
    ) is None


def test_enabled_via_environment(monkeypatch):
    monkeypatch.setenv(TRACE_ENV_VAR, "1")
    assert is_decision_trace_enabled() is True
    trace = TurnTrace.begin(user_id="u1", text="book")
    assert trace is not None


def test_enabled_via_header():
    assert is_decision_trace_enabled(header_value="true") is True


def test_enabled_via_query_param():
    assert is_decision_trace_enabled(query_trace="decision") is True


def test_empty_trace_validates_against_schema():
    payload = empty_decision_trace(user_id="u1", text="hello")
    validate_decision_trace(payload)
    assert payload["version"] == "1.1"
    assert payload["records"] == []
    assert payload["root_id"] is None
    assert payload["summary"]["why_text"] == []


def test_build_decision_trace_for_request_when_enabled(monkeypatch):
    monkeypatch.setenv(TRACE_ENV_VAR, "1")
    raw = build_decision_trace_for_request(
        user_id="u1",
        text="hello",
        transaction_id="tx-1",
    )
    payload = trace_to_dict(raw)
    validate_decision_trace(payload)
    assert payload["turn"]["transaction_id"] == "tx-1"
    assert payload["records"] == []


def test_emit_evidence_decision_mutation_graph(monkeypatch):
    monkeypatch.setenv(TRACE_ENV_VAR, "1")
    TurnTrace.begin(user_id="u1", text="paginate")

    evidence_id = emit_evidence(
        "SESSION_SNAPSHOT",
        subsystem="session",
        facts={"page_index": 1},
        node_id="evidence.session.presentation",
    )
    decision_id = decide(
        "PAGINATE",
        subsystem="orchestration",
        winner="BROWSE_PREVIOUS",
        reason_code=BROWSE_PREVIOUS,
        reason_text="User browsed to previous page",
        node_id="decision.pagination.handle_turn",
        depends_on=[evidence_id],
    )
    mutation_id = emit_mutation(
        decision_id,
        subsystem="orchestration",
        field="availability_presentation.page_index",
        previous=1,
        new=0,
        reason_code=BROWSE_PREVIOUS,
        reason_text="User browsed to previous page",
        presentation_only=True,
    )

    payload = trace_to_dict(finalize_turn_trace())
    validate_decision_trace(payload)

    assert len(payload["records"]) == 3
    assert {r["id"] for r in payload["records"]} == {
        evidence_id,
        decision_id,
        mutation_id,
    }

    depends = [e for e in payload["edges"] if e["kind"] == "depends_on"]
    causes = [e for e in payload["edges"] if e["kind"] == "causes"]
    assert depends == [
        {"from": evidence_id, "to": decision_id, "kind": "depends_on"}
    ]
    assert causes == [
        {"from": decision_id, "to": mutation_id, "kind": "causes"}
    ]

    decision_record = next(r for r in payload["records"] if r["id"] == decision_id)
    assert decision_record["depends_on"] == [evidence_id]
    mutation_record = next(r for r in payload["records"] if r["id"] == mutation_id)
    assert mutation_record["decision_id"] == decision_id
    assert mutation_record["sequence"] == 0


def test_shared_evidence_dag(monkeypatch):
    monkeypatch.setenv(TRACE_ENV_VAR, "1")
    TurnTrace.begin(user_id="u1", text="shared")

    shared = emit_evidence(
        "FINGERPRINT_COMPARISON",
        subsystem="orchestration",
        facts={"match": True},
        node_id="evidence.fingerprint.trust",
    )
    d1 = decide(
        "TRUST",
        subsystem="orchestration",
        winner=True,
        reason_code="FINGERPRINT_MATCH",
        reason_text="match",
        node_id="decision.fingerprint.trust",
        depends_on=[shared],
    )
    d2 = decide(
        "PLAN_ACTION",
        subsystem="planning",
        winner=None,
        reason_code="NO_STEP_REQUIRES_SATISFIED",
        reason_text="none",
        node_id="decision.planner.select_action",
        depends_on=[shared, d1],
    )

    payload = trace_to_dict(finalize_turn_trace())
    validate_decision_trace(payload)

    shared_edges = [
        e
        for e in payload["edges"]
        if e["kind"] == "depends_on" and e["from"] == shared
    ]
    assert len(shared_edges) == 2
    assert {e["to"] for e in shared_edges} == {d1, d2}


def test_parent_child_scope_edges(monkeypatch):
    monkeypatch.setenv(TRACE_ENV_VAR, "1")
    trace = TurnTrace.begin(user_id="u1", text="scope")
    assert trace is not None

    parent = decide(
        "ROOT",
        subsystem="api",
        winner="ok",
        reason_code="TEST",
        reason_text="root",
        node_id="decision.parent",
    )
    with trace.scope(parent):
        child = decide(
            "CHILD",
            subsystem="api",
            winner="ok",
            reason_code="TEST",
            reason_text="child",
            node_id="decision.child",
        )

    payload = trace_to_dict(trace.finalize())
    child_edges = [e for e in payload["edges"] if e["kind"] == "child"]
    assert {"from": parent, "to": child, "kind": "child"} in child_edges

    parent_record = next(r for r in payload["records"] if r["id"] == parent)
    assert parent_record["child_ids"] == [child]


def test_mutation_requires_existing_decision(monkeypatch):
    monkeypatch.setenv(TRACE_ENV_VAR, "1")
    TurnTrace.begin(user_id="u1", text="x")
    with pytest.raises(DecisionTraceError, match="existing decision"):
        emit_mutation(
            "missing.decision",
            subsystem="session",
            field="slots.time",
            previous=None,
            new="17:00",
            reason_code="TEST",
            reason_text="test",
        )


def test_duplicate_record_id_rejected(monkeypatch):
    monkeypatch.setenv(TRACE_ENV_VAR, "1")
    TurnTrace.begin(user_id="u1", text="x")
    emit_evidence(
        "A",
        subsystem="api",
        facts={},
        node_id="evidence.duplicate",
    )
    with pytest.raises(DecisionTraceError, match="Duplicate"):
        emit_evidence(
            "B",
            subsystem="api",
            facts={},
            node_id="evidence.duplicate",
        )


def test_cycle_detection_on_depends_on(monkeypatch):
    monkeypatch.setenv(TRACE_ENV_VAR, "1")
    trace = TurnTrace.begin(user_id="u1", text="cycle")
    assert trace is not None

    a = trace.decide(
        "A",
        subsystem="api",
        winner="a",
        reason_code="TEST",
        reason_text="a",
        node_id="decision.a",
    )
    b = trace.decide(
        "B",
        subsystem="api",
        winner="b",
        reason_code="TEST",
        reason_text="b",
        node_id="decision.b",
        depends_on=[a],
    )
    assert b == "decision.b"
    with pytest.raises(DecisionTraceError, match="cycle"):
        trace._add_edge(b, a, "depends_on")


def test_finalize_is_immutable_and_idempotent(monkeypatch):
    monkeypatch.setenv(TRACE_ENV_VAR, "1")
    trace = TurnTrace.begin(user_id="u1", text="freeze")
    assert trace is not None
    decide(
        "X",
        subsystem="api",
        winner=True,
        reason_code="TEST",
        reason_text="done",
        node_id="decision.x",
    )

    frozen = trace.finalize()
    assert isinstance(frozen, MappingProxyType)
    assert trace.finalized is True

  # Second finalize returns same frozen object
    again = trace.finalize()
    assert again is frozen

    with pytest.raises(DecisionTraceError, match="finalized"):
        trace.emit_evidence("Y", subsystem="api", facts={})


def test_records_not_mutated_after_emit(monkeypatch):
    monkeypatch.setenv(TRACE_ENV_VAR, "1")
    trace = TurnTrace.begin(user_id="u1", text="immutable")
    assert trace is not None

    facts = {"page_index": 1}
    emit_evidence(
        "SESSION_SNAPSHOT",
        subsystem="session",
        facts=facts,
        node_id="evidence.session",
    )
    facts["page_index"] = 999

    payload = trace_to_dict(trace.finalize())
    record = payload["records"][0]
    assert record["facts"]["page_index"] == 1


def test_candidate_and_ignored_inputs_serialization(monkeypatch):
    monkeypatch.setenv(TRACE_ENV_VAR, "1")
    TurnTrace.begin(user_id="u1", text="inputs")

    decide(
        "PLAN_ACTION",
        subsystem="planning",
        winner=None,
        reason_code="NO_STEP_REQUIRES_SATISFIED",
        reason_text="blocked",
        candidates=[
            Candidate(
                id="SEARCH_AVAILABILITY",
                matched=False,
                reason_code="REQUIREMENT_UNSATISFIED",
                reason_text="availability_check_required is false",
                blocking_requirements=("availability_check_required",),
                failed_predicates=(
                    FailedPredicate(
                        predicate="flags.availability_check_required == true",
                        actual=False,
                        reason_code="AVAILABILITY_ALREADY_READY",
                    ),
                ),
            )
        ],
        inputs_evaluated={"availability_ready": True},
        inputs_ignored={
            "availability_presentation.page_index": IgnoredInput(
                reason_code="INPUT_IGNORED_NOT_APPLICABLE",
                reason_text="not used for plan action selection",
            )
        },
    )

    payload = trace_to_dict(finalize_turn_trace())
    validate_decision_trace(payload)
    decision = payload["records"][0]
    assert decision["candidates"][0]["blocking_requirements"] == [
        "availability_check_required"
    ]
    assert decision["inputs_ignored"]["availability_presentation.page_index"][
        "reason_code"
    ] == "INPUT_IGNORED_NOT_APPLICABLE"


def test_summary_for_empty_trace(monkeypatch):
    monkeypatch.setenv(TRACE_ENV_VAR, "1")
    payload = trace_to_dict(
        build_decision_trace_for_request(user_id="u1", text="empty")
    )
    assert payload["summary"]["why_text"] == [TRACE_EMPTY]


def test_request_scoped_enable_flag(monkeypatch):
    set_request_decision_trace_enabled(True)
    assert is_decision_trace_enabled() is True
    clear_request_decision_trace_enabled()
    assert is_decision_trace_enabled() is False


def test_json_round_trip_serialization(monkeypatch):
    monkeypatch.setenv(TRACE_ENV_VAR, "1")
    TurnTrace.begin(user_id="u1", text="json")
    emit_evidence("E", subsystem="api", facts={"n": 1}, node_id="evidence.e")
    payload = trace_to_dict(finalize_turn_trace())
    encoded = json.dumps(payload, default=str)
    decoded = json.loads(encoded)
    validate_decision_trace(decoded)
