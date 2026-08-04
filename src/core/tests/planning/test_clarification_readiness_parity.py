"""Parity: clarification-readiness evidence matches Stage 08 construction."""

from __future__ import annotations

from core.planning.pipeline.clarification_readiness import (
    awaiting_from_ask_next,
    build_clarification_readiness_evidence,
)
from core.planning.pipeline.types import SlotTurnState
from core.planning.planning_evidence import planning_evidence_payload_key


def _slot_state(**overrides):
    base = dict(
        intent_name="CREATE_APPOINTMENT",
        missing_slots=[],
        effective_collected_slots={},
        base_status="READY",
        ask_next=None,
        promptable_slots=[],
        declined_slots=[],
        needs_clarification=False,
    )
    base.update(overrides)
    return SlotTurnState(**base)


def test_clarification_projects_stage04_fields():
    evidence = build_clarification_readiness_evidence(
        slot_state=_slot_state(
            missing_slots=["date", "time"],
            promptable_slots=["notes"],
            declined_slots=["engine_type"],
            needs_clarification=True,
            ask_next="date",
        ),
        payload={},
    )
    assert evidence.missing_slots == ("date", "time")
    assert evidence.promptable_slots == ("notes",)
    assert evidence.declined_slots == ("engine_type",)
    assert evidence.needs_clarification is True
    assert evidence.default_ask_next == "date"
    assert evidence.has_planning_evidence is False
    assert evidence.turn_understanding is None
    assert evidence.block_auto_reshow is True


def test_clarification_derives_ask_next_when_stage04_ask_is_none():
    evidence = build_clarification_readiness_evidence(
        slot_state=_slot_state(
            missing_slots=["service_id", "date"],
            ask_next=None,
        ),
        payload={},
    )
    assert evidence.default_ask_next == "service_id"


def test_clarification_derives_ask_from_promptable_when_no_missing():
    evidence = build_clarification_readiness_evidence(
        slot_state=_slot_state(
            missing_slots=[],
            promptable_slots=["notes", "phone"],
            ask_next=None,
        ),
        payload={},
    )
    assert evidence.default_ask_next == "notes"
    assert evidence.block_auto_reshow is False


def test_clarification_reads_stamped_planning_evidence_only():
    key = planning_evidence_payload_key()
    evidence = build_clarification_readiness_evidence(
        slot_state=_slot_state(missing_slots=["time"]),
        payload={key: True},
    )
    assert evidence.has_planning_evidence is True
    assert evidence.block_auto_reshow is False


def test_clarification_turn_understanding_from_nested_turn():
    evidence = build_clarification_readiness_evidence(
        slot_state=_slot_state(missing_slots=["date"]),
        payload={"turn": {"understanding": "UNRECOGNIZED_INPUT"}},
    )
    assert evidence.turn_understanding == "UNRECOGNIZED_INPUT"
    assert evidence.block_auto_reshow is True


def test_clarification_turn_understanding_from_top_level_fallback():
    evidence = build_clarification_readiness_evidence(
        slot_state=_slot_state(),
        payload={"understanding": "UNDERSTOOD"},
    )
    assert evidence.turn_understanding == "UNDERSTOOD"


def test_awaiting_from_ask_next_maps_slot_name():
    assert awaiting_from_ask_next("time") == "time"
    assert awaiting_from_ask_next(None) is None
    assert awaiting_from_ask_next("") is None
