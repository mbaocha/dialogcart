"""Availability-domain execution preparation."""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from core.execution.adapters.base import (
    ExecutionAdapter,
    PreparedExecution,
    apply_organization_id,
    inject_customer_id,
    load_catalog_mapping,
)
from core.execution.command import ExecutionCommand


class AvailabilityAdapter(ExecutionAdapter):
    """Prepare SEARCH_AVAILABILITY operational inputs."""

    def prepare(
        self,
        command: ExecutionCommand,
        session_state: Optional[Dict[str, Any]],
        organization_id: int,
        *,
        organization_client: Optional[Any] = None,
        kwargs: Optional[Dict[str, Any]] = None,
        plan_snapshot: Optional[Mapping[str, Any]] = None,
    ) -> PreparedExecution:
        slots = dict(command.slots)
        slots = apply_organization_id(slots, organization_id=organization_id)
        slots = inject_customer_id(
            slots, session_state=session_state, kwargs=kwargs
        )

        proposal_context = (
            dict(command.execution_proposal_context)
            if command.execution_proposal_context is not None
            else None
        )
        # Preserve Decision plan fields needed by proposal resolution
        # (facts, merged luma, revision flags) without mutating the snapshot.
        plan_view: Dict[str, Any] = (
            dict(plan_snapshot) if isinstance(plan_snapshot, Mapping) else {}
        )
        plan_view["slots"] = slots
        plan_view["action"] = command.action
        if proposal_context is not None:
            plan_view["execution_proposal_context"] = proposal_context
        if command.entity_schema is not None:
            plan_view["_entity_schema"] = dict(command.entity_schema)

        from core.planning.temporal_proposal import (
            resolve_execution_proposals,
            slots_for_availability_search,
        )

        proposals = resolve_execution_proposals(
            plan_view,
            session_state,
            context=plan_view.get("execution_proposal_context"),
        )
        slots = slots_for_availability_search(
            slots,
            proposals["date_proposal"],
            proposals["time_proposal"],
        )

        return PreparedExecution(
            action=command.action,
            slots=slots,
            stage="AVAILABILITY",
            sku_to_catalog_id=load_catalog_mapping(
                organization_id=organization_id,
                organization_client=organization_client,
            ),
            execution_proposal_context=proposal_context,
            entity_schema=(
                dict(command.entity_schema)
                if command.entity_schema is not None
                else None
            ),
            turn_operation=command.turn_operation,
        )
