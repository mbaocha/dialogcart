"""Tests for compact reasoning trace projection."""

from __future__ import annotations

import pytest

from core.tracing.availability import AVAILABILITY_REQUEST_ID, AVAILABILITY_RESPONSE_ID
from core.tracing.decision_trace import (
    TRACE_ENV_VAR,
    TurnTrace,
    decide,
    emit_evidence,
    finalize_turn_trace,
    reset_decision_trace_state,
    trace_to_dict,
)
from core.tracing.reason_codes import FINGERPRINT_MISMATCH, INPUT_IGNORED_NOT_APPLICABLE
from core.tracing.reasoning import (
    build_provenance,
    format_reasoning_text,
    project_reasoning,
)


@pytest.fixture(autouse=True)
def _enable_trace(monkeypatch):
    monkeypatch.setenv(TRACE_ENV_VAR, "1")
    reset_decision_trace_state()
    yield
    reset_decision_trace_state()


def test_availability_request_provenance_in_reasoning_text():
    TurnTrace.begin(user_id="u1", text="premium friday")
    emit_evidence(
        "AVAILABILITY_REQUEST",
        subsystem="execution",
        facts={
            "service_id": 18,
            "date": "2026-01-14",
            "field_provenance": {
                "service_id": {
                    "value": 18,
                    "source": "session.slots.service_id",
                    "consumer": "AvailabilityClient",
                },
                "date": {
                    "value": "2026-01-14",
                    "source": "session.date_proposal",
                    "reason": "explicit user temporal constraint",
                },
                "time": {
                    "omitted": True,
                    "reason": "conversational preference applied after availability search",
                },
            },
        },
        node_id=AVAILABILITY_REQUEST_ID,
        observed_at_stage="execution",
    )
    emit_evidence(
        "AVAILABILITY_RESPONSE",
        subsystem="execution",
        facts={
            "http_status": 200,
            "available_slot_count": 12,
            "normalized_slot_count": 10,
            "field_provenance": {
                "slots": {
                    "value": "10 offers",
                    "source": "availability API",
                    "consumer": "last_execution_result",
                }
            },
        },
        node_id=AVAILABILITY_RESPONSE_ID,
        observed_at_stage="execution",
    )
    trace = trace_to_dict(finalize_turn_trace())
    text = format_reasoning_text(trace)
    assert "Availability Request" in text
    assert "service_id: 18" in text
    assert "session.slots.service_id" in text
    assert "session.date_proposal" in text
    assert "time: omitted" in text
    assert "Availability Response" in text
    assert "10 normalized" in text

    provenance = build_provenance(trace)
    fields = {entry["field"] for entry in provenance}
    assert "availability_request.service_id" in fields
    assert "availability_response.slots" in fields


def test_skipped_confirmation_hidden_from_reasoning():
    TurnTrace.begin(user_id="u1", text="yes")
    decide(
        "CONFIRMATION_GATE_OPEN",
        subsystem="session",
        winner="closed",
        reason_code="CONFIRMATION_GATE_CLOSED",
        reason_text="Confirmation gate is closed",
        node_id="decision.confirmation.gate_open",
        category="routing",
    )
    decide(
        "CONFIRMATION_CLASSIFY",
        subsystem="session",
        winner="NONE",
        reason_code=INPUT_IGNORED_NOT_APPLICABLE,
        reason_text="Confirmation gate not active for this turn",
        node_id="decision.confirmation.classify_turn",
        category="routing",
        skipped=True,
    )
    trace = trace_to_dict(finalize_turn_trace())
    projection = project_reasoning(trace)
    ids = {record["id"] for record in projection["causal_decisions"]}
    assert "decision.confirmation.classify_turn" not in ids
    assert "decision.confirmation.gate_open" not in ids


def test_fingerprint_provenance_surfaces_in_key_values():
    TurnTrace.begin(user_id="u1", text="change date")
    emit_evidence(
        "FINGERPRINT_SLOTS",
        subsystem="orchestration",
        facts={
            "criteria_slots": {"service_id": 18, "date": "2026-01-15"},
            "criteria_slot_keys": ["date", "service_id"],
        },
        node_id="evidence.fingerprint.slots",
        observed_at_stage="fingerprint",
    )
    emit_evidence(
        "FINGERPRINT_STORED",
        subsystem="session",
        facts={"fingerprint_hash": "abc123", "present": True},
        node_id="evidence.fingerprint.stored",
        observed_at_stage="fingerprint",
    )
    emit_evidence(
        "FINGERPRINT_COMPUTED",
        subsystem="orchestration",
        facts={"fingerprint_hash": "def456", "present": True},
        node_id="evidence.fingerprint.computed",
        observed_at_stage="fingerprint",
    )
    decide(
        "FINGERPRINT_TRUST",
        subsystem="orchestration",
        winner="stale",
        reason_code=FINGERPRINT_MISMATCH,
        reason_text="Stored fingerprint missing or does not match current criteria",
        node_id="decision.fingerprint.trust",
        category="inference",
    )
    trace = trace_to_dict(finalize_turn_trace())
    text = format_reasoning_text(trace)
    assert "availability_fingerprint" in text
    assert "abc123" in text
    assert "def456" in text
    assert "fingerprint.trust" in text
