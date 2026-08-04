"""Parity: progress-clarification evidence matches Stage 08 construction."""

from __future__ import annotations

from core.planning.pipeline.progress_clarification_readiness import (
    build_progress_clarification_evidence,
    stage_for_execution_action,
)


def test_progress_step_clarification_projects_branch_and_meta():
    evidence = build_progress_clarification_evidence(
        selected_step=None,
        candidates=[
            {
                "id": "SEARCH_AVAILABILITY",
                "action": "SEARCH_AVAILABILITY",
                "missing_requirements": [],
                "missing_slots": ["service_id", "date"],
                "optional_slots": [],
                "resolves": ["availability"],
            }
        ],
        promptable_slots=[],
        entity_schema=None,
        default_ask_next="date",
    )
    assert evidence.has_progress_clarification is True
    assert evidence.progress_branch == "progress_step_clarification"
    assert evidence.ask_next == "service_id"
    assert evidence.execution_step_selected is False
    meta = evidence.progress_meta_dict()
    assert meta is not None
    assert meta["blocker"] == "slots"
    assert meta["missing_slots"] == ["service_id", "date"]


def test_promptable_before_step_defers_selected_execution():
    evidence = build_progress_clarification_evidence(
        selected_step={
            "action": "SEARCH_AVAILABILITY",
            "client": "availability_client",
            "mode": "exploratory",
            "optional_slots": ["engine_type"],
            "resolves": ["availability"],
        },
        candidates=[],
        promptable_slots=["engine_type"],
        entity_schema=None,
        default_ask_next=None,
    )
    assert evidence.promptable_before_step is True
    assert evidence.has_progress_clarification is True
    assert evidence.progress_branch == "promptable_before_step"
    assert evidence.ask_next == "engine_type"
    assert evidence.execution_step_selected is False
    assert evidence.selected_execution_action is None


def test_selected_step_without_blocker_projects_execution_metadata():
    selected = {
        "action": "SEARCH_AVAILABILITY",
        "client": "availability_client",
        "mode": "exploratory",
        "optional_slots": [],
        "resolves": ["availability"],
    }
    evidence = build_progress_clarification_evidence(
        selected_step=selected,
        candidates=[],
        promptable_slots=[],
        entity_schema=None,
        default_ask_next="time",
    )
    assert evidence.has_progress_clarification is False
    assert evidence.progress_branch is None
    assert evidence.ask_next == "time"
    assert evidence.execution_step_selected is True
    assert evidence.selected_execution_action == "SEARCH_AVAILABILITY"
    assert evidence.selected_policy_client == "availability_client"
    assert evidence.selected_stage == stage_for_execution_action(
        "SEARCH_AVAILABILITY", selected
    )


def test_no_selected_step_and_no_progress_candidate_keeps_default_ask():
    evidence = build_progress_clarification_evidence(
        selected_step=None,
        candidates=[
            {
                "id": "SEARCH_AVAILABILITY",
                "missing_requirements": ["availability_ready"],
                "missing_slots": ["date"],
            }
        ],
        promptable_slots=[],
        entity_schema=None,
        default_ask_next="service_id",
    )
    assert evidence.has_progress_clarification is False
    assert evidence.ask_next == "service_id"
    assert evidence.execution_step_selected is False
