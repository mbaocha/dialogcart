"""Parity: decision-finalization evidence matches Stage 08 terminal reconcile."""

from __future__ import annotations

from core.planning.pipeline.decision_finalization import (
    build_decision_finalization_evidence,
)
from core.planning.pipeline.presentation_readiness import (
    build_presentation_readiness_evidence,
)


def _presentation(**kwargs):
    return build_presentation_readiness_evidence(payload={}, **kwargs)


def test_finalization_applies_recovery_presentation():
    presentation = _presentation(
        status="READY",
        action=None,
        action_branch="no_execution_step",
        missing_slots=["time"],
        ask_next="time",
        has_planning_evidence=False,
        turn_understanding="UNRECOGNIZED_INPUT",
    )
    evidence = build_decision_finalization_evidence(
        status="READY",
        action=None,
        awaiting=None,
        stage="AVAILABILITY",
        action_branch="no_execution_step",
        missing_slots=["time"],
        ask_next="time",
        availability_reshow=False,
        availability_browse=None,
        presentation=presentation,
    )
    assert evidence.recovery_presentation_applied is True
    assert evidence.action_branch == "recovery_presentation"
    assert evidence.status == "READY"
    assert evidence.awaiting == "time"
    assert evidence.violates_dead_ready_invariant is False


def test_finalization_demotes_unanswered_ready_to_clarification():
    presentation = _presentation(
        status="READY",
        action=None,
        action_branch="no_execution_step",
        missing_slots=["date"],
        ask_next="date",
        has_planning_evidence=True,
        turn_understanding="UNDERSTOOD",
    )
    evidence = build_decision_finalization_evidence(
        status="READY",
        action=None,
        awaiting=None,
        stage=None,
        action_branch="no_execution_step",
        missing_slots=["date"],
        ask_next="date",
        availability_reshow=False,
        availability_browse=None,
        presentation=presentation,
    )
    assert evidence.clarification_demotion_applied is True
    assert evidence.status == "NEEDS_CLARIFICATION"
    assert evidence.action_branch == "reconcile_unanswered_ask_next"
    assert evidence.stage == "AVAILABILITY"
    assert evidence.awaiting == "date"


def test_finalization_fills_awaiting_for_existing_clarification():
    presentation = _presentation(
        status="NEEDS_CLARIFICATION",
        action=None,
        action_branch="progress_step_clarification",
        missing_slots=["time"],
        ask_next="time",
    )
    evidence = build_decision_finalization_evidence(
        status="NEEDS_CLARIFICATION",
        action=None,
        awaiting=None,
        stage="AVAILABILITY",
        action_branch="progress_step_clarification",
        missing_slots=["time"],
        ask_next="time",
        availability_reshow=False,
        availability_browse=None,
        presentation=presentation,
    )
    assert evidence.awaiting_filled_from_ask is True
    assert evidence.awaiting == "time"
    assert evidence.status == "NEEDS_CLARIFICATION"


def test_finalization_promptable_optional_demotion():
    presentation = _presentation(
        status="READY",
        action=None,
        action_branch="no_execution_step",
        missing_slots=[],
        promptable_slots=["notes"],
        ask_next="notes",
    )
    evidence = build_decision_finalization_evidence(
        status="READY",
        action=None,
        awaiting=None,
        stage=None,
        action_branch="no_execution_step",
        missing_slots=[],
        ask_next="notes",
        promptable_slots=["notes"],
        availability_reshow=False,
        availability_browse=None,
        presentation=presentation,
    )
    assert evidence.promptable_optional_demotion is True
    assert evidence.status == "NEEDS_CLARIFICATION"
    # Preserve an existing branch when one is already set (Stage 08 parity).
    assert evidence.action_branch == "no_execution_step"
    assert evidence.stage == "AVAILABILITY"
    assert evidence.awaiting == "notes"


def test_finalization_promptable_sets_branch_when_absent():
    presentation = _presentation(
        status="READY",
        action=None,
        action_branch=None,
        missing_slots=[],
        promptable_slots=["notes"],
        ask_next="notes",
    )
    evidence = build_decision_finalization_evidence(
        status="READY",
        action=None,
        awaiting=None,
        stage=None,
        action_branch=None,
        missing_slots=[],
        ask_next="notes",
        promptable_slots=["notes"],
        availability_reshow=False,
        availability_browse=None,
        presentation=presentation,
    )
    assert evidence.promptable_optional_demotion is True
    assert evidence.action_branch == "promptable_optional"


def test_finalization_marks_dead_ready_invariant_when_unrecoverable():
    # Force a presentation snapshot that does not claim eligibility, then pass
    # provisional state that remains dead READY without presentation.
    presentation = _presentation(
        status="READY",
        action="SEARCH_AVAILABILITY",
        action_branch="policy",
        missing_slots=[],
    )
    evidence = build_decision_finalization_evidence(
        status="READY",
        action=None,
        awaiting=None,
        stage="AVAILABILITY",
        action_branch="no_execution_step",
        missing_slots=["time"],
        ask_next="time",
        availability_reshow=False,
        availability_browse=None,
        presentation=presentation,
    )
    # unanswered_required is False because presentation snapshot had action set;
    # provisional Decision state is still dead READY.
    assert evidence.clarification_demotion_applied is False
    assert evidence.recovery_presentation_applied is False
    assert evidence.violates_dead_ready_invariant is True
    assert evidence.dead_ready_invariant_message is not None
