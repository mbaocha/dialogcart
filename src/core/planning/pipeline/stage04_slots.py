"""Stage 04 — missing-slot and clarification policy."""

from __future__ import annotations

from typing import Any, Dict, Optional

from core.planning.pipeline.requests import AttachedRequest
from core.planning.pipeline.types import SlotTurnState, WorkingTurn
from core.planning.planner.missing_slots import derive_ask_next
from core.planning.planner.promptable import (
    apply_preference_decline,
    derive_promptable_slots,
    normalize_declined_slots,
    planning_keys_from_declined_entities,
)
from core.planning.temporal_proposal import (
    has_bound_booking_datetime,
    strip_unconfirmed_temporal_slots,
)
from core.planning.turn_state import finalize_turn_state


def resolve_slot_turn_state(
    *,
    working_turn: WorkingTurn,
    intent_name: str,
    session_state: Optional[Dict[str, Any]],
    attached_request: Optional[AttachedRequest] = None,
) -> SlotTurnState:
    payload = working_turn.payload
    facts_obj = payload.get("facts", {})
    slots_for_filtering = dict(working_turn.effective_collected_slots)
    slots_for_filtering = strip_unconfirmed_temporal_slots(
        slots_for_filtering,
        intent_name,
        session_state,
        confirmed=has_bound_booking_datetime(
            slots_for_filtering, session_state, payload
        ),
    )

    awaiting_slot = None
    prior_declined: list = []
    if isinstance(session_state, dict):
        awaiting_slot = session_state.get("awaiting_slot")
        prior_declined = normalize_declined_slots(
            session_state.get("declined_slots")
        )
        planning = session_state.get("planning")
        if isinstance(planning, dict) and not prior_declined:
            prior_declined = normalize_declined_slots(
                planning.get("declined_slots")
            )

    entity_schema = (
        payload.get("_entity_schema")
        if isinstance(payload.get("_entity_schema"), dict)
        else None
    )

    # Session reset / new booking: drop prior declines.
    if attached_request is not None and getattr(
        attached_request, "session_reset_occurred", False
    ):
        prior_declined = []

    turn_declined_entities = payload.get("declined_entities")
    if not isinstance(turn_declined_entities, list):
        turn_declined_entities = []
    turn_declined_slots = planning_keys_from_declined_entities(
        entity_schema, turn_declined_entities
    )

    declined_slots = apply_preference_decline(
        declined_slots=prior_declined,
        turn_declined_slots=turn_declined_slots,
        slots=slots_for_filtering,
    )
    payload["declined_slots"] = list(declined_slots)

    turn_state = finalize_turn_state(
        intent_name=intent_name,
        merged_session_slots=slots_for_filtering,
        existing_missing_slots=(
            payload.get("missing_slots")
            if isinstance(payload.get("missing_slots"), list)
            else None
        ),
        planning_context={
            "date_proposal": payload.get("date_proposal"),
            "time_proposal": payload.get("time_proposal"),
            "temporal": payload.get("temporal"),
            "nlu_facts": facts_obj if isinstance(facts_obj, dict) else None,
            "issues": payload.get("issues"),
            "raw_luma_slots": payload.get("_raw_luma_slots"),
            "awaiting_slot": awaiting_slot,
            "entity_schema": entity_schema,
        },
    )

    missing_slots = turn_state["missing_slots"]
    effective_collected_slots = turn_state["effective_slots"]
    promptable_slots = derive_promptable_slots(
        entity_schema,
        effective_collected_slots,
        declined_slots,
    )
    ask_next = derive_ask_next(missing_slots, promptable_slots)

    payload["missing_slots"] = missing_slots
    payload["promptable_slots"] = list(promptable_slots)
    if ask_next is not None:
        payload["ask_next"] = ask_next
    else:
        payload.pop("ask_next", None)
    payload["_effective_collected_slots"] = effective_collected_slots
    working_turn.effective_collected_slots = dict(effective_collected_slots)

    needs_clarification = bool(payload.get("needs_clarification", False))
    facts_obj = payload.get("facts", {})
    if isinstance(facts_obj, dict) and "context" in facts_obj:
        clarification_context = facts_obj.get("context", {})
    else:
        clarification_context = payload.get("context", {})
    if not isinstance(clarification_context, dict):
        clarification_context = {}

    return SlotTurnState(
        intent_name=intent_name,
        missing_slots=missing_slots,
        ask_next=ask_next,
        promptable_slots=list(promptable_slots),
        declined_slots=list(declined_slots),
        effective_collected_slots=effective_collected_slots,
        base_status=turn_state["status"],
        needs_clarification=needs_clarification,
        clarification_reason=payload.get("clarification_reason"),
        clarification_data=payload.get("clarification_data"),
        clarification_issues=payload.get("issues", {})
        if isinstance(payload.get("issues"), dict)
        else {},
        clarification_context=clarification_context,
    )
