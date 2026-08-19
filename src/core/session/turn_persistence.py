"""Canonical turn-result projection and persistence."""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from core.session.session_projector import SessionProjectorV2


_PERSISTABLE_STATUSES = {
    "NEEDS_CLARIFICATION",
    "AWAITING_CONFIRMATION",
    "AWAITING_CAPABILITY",
    "READY",
    "EXECUTED",
    "success",
    "succeeded",
    "HANDLER_DELEGATED",
    "OFF_TOPIC",
}


def _canonical_missing_slots(
    outcome: Dict[str, Any], plan: Dict[str, Any]
) -> Optional[List[str]]:
    """Read canonical missing slots from planner-owned result locations."""
    missing = outcome.get("missing_slots")
    if isinstance(missing, list):
        return missing

    facts = outcome.get("facts")
    if isinstance(facts, dict):
        missing = facts.get("missing_slots")
        if isinstance(missing, list):
            return missing

    missing = plan.get("missing_slots")
    if isinstance(missing, list):
        return missing

    plan_facts = plan.get("facts")
    if isinstance(plan_facts, dict):
        missing = plan_facts.get("missing_slots")
        if isinstance(missing, list):
            return missing

    return None


def build_projection_outcome(
    result: Dict[str, Any],
    *,
    outcome: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build the planner/execution projection envelope for one engine result."""
    source_outcome = outcome
    if not isinstance(source_outcome, dict):
        candidate = result.get("outcome") or result.get("result")
        source_outcome = candidate if isinstance(candidate, dict) else {}

    projection_outcome = dict(source_outcome)
    plan = result.get("plan")
    if not isinstance(plan, dict):
        nested_plan = projection_outcome.get("plan")
        plan = nested_plan if isinstance(nested_plan, dict) else {}
    projection_outcome["plan"] = plan

    missing_slots = _canonical_missing_slots(projection_outcome, plan)
    if missing_slots is not None:
        projection_outcome["missing_slots"] = missing_slots
        facts = projection_outcome.get("facts")
        projection_facts = dict(facts) if isinstance(facts, dict) else {}
        projection_facts["missing_slots"] = missing_slots
        projection_outcome["facts"] = projection_facts

    if not projection_outcome.get("intent_name") and not projection_outcome.get(
        "intent"
    ):
        intent_name = plan.get("intent_name") or plan.get("intent")
        if intent_name:
            projection_outcome["intent_name"] = intent_name

    pagination = result.get("availability_pagination")
    if isinstance(pagination, dict):
        projection_outcome.setdefault("availability_pagination", pagination)

    workflow_result = result.get("_workflow_result")
    if isinstance(workflow_result, dict):
        projection_outcome["_workflow_result"] = workflow_result

    return projection_outcome


def resolve_projection_status(
    outcome: Dict[str, Any],
    *,
    result: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Resolve durable session projection status for a turn outcome."""
    if isinstance(result, dict):
        explicit = result.get("projection_status")
        if isinstance(explicit, str) and explicit:
            return explicit

        from core.planning.planner.plan_builder import post_execution_planner_status

        planner_status = post_execution_planner_status(result)
        if planner_status:
            return planner_status

    status = outcome.get("status")
    if outcome.get("schema_version") == 1 and status == "succeeded":
        subject = outcome.get("subject")
        subject_kind = subject.get("kind") if isinstance(subject, dict) else None
        return "success" if subject_kind == "availability" else "EXECUTED"
    return status if isinstance(status, str) else None


def _projection_status(
    outcome: Dict[str, Any],
    *,
    result: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    return resolve_projection_status(outcome, result=result)


def project_and_persist_turn_result(
    *,
    result: Dict[str, Any],
    organization_id: int,
    user_id: str,
    previous_session_state: Optional[Dict[str, Any]] = None,
    working_session_state: Optional[Dict[str, Any]] = None,
    outcome: Optional[Dict[str, Any]] = None,
    outcome_status: Optional[str] = None,
    session_store: Optional[Any] = None,
    capability_result: Optional[Dict[str, Any]] = None,
    handler_conversation_update: Optional[Dict[str, Any]] = None,
    conversation_messages: Optional[List[Dict[str, Any]]] = None,
    assistant_proposals: Optional[List[Dict[str, Any]]] = None,
    assistant_proposal_updates: Optional[List[Dict[str, Any]]] = None,
    fallback_session_state: Optional[Dict[str, Any]] = None,
    save: bool = True,
    save_callback: Optional[Callable[[int, str, Dict[str, Any]], None]] = None,
) -> Optional[Dict[str, Any]]:
    """Project a complete engine result through SessionProjectorV2 and save it."""
    projection_outcome = build_projection_outcome(result, outcome=outcome)
    status = outcome_status or _projection_status(projection_outcome, result=result)
    if status not in _PERSISTABLE_STATUSES:
        return None

    if capability_result is None:
        facts = projection_outcome.get("facts")
        payment_satisfied = (
            facts.get("payment_satisfied") if isinstance(facts, dict) else None
        )
        active_capability = projection_outcome.get("active_capability")
        if payment_satisfied is not None or active_capability:
            capability_result = {
                "payment_satisfied": payment_satisfied,
                "active": active_capability,
            }

    workflow_result = result.get("_workflow_result")
    post_commit_transition = result.get("_post_commit_transition")
    projected = SessionProjectorV2().project(
        outcome=projection_outcome,
        outcome_status=status,
        organization_id=organization_id,
        merged_luma_response=result.get("_merged_luma_response"),
        previous_session_state=previous_session_state,
        user_id=user_id,
        working_session_state=working_session_state,
        workflow_result=(
            workflow_result if isinstance(workflow_result, dict) else None
        ),
        post_commit_transition=(
            post_commit_transition
            if isinstance(post_commit_transition, dict)
            else None
        ),
        capability_result=capability_result,
        handler_conversation_update=handler_conversation_update,
        conversation_messages=conversation_messages,
        assistant_proposals=assistant_proposals,
        assistant_proposal_updates=assistant_proposal_updates,
    )

    if projected is None and status in ("READY", "success"):
        projected = fallback_session_state or working_session_state

    if projected is not None and save:
        if save_callback is not None:
            save_callback(organization_id, user_id, projected)
        elif session_store is not None and hasattr(session_store, "save_session"):
            session_store.save_session(organization_id, user_id, projected)
        else:
            from core.session.session_manager import save_session

            save_session(organization_id, user_id, projected)

    return projected
