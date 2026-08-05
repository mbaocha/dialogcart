"""Stage 01 — intent reconciliation and early handler routing."""



from __future__ import annotations



import logging

from typing import Any, Dict, Optional, Tuple



from core.planning.pipeline.requests import derive_turn_operation

from core.planning.pipeline.types import IntentDecision

from core.planning.planner.intent_resolution import resolve_effective_intent

from core.planning.policy.handler_router import resolve_handler

from core.planning.luma_facts_adapter import facts_to_slots

from core.policy.intent_policy import get_intent_durable, is_intent_plannable

from core.session.confirmation_gate import (
    ConfirmationGateTurn,
    classify_confirmation_gate_turn,
    get_confirmation_state,
    is_confirmation_gate_open,
)

logger = logging.getLogger(__name__)


def _emit_stage2_confirmation_gate_trace(
    *,
    gate_action: Optional[ConfirmationGateTurn],
    gate_session: Dict[str, Any],
    raw_luma_intent: str,
    planning_intent: str = "",
) -> None:
    """Observational only: emit Stage 01 gate classification into Decision Trace."""
    try:
        from core.tracing.confirmation import (
            emit_confirmation_classify_trace,
            emit_confirmation_gate_open_trace,
        )
    except ImportError:
        return

    gate_open = is_confirmation_gate_open(gate_session)
    confirmation_state = get_confirmation_state(gate_session)
    status = ""
    if isinstance(gate_session, dict):
        status = str(gate_session.get("status") or "")

    gate_open_id = emit_confirmation_gate_open_trace(
        session_state=gate_session,
        gate_open=gate_open,
        intent_name=planning_intent or "",
        confirmation_state=confirmation_state,
        status=status or None,
    )
    emit_confirmation_classify_trace(
        gate_action=gate_action.value if gate_action is not None else "",
        gate_open=gate_open,
        raw_intent=raw_luma_intent or "",
        gate_open_id=gate_open_id,
    )


def _session_booking_intent(session_state: Optional[Dict[str, Any]]) -> str:

    if not isinstance(session_state, dict):

        return ""

    intent = session_state.get("intent_name") or session_state.get("intent") or ""

    if isinstance(intent, dict):

        return intent.get("name") or ""

    return str(intent) if intent else ""





def _session_has_committed_booking(session_state: Optional[Dict[str, Any]]) -> bool:
    from core.planning.booking_revision import has_committed_create_appointment

    return has_committed_create_appointment(session_state=session_state)


def reconcile_intent(

    *,

    luma_response: Dict[str, Any],

    session_state: Optional[Dict[str, Any]],

    user_id: str,

    organization_id: int,

    transaction_id: Optional[str] = None,

    source_text: str = "",

) -> Tuple[IntentDecision, Optional[Dict[str, Any]]]:

    """Return intent decision. Stage 01 is decision-only — no session mutation."""

    gate_session = session_state if isinstance(session_state, dict) else {}

    gate_booking_intent = _session_booking_intent(gate_session)

    gate_action = classify_confirmation_gate_turn(luma_response, gate_session)



    luma_intent_obj = luma_response.get("intent", {})

    raw_luma_intent = (

        luma_intent_obj.get("name", "") if isinstance(luma_intent_obj, dict) else ""

    )



    planning_intent, session_reset_occurred = resolve_effective_intent(

        luma_response, gate_session, user_id, organization_id, transaction_id

    )



    if not planning_intent or planning_intent == "UNKNOWN":

        if gate_session and not session_reset_occurred:

            session_intent = _session_booking_intent(gate_session)

            if session_intent:

                try:

                    if get_intent_durable(session_intent):

                        planning_intent = session_intent

                except Exception:

                    planning_intent = session_intent



    confirm_booking_continuation = False



    if gate_action == ConfirmationGateTurn.YES and gate_booking_intent:
        planning_intent = gate_booking_intent
        # Already-committed create workflow: do not re-authorize CONFIRM.
        if _session_has_committed_booking(gate_session):
            confirm_booking_continuation = False
        else:
            confirm_booking_continuation = True

    if gate_action == ConfirmationGateTurn.NO and gate_booking_intent:
        # Keep durable booking intent so confirmation stage can apply reject policy.
        planning_intent = gate_booking_intent



    if planning_intent and planning_intent != "UNKNOWN":

        try:

            is_durable = get_intent_durable(planning_intent)

            if not is_durable:

                from core.session.confirmation_gate import get_confirmation_state



                _session_booking = _session_booking_intent(gate_session)

                _session_confirmation = get_confirmation_state(gate_session)

                if (

                    not confirm_booking_continuation

                    and planning_intent == "CONFIRM_ACTION"

                    and _session_booking

                    and (

                        gate_session.get("status") == "READY"

                        or _session_confirmation == "pending"

                    )

                ):

                    if get_intent_durable(_session_booking):

                        planning_intent = _session_booking
                        # Bare yes after commit must not reopen confirmation.
                        if _session_has_committed_booking(gate_session):
                            confirm_booking_continuation = False
                        else:
                            confirm_booking_continuation = True

                        is_durable = True



                _BOOKING_REFINEMENT = frozenset(

                    {"CORRECTION", "AVAILABILITY", "CHECK_AVAILABILITY"}

                )

                if (

                    not is_durable

                    and planning_intent in _BOOKING_REFINEMENT

                    and _session_booking

                    and get_intent_durable(_session_booking)

                ):

                    planning_intent = _session_booking

                    is_durable = True

                # Cold-start AVAILABILITY / CHECK_AVAILABILITY: NLU may emit an
                # ephemeral exploratory intent. Planning must never own ephemeral
                # intent_name — redirect onto durable CREATE_APPOINTMENT while
                # preserving raw_luma_intent / turn_operation for the turn.
                _EPHEMERAL_AVAILABILITY = frozenset(
                    {"AVAILABILITY", "CHECK_AVAILABILITY"}
                )
                if (
                    not is_durable
                    and planning_intent in _EPHEMERAL_AVAILABILITY
                ):
                    planning_intent = "CREATE_APPOINTMENT"
                    is_durable = True

                if not is_durable:

                    facts_obj = luma_response.get("facts", {})

                    schema = luma_response.get("_entity_schema")
                    if not isinstance(schema, dict):
                        schema = None

                    slots = (

                        facts_to_slots(

                            facts_obj,

                            intent_name=planning_intent,

                            source_text=source_text,

                            entity_schema=schema,

                        )

                        if isinstance(facts_obj, dict)

                        else {}

                    )

                    if isinstance(facts_obj, dict) and "slots" in facts_obj:

                        nested = facts_obj.get("slots", {})

                        if isinstance(nested, dict):

                            slots.update(nested)

                    elif "slots" in luma_response:

                        top = luma_response.get("slots", {})

                        if isinstance(top, dict):

                            slots.update(top)



                    handler = resolve_handler(planning_intent)
                    has_handler = handler is not None
                    is_off_topic = planning_intent == "OFF_TOPIC"

                    turn_operation = derive_turn_operation(

                        raw_luma_intent=raw_luma_intent,

                        planning_intent=planning_intent,

                        luma_response=luma_response,

                    )

                    _emit_stage2_confirmation_gate_trace(
                        gate_action=gate_action,
                        gate_session=gate_session,
                        raw_luma_intent=raw_luma_intent,
                        planning_intent=planning_intent or "",
                    )

                    if is_off_topic:
                        non_durable_status = "OFF_TOPIC"
                        handler_delegated = False
                        handler_name = None
                    elif has_handler:
                        non_durable_status = "HANDLER_DELEGATED"
                        handler_delegated = True
                        handler_name = handler
                    elif is_intent_plannable(planning_intent):
                        # Non-durable but plannable (not availability — those are
                        # redirected to CREATE_APPOINTMENT above): continue so
                        # missing slots can be clarified without session ownership.
                        decision = IntentDecision(
                            planning_intent=planning_intent,
                            raw_luma_intent=raw_luma_intent,
                            turn_operation=turn_operation,
                            session_reset_occurred=session_reset_occurred,
                            confirm_booking_continuation=confirm_booking_continuation,
                            gate_action=gate_action,
                        )
                        return decision, session_state
                    else:
                        non_durable_status = "NON_DURABLE_INTENT"
                        handler_delegated = False
                        handler_name = None

                    decision = IntentDecision(

                        planning_intent=planning_intent,

                        raw_luma_intent=raw_luma_intent,

                        turn_operation=turn_operation,

                        session_reset_occurred=session_reset_occurred,

                        confirm_booking_continuation=confirm_booking_continuation,

                        gate_action=gate_action,

                        handler_delegated=handler_delegated,

                        handler_name=handler_name,

                        non_durable_status=non_durable_status,

                        delegated_search_query=luma_response.get("search_query"),

                        off_topic_query=luma_response.get("off_topic_query"),

                        off_topic_answerable=(
                            luma_response.get("answerable")
                            if isinstance(luma_response.get("answerable"), bool)
                            else None
                        ),

                        off_topic_answer=(
                            luma_response.get("answer")
                            if isinstance(luma_response.get("answer"), str)
                            and str(luma_response.get("answer")).strip()
                            else None
                        ),

                        delegated_slots=slots,

                    )

                    return decision, session_state

        except Exception as exc:

            logger.warning(

                "Durability check failed for %r: %s", planning_intent, exc

            )



    turn_operation = derive_turn_operation(

        raw_luma_intent=raw_luma_intent,

        planning_intent=planning_intent or "",

        luma_response=luma_response,

    )

    _emit_stage2_confirmation_gate_trace(
        gate_action=gate_action,
        gate_session=gate_session,
        raw_luma_intent=raw_luma_intent,
        planning_intent=planning_intent or "",
    )

    decision = IntentDecision(

        planning_intent=planning_intent or "",

        raw_luma_intent=raw_luma_intent,

        turn_operation=turn_operation,

        session_reset_occurred=session_reset_occurred,

        confirm_booking_continuation=confirm_booking_continuation,

        gate_action=gate_action,

    )

    return decision, session_state

