"""Parity: presentation-readiness evidence matches Stage 08 construction."""

from __future__ import annotations

from core.planning.pipeline.presentation_readiness import (
    PRESENTATION_ACTION_BRANCHES,
    build_presentation_readiness_evidence,
    has_planner_presentation,
)


def test_auto_reshow_allowed_when_requested_and_not_blocked():
    evidence = build_presentation_readiness_evidence(
        payload={},
        requested_availability_reshow=True,
        block_auto_reshow=False,
    )
    assert evidence.availability_reshow_allowed is True


def test_auto_reshow_blocked_by_clarification_gate():
    evidence = build_presentation_readiness_evidence(
        payload={},
        requested_availability_reshow=True,
        block_auto_reshow=True,
    )
    assert evidence.availability_reshow_allowed is False


def test_recovery_eligible_only_for_unrecognized_without_evidence():
    evidence = build_presentation_readiness_evidence(
        payload={},
        status="READY",
        action=None,
        action_branch="no_execution_step",
        missing_slots=["time"],
        has_planning_evidence=False,
        turn_understanding="UNRECOGNIZED_INPUT",
    )
    assert evidence.recovery_presentation_eligible is True
    assert evidence.unanswered_required_slots_without_presentation is True


def test_understood_without_evidence_is_not_recovery():
    evidence = build_presentation_readiness_evidence(
        payload={},
        status="READY",
        action=None,
        action_branch="no_execution_step",
        missing_slots=["time"],
        has_planning_evidence=False,
        turn_understanding="UNDERSTOOD",
    )
    assert evidence.recovery_presentation_eligible is False
    assert evidence.unanswered_required_slots_without_presentation is True


def test_existing_presentation_branch_blocks_terminal_demotion_flags():
    evidence = build_presentation_readiness_evidence(
        payload={},
        requested_availability_reshow=True,
        status="READY",
        action=None,
        action_branch="availability_reshow",
        missing_slots=["time"],
        has_planning_evidence=False,
        turn_understanding="UNRECOGNIZED_INPUT",
    )
    assert evidence.has_presentation is True
    assert evidence.recovery_presentation_eligible is False
    assert evidence.unanswered_required_slots_without_presentation is False


def test_promptable_optional_eligible_when_required_complete():
    evidence = build_presentation_readiness_evidence(
        payload={},
        status="READY",
        action=None,
        action_branch="no_execution_step",
        missing_slots=[],
        promptable_slots=["notes"],
        ask_next="notes",
    )
    assert evidence.promptable_optional_eligible is True


def test_has_planner_presentation_recognizes_browse_direction():
    assert "cache_satisfiable_browse" in PRESENTATION_ACTION_BRANCHES
    assert has_planner_presentation(
        action_branch=None,
        availability_reshow=False,
        availability_browse={"direction": "next", "axis_hint": "any"},
    )
    assert not has_planner_presentation(
        action_branch="no_execution_step",
        availability_reshow=False,
        availability_browse=None,
    )


def test_cache_satisfiable_browse_absent_without_session_cache():
    evidence = build_presentation_readiness_evidence(
        payload={"operation": "browse_next"},
        session_state={},
    )
    assert evidence.cache_satisfiable_browse is None
