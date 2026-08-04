"""Canonical planning pipeline orchestrator.

Stage 01–09 order is unchanged. Decision (``decide()``) is the sole owner of
planner action/status/stage/awaiting selection after Attach + Evaluate evidence.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from core.adapters.nlu import LumaClient
from core.planning.nlu_failure_fallback import build_nlu_failure_fallback
from core.planning.nlu_invocation import invoke_nlu_for_planning
from core.planning.pipeline.decision import (
    DecisionInput,
    decide,
    decide_handler_delegation,
)
from core.planning.pipeline.relationship_evaluator import (
    evaluate_relationship,
    snapshot_conversation_context,
)
from core.planning.pipeline.requests import (
    AttachedRequest,
    build_attached_request,
    build_current_request,
    build_legacy_attachment_read_report,
)
from core.session.invalidation import apply_confirmation_planning_mutations
from core.planning.pipeline.stage01_intent import reconcile_intent
from core.planning.pipeline.stage02_working_turn import build_working_turn
from core.planning.pipeline.stage03_revision import apply_revision_policy
from core.planning.pipeline.stage04_slots import resolve_slot_turn_state
from core.planning.pipeline.stage05_availability import resolve_availability
from core.planning.pipeline.stage06_confirmation import resolve_confirmation
from core.planning.pipeline.stage07_capability import resolve_capability_gating
from core.planning.pipeline.stage09_outcome import (
    assemble_handler_delegated_outcome,
    assemble_planning_outcome,
)
from core.planning.pipeline.types import (
    CapabilityDecision,
    DecisionPlan,
    WorkflowRoute,
)
from core.workflows.router import WorkflowRouter

logger = logging.getLogger(__name__)


def derive_workflow_route(decision_plan: DecisionPlan) -> WorkflowRoute:
    """Derive the workflow route from the final policy client."""
    client_name = decision_plan.policy_client
    route = WorkflowRouter().get_route(client_name)
    return WorkflowRoute(route=route, client_name=client_name)


def _emit_architecture_observables(
    *,
    luma_response: Dict[str, Any],
    source_text: str,
    attached_request: AttachedRequest,
    session_for_context: Optional[Dict[str, Any]],
    decision_plan: Optional[DecisionPlan] = None,
    decision_input: Optional[DecisionInput] = None,
) -> None:
    """Observational architecture traces only — must not influence Decision."""
    try:
        from core.tracing.architecture import emit_phase1_architecture_traces

        current_request = build_current_request(
            luma_response,
            source_text=source_text,
        )
        conversation_context = snapshot_conversation_context(session_for_context)
        relationship = evaluate_relationship(
            current_request=current_request,
            conversation_context=conversation_context,
            session_state=session_for_context,
            gate_action=attached_request.gate_action,
        )
        emit_phase1_architecture_traces(
            current_request=current_request,
            attached_request=attached_request,
            conversation_context=conversation_context,
            relationship=relationship,
            decision_plan=decision_plan,
            legacy_reads=build_legacy_attachment_read_report(),
            decision_input=decision_input,
        )
    except Exception as exc:
        logger.debug("Architecture observables skipped: %s", exc)


def _hydrate_org_facts(
    payload: Dict[str, Any],
    organization_id: int,
    organization_client: Any,
) -> None:
    if not organization_client:
        return
    try:
        org_details = organization_client.get_details(organization_id)
        if isinstance(org_details, dict):
            org_data = org_details.get("organization") or org_details
            if isinstance(org_data, dict):
                facts = payload.setdefault("facts", {})
                if not isinstance(facts, dict):
                    facts = {}
                    payload["facts"] = facts
                facts["org"] = org_data
    except Exception as exc:
        logger.debug("Failed to fetch organization data: %s", exc)


def _session_booking_intent(session_state: Optional[Dict[str, Any]]) -> str:
    if not isinstance(session_state, dict):
        return ""
    intent = session_state.get("intent_name") or session_state.get("intent") or ""
    if isinstance(intent, dict):
        return intent.get("name") or ""
    return str(intent) if intent else ""


def run_planning_pipeline(
    *,
    user_id: str,
    text: str,
    organization_id: int,
    derived_domain: str,
    timezone: str = "UTC",
    tenant_context: Optional[Dict[str, Any]] = None,
    entity_schema: Optional[Dict[str, Any]] = None,
    session_state: Optional[Dict[str, Any]] = None,
    luma_client: Optional[LumaClient] = None,
    organization_client: Any = None,
    transaction_id: Optional[str] = None,
    planning_only: bool = True,
    apply_domain_filter: bool = True,
) -> Dict[str, Any]:
    """Execute stages 01–09 and return the legacy turn envelope."""
    if luma_client is None:
        luma_client = LumaClient()

    nlu_result = invoke_nlu_for_planning(
        user_id=user_id,
        text=text,
        derived_domain=derived_domain,
        timezone=timezone,
        tenant_context=tenant_context,
        entity_schema=entity_schema,
        session_state=session_state,
        luma_client=luma_client,
    )
    if nlu_result.status == "upstream_error":
        return build_nlu_failure_fallback(
            session_state,
            user_id=user_id,
            error_code="upstream_error",
            error_message=nlu_result.error_message or "",
        )
    if nlu_result.status == "empty_response":
        return build_nlu_failure_fallback(
            session_state,
            user_id=user_id,
            error_code="upstream_error",
            error_message=nlu_result.error_message or "Luma returned empty response",
            fallback_reason="empty_response",
        )
    if nlu_result.status == "contract_violation":
        return build_nlu_failure_fallback(
            session_state,
            user_id=user_id,
            error_code="contract_violation",
            error_message=nlu_result.error_message or "",
        )

    luma_response = nlu_result.luma_response or {}
    if entity_schema is not None:
        # Request-scoped allowlist for fact→slot promotion (not persisted).
        luma_response = {**luma_response, "_entity_schema": entity_schema}
    original_session_state = session_state

    intent_decision, session_state = reconcile_intent(
        luma_response=luma_response,
        session_state=session_state,
        user_id=user_id,
        organization_id=organization_id,
        transaction_id=transaction_id,
        source_text=text,
    )

    attached_request = build_attached_request(intent_decision)
    _architecture_session = original_session_state or session_state

    if intent_decision.non_durable_status:
        decision_plan = decide_handler_delegation(intent_decision)
        _emit_architecture_observables(
            luma_response=luma_response,
            source_text=text,
            attached_request=attached_request,
            session_for_context=_architecture_session,
            decision_plan=decision_plan,
        )
        return assemble_handler_delegated_outcome(
            decision_plan=decision_plan,
            luma_response=luma_response,
        ).to_turn_result()

    working_turn = build_working_turn(
        luma_response=luma_response,
        raw_luma_response_deep_copy=nlu_result.raw_luma_response_deep_copy,
        attached_request=attached_request,
        session_state=session_state,
        original_session_state=original_session_state,
        source_text=text,
        tenant_context=tenant_context,
        apply_domain_filter=apply_domain_filter,
        entity_schema=entity_schema,
    )
    _hydrate_org_facts(working_turn.payload, organization_id, organization_client)

    apply_revision_policy(working_turn, original_session_state or session_state)

    planning_intent = attached_request.planning_intent
    persisted_session = original_session_state or session_state

    slot_state = resolve_slot_turn_state(
        working_turn=working_turn,
        intent_name=planning_intent,
        session_state=persisted_session,
        attached_request=attached_request,
    )

    availability = resolve_availability(
        slot_state=slot_state,
        working_turn=working_turn,
        session_state=persisted_session,
        organization_id=organization_id,
        attached_request=attached_request,
    )

    confirmation = resolve_confirmation(
        attached_request=attached_request,
        slot_state=slot_state,
        working_turn=working_turn,
        availability=availability,
        session_state=persisted_session,
        gate_booking_intent=_session_booking_intent(persisted_session),
        user_id=user_id,
    )

    # Single planning mutation boundary for Stage 06 evidence (reject / consume /
    # bound-datetime clear). Working turn + live request-scoped session_state.
    apply_confirmation_planning_mutations(
        working_turn,
        confirmation,
        session_state=persisted_session,
    )

    if confirmation.slots_adjusted:
        slot_state = resolve_slot_turn_state(
            working_turn=working_turn,
            intent_name=planning_intent,
            session_state=persisted_session,
            attached_request=attached_request,
        )
        availability = resolve_availability(
            slot_state=slot_state,
            working_turn=working_turn,
            session_state=persisted_session,
            organization_id=organization_id,
            attached_request=attached_request,
        )

    # Confirmation reject: Evaluate emits evidence; Decision selects outcome.
    # Slot/availability evidence above must reflect REJECT_CONFIRMATION first.
    if confirmation.reject_evidence is not None and confirmation.reject_evidence.rejected:
        decision_input = DecisionInput(
            attached_request=attached_request,
            working_turn=working_turn,
            slot_state=slot_state,
            availability=availability,
            confirmation=confirmation,
            capability=CapabilityDecision(),
            session_state=persisted_session,
            organization_id=organization_id,
            confirmation_reject=confirmation.reject_evidence,
        )
        decision_plan = decide(decision_input)
        _emit_architecture_observables(
            luma_response=luma_response,
            source_text=text,
            attached_request=attached_request,
            session_for_context=_architecture_session,
            decision_plan=decision_plan,
            decision_input=decision_input,
        )
        return assemble_planning_outcome(
            decision_plan=decision_plan,
            workflow_route=WorkflowRoute(route=None, client_name=None),
            working_turn=working_turn,
            slot_state=slot_state,
            confirmation=confirmation,
            session_state=persisted_session,
            domain=derived_domain,
            user_id=user_id,
            organization_id=organization_id,
            planning_only=planning_only,
        ).to_turn_result()

    from core.planning.pipeline.decision import (
        apply_confirmation_evidence_to_availability,
    )

    # Stage 06 typed evidence projects onto availability evidence before Decision.
    availability = apply_confirmation_evidence_to_availability(availability, confirmation)

    capability = resolve_capability_gating(
        slot_state=slot_state,
        working_turn=working_turn,
        availability_ready=availability.availability_ready,
        confirmation=confirmation,
        session_state=persisted_session,
        organization_id=organization_id,
    )

    decision_input = DecisionInput(
        attached_request=attached_request,
        working_turn=working_turn,
        slot_state=slot_state,
        availability=availability,
        confirmation=confirmation,
        capability=capability,
        session_state=persisted_session,
        organization_id=organization_id,
    )
    decision_plan = decide(decision_input)

    _emit_architecture_observables(
        luma_response=luma_response,
        source_text=text,
        attached_request=attached_request,
        session_for_context=_architecture_session,
        decision_plan=decision_plan,
        decision_input=decision_input,
    )

    workflow_route = derive_workflow_route(decision_plan)

    outcome = assemble_planning_outcome(
        decision_plan=decision_plan,
        workflow_route=workflow_route,
        working_turn=working_turn,
        slot_state=slot_state,
        confirmation=confirmation,
        session_state=persisted_session,
        domain=derived_domain,
        user_id=user_id,
        organization_id=organization_id,
        planning_only=planning_only,
    )
    return outcome.to_turn_result()
