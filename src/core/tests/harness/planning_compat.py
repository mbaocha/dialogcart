"""Test-only planning compatibility helpers.

These wrappers exist for isolated unit/orchestration tests. Production code must
call ``run_planning_pipeline()`` directly.

Isolated stage tests may still plant attachment fields on the working payload
(``_turn_operation``, ``_confirm_booking_continuation``, ``_gate_action``) so
``_attached_request_from_payload`` can rebuild ``AttachedRequest``. Production
no longer writes those fields.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from core.planning.pipeline.decision import DecisionInput, decide
from core.planning.pipeline.orchestrator import derive_workflow_route
from core.planning.pipeline.requests import AttachedRequest
from core.planning.pipeline.stage03_revision import apply_revision_policy
from core.planning.pipeline.stage04_slots import resolve_slot_turn_state
from core.planning.pipeline.stage05_availability import resolve_availability
from core.planning.pipeline.stage06_confirmation import resolve_confirmation
from core.planning.pipeline.stage07_capability import resolve_capability_gating
from core.planning.pipeline.stage09_outcome import assemble_planning_outcome
from core.planning.pipeline.types import WorkingTurn
from core.session.confirmation_gate import ConfirmationGateTurn
from core.planning.planning_mutations import apply_confirmation_planning_mutations


def _attached_request_from_payload(
    payload: Dict[str, Any],
    *,
    intent_name: str = "",
) -> AttachedRequest:
    """Test helper: reconstruct attachment fields from a pre-merged test payload."""
    planning_intent = intent_name or str(
        (payload.get("intent") or {}).get("name")
        if isinstance(payload.get("intent"), dict)
        else payload.get("_planning_intent")
        or ""
    )
    gate_action = None
    gate_raw = payload.get("_gate_action")
    if gate_raw:
        try:
            gate_action = ConfirmationGateTurn(gate_raw)
        except ValueError:
            gate_action = None
    return AttachedRequest(
        planning_intent=planning_intent,
        turn_operation=payload.get("_turn_operation", "NONE"),
        session_reset_occurred=False,
        confirm_booking_continuation=bool(payload.get("_confirm_booking_continuation")),
        gate_action=gate_action,
    )


def normalize_luma_response(luma_response: Dict[str, Any]) -> Dict[str, Any]:
    """Adapter-level normalization (contract shape only)."""
    if not isinstance(luma_response, dict):
        return {}
    normalized = dict(luma_response)
    intent = normalized.get("intent")
    if not isinstance(intent, dict):
        normalized["intent"] = {"name": str(intent or "")}
    legacy_intent = normalized.pop("_effective_intent", None)
    if legacy_intent and (
        not normalized["intent"].get("name")
        or normalized["intent"].get("name") == "UNKNOWN"
    ):
        normalized["intent"]["name"] = legacy_intent
    if "slots" not in normalized or not isinstance(normalized.get("slots"), dict):
        normalized["slots"] = normalized.get("slots") or {}
    if "missing_slots" not in normalized or not isinstance(
        normalized.get("missing_slots"), list
    ):
        normalized["missing_slots"] = normalized.get("missing_slots") or []
    return normalized


def run_planning_from_working_payload(
    *,
    working_payload: Dict[str, Any],
    intent_name: str,
    session_state: Optional[Dict[str, Any]],
    organization_id: int,
    domain: str,
    user_id: str,
    organization_client: Any = None,
) -> Dict[str, Any]:
    """Test helper: run stages 5–11 on a pre-merged working payload."""
    attached_request = _attached_request_from_payload(
        working_payload,
        intent_name=intent_name,
    )
    working_turn = WorkingTurn(
        payload=working_payload,
        effective_collected_slots=dict(
            working_payload.get("_effective_collected_slots")
            or working_payload.get("slots")
            or {}
        ),
    )
    if organization_client is not None:
        from core.planning.pipeline.orchestrator import _hydrate_org_facts

        _hydrate_org_facts(working_turn.payload, organization_id, organization_client)

    apply_revision_policy(working_turn, session_state)
    slot_state = resolve_slot_turn_state(
        working_turn=working_turn,
        intent_name=intent_name,
        session_state=session_state,
        attached_request=attached_request,
    )
    availability = resolve_availability(
        slot_state=slot_state,
        working_turn=working_turn,
        session_state=session_state,
        organization_id=organization_id,
        attached_request=attached_request,
    )
    confirmation = resolve_confirmation(
        attached_request=attached_request,
        slot_state=slot_state,
        working_turn=working_turn,
        availability=availability,
        session_state=session_state,
        gate_booking_intent="",
        user_id=user_id,
    )
    apply_confirmation_planning_mutations(
        working_turn, confirmation, session_state=session_state
    )
    if confirmation.slots_adjusted:
        slot_state = resolve_slot_turn_state(
            working_turn=working_turn,
            intent_name=intent_name,
            session_state=session_state,
            attached_request=attached_request,
        )
        availability = resolve_availability(
            slot_state=slot_state,
            working_turn=working_turn,
            session_state=session_state,
            organization_id=organization_id,
            attached_request=attached_request,
        )
    capability = resolve_capability_gating(
        slot_state=slot_state,
        working_turn=working_turn,
        availability_ready=availability.availability_ready,
        confirmation=confirmation,
        session_state=session_state,
        organization_id=organization_id,
    )
    decision_plan = decide(
        DecisionInput(
            attached_request=attached_request,
            working_turn=working_turn,
            slot_state=slot_state,
            availability=availability,
            confirmation=confirmation,
            capability=capability,
            session_state=session_state,
            organization_id=organization_id,
        )
    )
    workflow_route = derive_workflow_route(decision_plan)
    return assemble_planning_outcome(
        decision_plan=decision_plan,
        workflow_route=workflow_route,
        working_turn=working_turn,
        slot_state=slot_state,
        confirmation=confirmation,
        session_state=session_state,
        domain=domain,
        user_id=user_id,
        organization_id=organization_id,
    ).to_turn_result()


def process_luma_response(
    luma_response: Dict[str, Any],
    domain: str,
    user_id: str,
    organization_id: int,
    session_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Test helper over stages 5–11 with adapter-level NLU normalization."""
    payload = normalize_luma_response(luma_response)
    intent_obj = payload.get("intent", {})
    intent_name = intent_obj.get("name", "") if isinstance(intent_obj, dict) else ""

    result = run_planning_from_working_payload(
        working_payload=payload,
        intent_name=intent_name,
        session_state=session_state,
        organization_id=organization_id,
        domain=domain,
        user_id=user_id,
    )
    decision = result.get("_decision")
    if not isinstance(decision, dict):
        decision = {
            "intent_name": result.get("outcome", {}).get("intent_name", intent_name),
            "plan": result.get("outcome", {}).get("plan", {}),
            "facts": result.get("outcome", {}).get("facts", {}),
            "booking": result.get("outcome", {}).get("booking", {}),
        }
    return decision


def build_decision_plan(
    intent_name: str,
    luma_response: Dict[str, Any],
    domain: str,
    organization_id: int,
    availability_resolved: bool = False,
    session_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Test helper: build a decision plan dict from a pre-merged payload."""
    _ = domain
    attached_request = _attached_request_from_payload(
        luma_response,
        intent_name=intent_name,
    )
    working_turn = WorkingTurn(
        payload=luma_response,
        effective_collected_slots=dict(
            luma_response.get("_effective_collected_slots")
            or luma_response.get("slots")
            or {}
        ),
    )
    slot_state = resolve_slot_turn_state(
        working_turn=working_turn,
        intent_name=intent_name,
        session_state=session_state,
        attached_request=attached_request,
    )
    availability = resolve_availability(
        slot_state=slot_state,
        working_turn=working_turn,
        session_state=session_state,
        organization_id=organization_id,
        attached_request=attached_request,
    )
    if availability_resolved and not availability.availability_ready:
        from dataclasses import replace

        availability = replace(availability, availability_ready=True)
    confirmation = resolve_confirmation(
        attached_request=attached_request,
        slot_state=slot_state,
        working_turn=working_turn,
        availability=availability,
        session_state=session_state,
        gate_booking_intent="",
        user_id="",
    )
    apply_confirmation_planning_mutations(
        working_turn, confirmation, session_state=session_state
    )
    if confirmation.slots_adjusted:
        slot_state = resolve_slot_turn_state(
            working_turn=working_turn,
            intent_name=intent_name,
            session_state=session_state,
            attached_request=attached_request,
        )
        availability = resolve_availability(
            slot_state=slot_state,
            working_turn=working_turn,
            session_state=session_state,
            organization_id=organization_id,
            attached_request=attached_request,
        )
    capability = resolve_capability_gating(
        slot_state=slot_state,
        working_turn=working_turn,
        availability_ready=availability.availability_ready,
        confirmation=confirmation,
        session_state=session_state,
        organization_id=organization_id,
    )
    decision_plan = decide(
        DecisionInput(
            attached_request=attached_request,
            working_turn=working_turn,
            slot_state=slot_state,
            availability=availability,
            confirmation=confirmation,
            capability=capability,
            session_state=session_state,
            organization_id=organization_id,
        )
    )
    return decision_plan.plan
