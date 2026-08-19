"""Post-execution planning projections (outcome overlay / status helpers).

Decision construction and time-match finalization live in
``planning.pipeline`` (``decide`` / ``finalize_decision_after_time_resolution``).
These helpers only project already-finalized plan fields onto execution
outcomes for session persistence and coordinator handoff.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from core.planning.time_resolution import TIME_MATCH_EXACT, TIME_MATCH_MISMATCH


def _plan_time_match_outcome(plan: Dict[str, Any]) -> Optional[str]:
    time_match = plan.get("time_match_outcome")
    if time_match:
        return str(time_match)
    time_resolution = plan.get("time_resolution")
    if isinstance(time_resolution, dict) and time_resolution.get("outcome"):
        return str(time_resolution["outcome"])
    return None


def post_execution_planner_status(result: Dict[str, Any]) -> Optional[str]:
    """Return planner status when a post-execution transition was applied."""
    plan = result.get("plan")
    if not isinstance(plan, dict):
        return None
    time_match = _plan_time_match_outcome(plan)
    if time_match not in (TIME_MATCH_EXACT, TIME_MATCH_MISMATCH):
        return None
    status = plan.get("status")
    if status in ("AWAITING_CONFIRMATION", "NEEDS_CLARIFICATION"):
        return str(status)
    return None


def overlay_post_execution_planning_on_outcome(
    plan: Dict[str, Any], outcome: Dict[str, Any]
) -> None:
    """Overlay planner-owned conversation fields onto the execution outcome envelope.

    Projection only — does not select Decision fields.
    """
    time_match = _plan_time_match_outcome(plan)
    if time_match not in (TIME_MATCH_EXACT, TIME_MATCH_MISMATCH):
        return

    for key in (
        "awaiting",
        "ask_next",
        "stage",
        "time_match_outcome",
        "time_resolution",
    ):
        if plan.get(key) is not None:
            outcome[key] = plan.get(key)

    nested = outcome.get("plan")
    outcome_plan = dict(nested) if isinstance(nested, dict) else {}
    for key in ("status", "stage", "action", "awaiting", "ask_next"):
        if key in plan:
            outcome_plan[key] = plan.get(key)
    outcome["plan"] = outcome_plan

    missing = plan.get("missing_slots")
    if isinstance(missing, list):
        outcome["missing_slots"] = list(missing)
        facts = outcome.get("facts")
        if not isinstance(facts, dict):
            facts = {}
            outcome["facts"] = facts
        facts["missing_slots"] = list(missing)

    slots = plan.get("slots")
    if isinstance(slots, dict):
        facts = outcome.get("facts")
        if not isinstance(facts, dict):
            facts = {}
            outcome["facts"] = facts
        facts["slots"] = dict(slots)
