"""Build ``ExecutionCommand`` from a finalized Decision plan.

Resolves the selected policy step exactly once. Does not mutate the plan.
Fails closed when Decision selected an action with no matching policy step.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping, Optional

from core.execution.command import ExecutionCommand, ExecutionCommandError
from core.policy.intent_policy import get_execution_steps


def build_execution_command(
    *,
    plan: Mapping[str, Any],
    organization_id: int,
    policy_client: Optional[str] = None,
) -> Optional[ExecutionCommand]:
    """Return an authorized execution command, or ``None`` when no action.

    Precedence for client/mode:
      1. Explicit ``policy_client`` when provided (Decision typed field)
      2. Matching policy step for ``plan.action`` (single lookup)
    """
    if not isinstance(plan, Mapping):
        return None

    # Execution authorization requires semantic evidence from the current turn.
    # This is deliberately independent of planner fallback construction so even
    # a malformed or synthetic plan cannot replay a durable executable action.
    if (
        plan.get("message_applied") is False
        or plan.get("nlu_failure_recovery") is True
    ):
        return None

    action = plan.get("action")
    if not action:
        return None
    action_s = str(action)

    intent_name = plan.get("intent_name") or plan.get("intent") or ""
    intent_s = str(intent_name) if intent_name else ""

    steps = get_execution_steps(intent_s)
    step = next((s for s in steps if s.get("action") == action_s), None)
    if step is None:
        raise ExecutionCommandError(
            f"Decision selected action {action_s!r} with no matching policy step "
            f"for intent {intent_s!r}"
        )

    client_name = (policy_client or step.get("client") or "").strip()
    if not client_name:
        raise ExecutionCommandError(
            f"Decision selected action {action_s!r} without an execution client"
        )

    mode = str(step.get("mode") or "exploratory")
    slots_src = plan.get("slots")
    slots = deepcopy(dict(slots_src)) if isinstance(slots_src, Mapping) else {}

    proposal_ctx = plan.get("execution_proposal_context")
    entity_schema = plan.get("_entity_schema")
    turn_operation = plan.get("turn_operation")
    if isinstance(turn_operation, str) and not turn_operation.strip():
        turn_operation = None
    stage = plan.get("stage")
    if stage is not None:
        stage = str(stage)

    return ExecutionCommand(
        action=action_s,
        client_name=str(client_name),
        intent_name=intent_s,
        mode=mode,
        slots=slots,
        organization_id=int(organization_id),
        execution_proposal_context=(
            dict(proposal_ctx) if isinstance(proposal_ctx, Mapping) else None
        ),
        entity_schema=(
            dict(entity_schema) if isinstance(entity_schema, Mapping) else None
        ),
        turn_operation=str(turn_operation) if turn_operation else None,
        stage=stage,
    )
