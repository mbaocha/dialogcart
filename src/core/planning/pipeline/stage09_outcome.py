"""Stage 09 — pure PlanningOutcome and clarification assembly."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from core.engine.outcome_builder import build_outcome_from_decision
from core.planning.pipeline.types import (
    ConfirmationDecision,
    DecisionPlan,
    PlanningOutcome,
    SlotTurnState,
    WorkflowRoute,
    WorkingTurn,
)
from core.planning.temporal_proposal import has_bound_booking_datetime, strip_unconfirmed_temporal_slots
from core.rendering.clarification_router import get_template_key
from core.rendering.response_renderer import _inject_rendering_text, _inject_system_text
from core.session.durable_intents import is_durable_intent
from nlu.clarification.reasons import ClarificationReason

logger = logging.getLogger(__name__)


def _nlu_turn_understanding(working_turn: WorkingTurn) -> Optional[str]:
    """Preserve NLU turn.understanding — never infer from planner state."""
    for source in (
        working_turn.payload,
        working_turn.raw_luma_response_deep_copy,
    ):
        if not isinstance(source, dict):
            continue
        turn = source.get("turn")
        if isinstance(turn, dict):
            value = turn.get("understanding")
            if isinstance(value, str) and value:
                return value
        # Legacy flat field (in-process / older fixtures).
        value = source.get("understanding")
        if isinstance(value, str) and value:
            return value
    return None


def extract_clarification_data(
    clarification_reason: Optional[str],
    issues: Dict[str, Any],
    clarification_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Normalize clarification evidence for Stage 09 outcome rendering."""
    reason = (
        clarification_reason
        if clarification_reason and clarification_reason.strip()
        else None
    )
    missing: List[str] = []
    ambiguous: List[str] = []
    if isinstance(issues, dict):
        for slot_name, slot_value in issues.items():
            if slot_value == "missing":
                missing.append(slot_name)
            elif slot_value == "ambiguous":
                ambiguous.append(slot_name)
            elif slot_name == "time" and isinstance(slot_value, dict):
                missing.append("time")

    if reason == ClarificationReason.UNSUPPORTED_SERVICE.value:
        if "service" not in missing and "service_id" not in missing:
            missing.append("service")

    if not reason and missing:
        if "time" in missing:
            reason = ClarificationReason.MISSING_TIME.value
        elif "date" in missing:
            reason = ClarificationReason.MISSING_DATE.value
        elif "service" in missing or "service_id" in missing:
            reason = ClarificationReason.MISSING_SERVICE.value
        else:
            reason = ClarificationReason.MISSING_CONTEXT.value

    if not reason:
        reason = ClarificationReason.MISSING_CONTEXT.value

    data: Dict[str, Any] = {
        "reason": reason,
        "missing": missing or [],
        "ambiguous": ambiguous or [],
    }
    if clarification_data and isinstance(clarification_data, dict):
        for key, value in clarification_data.items():
            if key not in ("reason", "missing", "ambiguous"):
                data[key] = value
    return data


def derive_clarification_reason_from_missing_slots(missing: List[str]) -> str:
    missing_set = set(missing)
    if missing_set == {"start_date", "end_date"}:
        return "MISSING_DATE_RANGE"
    if missing_set == {"start_date"}:
        return "MISSING_START_DATE"
    if missing_set == {"end_date"}:
        return "MISSING_END_DATE"
    if missing_set == {"service_id"}:
        return "MISSING_SERVICE"
    if missing_set == {"time"}:
        return "MISSING_TIME"
    if missing_set == {"date"}:
        return "MISSING_DATE"
    if "time" in missing_set:
        return "MISSING_TIME"
    # Single non-platform business slot → generic clarification (schema-driven ask).
    if len(missing_set) == 1:
        return "NEEDS_CLARIFICATION"
    return "NEEDS_CLARIFICATION"


def build_clarify_outcome(
    *,
    clarification_reason: str,
    issues: Dict[str, Any],
    context: Dict[str, Any],
    booking: Optional[Dict[str, Any]],
    domain: str,
    clarification_data: Optional[Dict[str, Any]] = None,
    facts: Optional[Dict[str, Any]] = None,
    intent_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the clarification branch of the Stage 09 outcome."""
    template_key = get_template_key(clarification_reason, domain)
    data = extract_clarification_data(clarification_reason, issues, clarification_data)
    missing_slots = data.get("missing", [])
    canonical_reason = derive_clarification_reason_from_missing_slots(missing_slots)
    outcome: Dict[str, Any] = {
        "status": "NEEDS_CLARIFICATION",
        "clarification_reason": canonical_reason,
        "template_key": template_key,
        "data": data,
        "context": context,
        "booking": booking or {},
    }
    if intent_name:
        outcome["intent_name"] = intent_name
    if facts:
        outcome["facts"] = facts
    return outcome


def _build_decision_dict(
    decision_plan: DecisionPlan,
    workflow_route: WorkflowRoute,
) -> Dict[str, Any]:
    plan = dict(decision_plan.plan)
    if workflow_route.route:
        plan["workflow_route"] = workflow_route.route
    decision: Dict[str, Any] = {
        "intent_name": decision_plan.intent_name,
        "plan": plan,
        "facts": decision_plan.facts,
        "booking": decision_plan.booking,
        "service_candidates": decision_plan.service_candidates,
    }
    ask_next = plan.get("ask_next")
    if ask_next is None and isinstance(decision_plan.facts, dict):
        ask_next = decision_plan.facts.get("ask_next")
    if ask_next is not None:
        decision["ask_next"] = ask_next
    if decision_plan.action_name:
        decision["action_name"] = decision_plan.action_name
    if decision_plan.candidate_evidence:
        decision["_candidate_evidence"] = decision_plan.candidate_evidence
    if plan.get("time_match_outcome") is not None:
        decision["time_match_outcome"] = plan["time_match_outcome"]
    if plan.get("time_resolution") is not None:
        decision["time_resolution"] = plan["time_resolution"]
    return decision


def _execution_payload_for_cached_availability(
    session_state: Optional[Dict[str, Any]],
    payload: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    from core.workflows.availability.presentation import (
        availability_cache_from_session,
        ensure_presented_availability,
        presented_availability_from_session,
    )

    # Rare: working payload already carries a normalized execution envelope.
    if isinstance(payload, dict):
        nested = payload.get("availability")
        if isinstance(nested, dict) and payload.get("status"):
            return {**payload, "status": payload.get("status") or "succeeded"}

    presented = None
    if isinstance(payload, dict) and isinstance(
        payload.get("presented_availability"), dict
    ):
        presented = payload["presented_availability"]
    if presented is None:
        presented = presented_availability_from_session(session_state)
    if presented is None:
        presented = ensure_presented_availability(session_state=session_state)
    if isinstance(presented, dict):
        return {"status": "succeeded", "availability": presented}

    cache = availability_cache_from_session(session_state)
    if cache is not None:
        return {
            "status": "succeeded",
            "availability": {
                "slots": list(cache.get("slots") or []),
                "search_date": cache.get("search_date"),
            },
        }
    return None


def assemble_planning_outcome(
    *,
    decision_plan: DecisionPlan,
    workflow_route: WorkflowRoute,
    working_turn: WorkingTurn,
    slot_state: SlotTurnState,
    confirmation: ConfirmationDecision,
    session_state: Optional[Dict[str, Any]],
    domain: str,
    user_id: str,
    organization_id: int,
    planning_only: bool = True,
) -> PlanningOutcome:
    # Confirmation reject: Decision already selected status/missing_slots;
    # Render projects the historical reject envelope shape (no new decisions).
    reject = confirmation.reject_evidence
    if reject is not None and getattr(reject, "rejected", False):
        from core.rendering.booking_confirmation_renderer import (
            render_booking_confirmation_rejected,
        )

        plan = decision_plan.plan if isinstance(decision_plan.plan, dict) else {}
        facts = decision_plan.facts if isinstance(decision_plan.facts, dict) else {}
        slots = dict(facts.get("slots") or {})
        missing = list(plan.get("missing_slots") or [])
        return PlanningOutcome(
            success=True,
            outcome={
                "status": plan.get("status") or "NEEDS_CLARIFICATION",
                "intent_name": decision_plan.intent_name,
                "slots": slots,
                "missing_slots": missing,
                "booking": {},
                "facts": {"slots": slots, "missing_slots": missing},
            },
            merged_luma_response=working_turn.payload,
            text=render_booking_confirmation_rejected(),
            decision=_build_decision_dict(decision_plan, workflow_route),
        )

    decision = _build_decision_dict(decision_plan, workflow_route)
    plan = decision_plan.plan
    plan_status = plan.get("status", "READY")
    awaiting = plan.get("awaiting")
    intent_name = decision_plan.intent_name
    missing_slots = list(slot_state.missing_slots)

    slots = dict(decision_plan.facts.get("slots", {}))
    outcome_slots = strip_unconfirmed_temporal_slots(
        slots,
        intent_name,
        session_state,
        confirmed=has_bound_booking_datetime(slots, session_state, working_turn.payload),
    )

    intentionally_dropped_slots = set(
        working_turn.payload.get("_intentionally_dropped_slots") or set()
    )
    service_candidates = (
        session_state.get("service_candidates")
        if isinstance(session_state, dict)
        else None
    )
    skip_session_service_reinject = bool(
        "service_id" in intentionally_dropped_slots
        or (service_candidates and len(service_candidates) > 0)
    )
    # Current-turn working service wins over stale session service (service revision).
    raw_service_id = None
    effective_slots = working_turn.payload.get("slots", {})
    if isinstance(effective_slots, dict) and effective_slots.get("service_id"):
        raw_service_id = effective_slots["service_id"]
    if not raw_service_id and not skip_session_service_reinject and isinstance(
        session_state, dict
    ):
        session_slots = session_state.get("slots", {})
        if isinstance(session_slots, dict) and "service_id" in session_slots:
            raw_service_id = session_slots["service_id"]
    if not raw_service_id and "service_id" in outcome_slots:
        raw_service_id = outcome_slots["service_id"]
    if raw_service_id:
        outcome_slots["service_id"] = raw_service_id
    outcome_slots.pop("_canonical_service_id", None)

    populated_plan = {
        "intent": intent_name,
        "intent_name": intent_name,
        "stage": plan.get("stage"),
        "action": plan.get("action"),
        "missing_slots": missing_slots,
        "ask_next": plan.get("ask_next") or decision_plan.facts.get("ask_next"),
        "promptable_slots": list(
            plan.get("promptable_slots")
            or decision_plan.facts.get("promptable_slots")
            or getattr(slot_state, "promptable_slots", [])
            or []
        ),
        "declined_slots": list(
            plan.get("declined_slots")
            or decision_plan.facts.get("declined_slots")
            or getattr(slot_state, "declined_slots", [])
            or []
        ),
        "slots": outcome_slots,
        "status": plan_status,
        "executable_actions": plan.get("executable_actions", []),
        "allowed_actions": plan.get("allowed_actions", []),
        "blocked_actions": plan.get("blocked_actions", []),
        "awaiting": awaiting,
    }
    if plan.get("active_capability"):
        populated_plan["active_capability"] = plan.get("active_capability")
    if workflow_route.route:
        populated_plan["workflow_route"] = workflow_route.route

    if plan.get("turn_operation"):
        populated_plan["turn_operation"] = plan.get("turn_operation")
    if isinstance(plan.get("execution_proposal_context"), dict):
        populated_plan["execution_proposal_context"] = dict(
            plan["execution_proposal_context"]
        )
    if plan.get("availability_reshow"):
        populated_plan["availability_reshow"] = True
    # Availability criteria identity for SEARCH fingerprint / request adapter.
    entity_schema = plan.get("_entity_schema")
    if not isinstance(entity_schema, dict) and isinstance(decision_plan.facts, dict):
        entity_schema = decision_plan.facts.get("_entity_schema")
    if isinstance(entity_schema, dict):
        populated_plan["_entity_schema"] = entity_schema

    outcome_facts = {
        **decision_plan.facts,
        "missing_slots": missing_slots,
        "ask_next": populated_plan.get("ask_next"),
        "promptable_slots": populated_plan.get("promptable_slots"),
        "declined_slots": populated_plan.get("declined_slots"),
        "slots": outcome_slots,
    }
    from core.planning.planning_evidence import (
        planning_evidence_outcome_key,
        planning_evidence_payload_key,
        require_planning_evidence,
    )

    _evidence = require_planning_evidence(working_turn.payload, populated_plan)
    outcome_facts[planning_evidence_outcome_key()] = _evidence
    populated_plan[planning_evidence_outcome_key()] = _evidence
    populated_plan[planning_evidence_payload_key()] = _evidence

    if slot_state.needs_clarification and plan_status == "NEEDS_CLARIFICATION":
        clarify = build_clarify_outcome(
            clarification_reason=slot_state.clarification_reason or "",
            issues=slot_state.clarification_issues,
            context=slot_state.clarification_context,
            booking=decision_plan.booking,
            domain=domain,
            clarification_data=slot_state.clarification_data,
            facts=outcome_facts,
            intent_name=intent_name,
        )
        outcome = {
            **clarify,
            "intent_name": intent_name,
            "stage": plan.get("stage"),
            "action": plan.get("action"),
            "missing_slots": missing_slots,
            "ask_next": populated_plan.get("ask_next"),
            "promptable_slots": populated_plan.get("promptable_slots"),
            "declined_slots": populated_plan.get("declined_slots"),
            "slots": outcome_slots,
            "plan": populated_plan,
        }
        understanding = _nlu_turn_understanding(working_turn)
        if isinstance(understanding, str) and understanding:
            outcome["turn"] = {"understanding": understanding}
            populated_plan["turn"] = {"understanding": understanding}
        result = PlanningOutcome(
            success=True,
            outcome=outcome,
            merged_luma_response=working_turn.payload,
            decision=decision,
        )
        turn_result = result.to_turn_result()
        _inject_rendering_text(turn_result, decision, session_state)
        if turn_result.get("text"):
            result = PlanningOutcome(
                success=True,
                outcome={**outcome, "text": turn_result.get("text")},
                merged_luma_response=working_turn.payload,
                decision=decision,
                text=turn_result.get("text"),
            )
        return result

    outcome = {
        "intent_name": intent_name,
        "stage": plan.get("stage"),
        "action": plan.get("action"),
        "missing_slots": missing_slots,
        "ask_next": populated_plan.get("ask_next"),
        "promptable_slots": populated_plan.get("promptable_slots"),
        "declined_slots": populated_plan.get("declined_slots"),
        "slots": outcome_slots,
        "status": plan_status,
        "plan": populated_plan,
        "facts": outcome_facts,
    }
    if plan.get("active_capability"):
        outcome["active_capability"] = plan.get("active_capability")

    # Preserve NLU utterance-understanding outcome (not planner status).
    understanding = _nlu_turn_understanding(working_turn)
    if isinstance(understanding, str) and understanding:
        outcome["turn"] = {"understanding": understanding}
        populated_plan["turn"] = {"understanding": understanding}

    result = PlanningOutcome(
        success=True,
        outcome=outcome,
        merged_luma_response=working_turn.payload,
        decision=decision,
    )

    if plan_status not in ("NEEDS_CLARIFICATION", "AWAITING_CAPABILITY", "EXECUTED"):
        turn_result = result.to_turn_result()
        _inject_system_text(turn_result, decision)

    if plan.get("availability_reshow"):
        exec_payload = _execution_payload_for_cached_availability(
            session_state, working_turn.payload
        )
        if exec_payload:
            from core.rendering.response_renderer import _inject_availability_text

            turn_result = result.to_turn_result()
            _inject_availability_text(
                turn_result, decision, exec_payload, session_state=session_state
            )
            if turn_result.get("text"):
                result = PlanningOutcome(
                    success=True,
                    outcome={**outcome, "text": turn_result.get("text")},
                    merged_luma_response=working_turn.payload,
                    decision=decision,
                    text=turn_result.get("text"),
                )

    if plan_status == "NEEDS_CLARIFICATION":
        turn_result = result.to_turn_result()
        _inject_rendering_text(turn_result, decision, session_state)
        if turn_result.get("text"):
            result = PlanningOutcome(
                success=result.success,
                outcome={**outcome, "text": turn_result.get("text")},
                merged_luma_response=working_turn.payload,
                decision=decision,
                text=turn_result.get("text"),
            )

    if plan_status in ("AWAITING_CONFIRMATION", "AWAITING_CAPABILITY"):
        outcome_dict = build_outcome_from_decision(decision)
        outcome_dict["status"] = plan_status
        outcome_dict["awaiting"] = awaiting
        if working_turn.payload.get("_raw_luma_response"):
            facts = outcome_dict.get("facts", {})
            if not isinstance(facts, dict):
                facts = {}
            facts["_raw_luma_response"] = working_turn.payload["_raw_luma_response"]
            outcome_dict["facts"] = facts
        confirmation_text = None
        if plan_status == "AWAITING_CONFIRMATION":
            outcome_dict["booking"] = decision_plan.booking
            from core.rendering.booking_confirmation_renderer import (
                prefix_with_revision_acknowledgement,
                render_booking_confirmation_prompt,
            )

            confirm_slots = outcome_dict.get("slots") or outcome_facts.get("slots", {})
            if isinstance(confirm_slots, dict):
                confirmation_text = render_booking_confirmation_prompt(confirm_slots)
                revision_summary = working_turn.payload.get("_revision_summary")
                confirmation_text = prefix_with_revision_acknowledgement(
                    confirmation_text, revision_summary
                )
        if plan_status == "AWAITING_CAPABILITY" and plan.get("active_capability"):
            outcome_dict["active_capability"] = plan.get("active_capability")
        result = PlanningOutcome(
            success=True,
            outcome=outcome_dict,
            merged_luma_response=working_turn.payload,
            decision=decision,
            text=confirmation_text,
        )

    if populated_plan.get("intent_name") and not is_durable_intent(
        populated_plan.get("intent_name", "")
    ):
        if populated_plan.get("intent_name") not in ("", "UNKNOWN"):
            raise AssertionError(
                f"Ephemeral intent '{populated_plan.get('intent_name')}' leaked into planning."
            )

    logger.info(
        "[OUTCOME] user_id=%s intent=%s stage=%s action=%s missing_slots=%s slots=%s",
        user_id,
        intent_name,
        result.outcome.get("stage"),
        result.outcome.get("action"),
        missing_slots,
        json.dumps(outcome_slots, default=str, ensure_ascii=True),
    )
    return result


def assemble_handler_delegated_outcome(
    *,
    decision_plan: DecisionPlan,
    working_turn: Optional[WorkingTurn] = None,
    luma_response: Optional[Dict[str, Any]] = None,
) -> PlanningOutcome:
    """Envelope for non-durable Decision early exit (OFF_TOPIC or RAG HANDLER_DELEGATED)."""
    plan = dict(decision_plan.plan) if isinstance(decision_plan.plan, dict) else {}
    facts = decision_plan.facts if isinstance(decision_plan.facts, dict) else {}
    slots = dict(facts.get("slots") or {})
    base_outcome = {
        "intent_name": decision_plan.intent_name,
        "slots": slots,
        "missing_slots": [],
        "facts": {
            "slots": slots,
            "missing_slots": [],
        },
    }
    status = plan.get("status")
    if status == "HANDLER_DELEGATED":
        outcome = {
            **base_outcome,
            "status": "HANDLER_DELEGATED",
            "active_handler": plan.get("active_handler"),
            "search_query": plan.get("search_query"),
        }
    elif status == "OFF_TOPIC":
        outcome = {
            **base_outcome,
            "status": "OFF_TOPIC",
            "off_topic_query": plan.get("off_topic_query"),
        }
        if plan.get("answerable") is not None:
            outcome["answerable"] = plan.get("answerable")
        if plan.get("answer") is not None:
            outcome["answer"] = plan.get("answer")
    else:
        outcome = {**base_outcome, "status": status}

    understanding = None
    if working_turn is not None:
        understanding = _nlu_turn_understanding(working_turn)
    if not understanding and isinstance(luma_response, dict):
        turn = luma_response.get("turn")
        if isinstance(turn, dict):
            value = turn.get("understanding")
            if isinstance(value, str) and value:
                understanding = value
        if not understanding:
            value = luma_response.get("understanding")
            if isinstance(value, str) and value:
                understanding = value
    if isinstance(understanding, str) and understanding:
        turn_meta = {"understanding": understanding}
        outcome["turn"] = turn_meta
        plan["turn"] = turn_meta

    merged = working_turn.payload if working_turn else luma_response
    return PlanningOutcome(
        success=True,
        outcome=outcome,
        merged_luma_response=merged if isinstance(merged, dict) else None,
        decision={
            "intent_name": decision_plan.intent_name,
            "plan": plan,
            "facts": facts,
            "booking": decision_plan.booking,
        },
    )
