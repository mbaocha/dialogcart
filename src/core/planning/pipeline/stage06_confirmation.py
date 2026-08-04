"""Stage 06 — confirmation policy."""



from __future__ import annotations



import logging

from typing import Any, Dict, Optional



from core.planning.booking_revision import has_committed_create_appointment

from core.planning.facts import derive_user_confirmation_satisfied

from core.planning.pipeline.decision import (
    AvailabilityInvalidationEvidence,
    BoundDatetimeClearEvidence,
    ConfirmationRejectEvidence,
)
from core.planning.pipeline.requests import (
    AttachedRequest,

    TurnOperation,

    is_availability_turn_operation,

)

from core.planning.pipeline.types import (

    AvailabilityDecision,

    ConfirmationDecision,

    SlotTurnState,

    WorkingTurn,

)

from core.planning.temporal_proposal import has_bound_booking_datetime

from core.planning.time_resolution import TIME_MATCH_EXACT

from core.session.confirmation_gate import (

    ConfirmationGateTurn,

    consume_confirmation_state,

    get_confirmation_state,

    set_confirmation_state,

)

from core.session.invalidation import InvalidationTrigger, apply_invalidation



logger = logging.getLogger(__name__)


def _turn_understanding(payload: Dict[str, Any]) -> Optional[str]:
    turn = payload.get("turn")
    if isinstance(turn, dict):
        value = turn.get("understanding")
        if isinstance(value, str) and value:
            return value
    return None


def _non_superseding_unrecognized_pending(
    payload: Dict[str, Any],
    confirmation_state: Optional[str],
) -> bool:
    """True when ANOTHER_REQUEST must not consume pending confirmation.

    Unrecognized input with no current-turn planning evidence is not workflow
    supersession — preserve authorization so recovery can re-ask.
    """
    if confirmation_state != "pending":
        return False
    if _turn_understanding(payload) != "UNRECOGNIZED_INPUT":
        return False
    from core.planning.planning_evidence import require_planning_evidence

    if require_planning_evidence(payload):
        return False
    return True


_SESSION_AVAILABILITY_KEYS = (

    "presented_availability",

    "availability_fingerprint",

    "last_execution_result",

    "availability_presentation",

)





def _same_turn_time_rebind(working_turn: WorkingTurn, turn_operation: TurnOperation) -> bool:

    """True when merge already bound a new exact time on this turn."""

    if is_availability_turn_operation(turn_operation):

        return False

    payload = working_turn.payload

    if payload.get("time_match_outcome") != TIME_MATCH_EXACT:

        return False

    return has_bound_booking_datetime(

        working_turn.effective_collected_slots,

        None,

        payload,

    )





def _maybe_enter_booking_confirmation_pending(

    intent_name: str,

    payload: Dict[str, Any],

    *,

    missing_slots: list,

    needs_clarification: bool,

    availability_ready: bool,

    confirmation_state: Optional[str],

    session_state: Optional[Dict[str, Any]],

) -> Optional[str]:

    if intent_name != "CREATE_APPOINTMENT":

        return confirmation_state



    effective_slots = payload.get("_effective_collected_slots") or payload.get("slots", {})

    session_slots = (

        session_state.get("slots") if isinstance(session_state, dict) else None

    )

    if has_committed_create_appointment(effective_slots) or has_committed_create_appointment(

        session_slots

    ):

        return confirmation_state

    if confirmation_state is not None:

        return confirmation_state

    if missing_slots or needs_clarification or not availability_ready:

        return confirmation_state

    if not has_bound_booking_datetime(effective_slots, session_state, payload):

        return confirmation_state



    previous_state = confirmation_state

    set_confirmation_state(payload, "pending")

    try:

        from core.tracing.confirmation import emit_confirmation_enter_pending_trace



        emit_confirmation_enter_pending_trace(

            entered=True,

            previous_state=previous_state,

            missing_slots=missing_slots,

            availability_resolved=availability_ready,

            time_selection_ready=True,

        )

    except ImportError:

        pass

    logger.info(

        "[BOOKING_CONFIRMATION] CREATE_APPOINTMENT commit-ready — "

        "setting confirmation_state=pending"

    )

    return "pending"





def _preserve_session_availability_cache(

    payload: Dict[str, Any],

    session_state: Optional[Dict[str, Any]],

) -> None:

    if not isinstance(session_state, dict):

        return

    for key in _SESSION_AVAILABILITY_KEYS:

        if session_state.get(key) is not None and payload.get(key) is None:

            payload[key] = session_state[key]





def _bound_datetime_clear_evidence(
    *,
    preserve_current_turn_time: bool,
) -> BoundDatetimeClearEvidence:
    """Semantic evidence that prior bound datetime is no longer authorized."""
    return BoundDatetimeClearEvidence(
        cleared=True,
        reason_code="BOUND_DATETIME_CLEARED",
        preserve_current_turn_time=preserve_current_turn_time,
    )


def _hydrate_working_slots_from_session(
    working_turn: WorkingTurn,
    session_state: Optional[Dict[str, Any]],
) -> None:
    """Ensure working-turn slots include durable session booking facts before reject.

    Stage 02 merge normally already did this. When the working payload lacks
    slots (e.g. unit callers), copy session slots onto the working turn only —
    do not rebuild the payload envelope.
    """
    payload = working_turn.payload
    current = dict(
        working_turn.effective_collected_slots
        or payload.get("_effective_collected_slots")
        or payload.get("slots")
        or {}
    )
    if current:
        payload.setdefault("slots", current)
        payload.setdefault("_effective_collected_slots", current)
        if not working_turn.effective_collected_slots:
            working_turn.effective_collected_slots = current
        return
    if not isinstance(session_state, dict):
        return
    session_slots = session_state.get("slots")
    if not isinstance(session_slots, dict) or not session_slots:
        return
    slots = dict(session_slots)
    payload["slots"] = slots
    payload["_effective_collected_slots"] = slots
    working_turn.effective_collected_slots = slots
    # Carry availability presentation artifacts already on session so reject
    # does not drop them (REJECT_CONFIRMATION does not clear availability).
    for key in _SESSION_AVAILABILITY_KEYS:
        if session_state.get(key) is not None and payload.get(key) is None:
            payload[key] = session_state[key]
    if (
        session_state.get("resolved_datetime_range") is not None
        and payload.get("resolved_datetime_range") is None
    ):
        payload["resolved_datetime_range"] = session_state.get(
            "resolved_datetime_range"
        )


def resolve_confirmation(

    *,

    attached_request: AttachedRequest,

    slot_state: SlotTurnState,

    working_turn: WorkingTurn,

    availability: AvailabilityDecision,

    session_state: Optional[Dict[str, Any]],

    gate_booking_intent: str,

    user_id: str,

) -> ConfirmationDecision:

    _ = user_id

    payload = working_turn.payload

    availability_reshow = False

    slots_adjusted = False

    availability_invalidation = None

    bound_datetime_clear = None

    gate_action = attached_request.gate_action

    turn_operation = attached_request.turn_operation

    confirm_booking_continuation = attached_request.confirm_booking_continuation



    confirmation_state = get_confirmation_state(payload)

    if confirmation_state is None and session_state:

        confirmation_state = get_confirmation_state(session_state)



    # Gate YES / continuation: user acceptance is turn evidence only
    # (user_confirmation_satisfied via confirm_booking_continuation). Durable
    # confirmation_state stays pending until commit consume or invalidation —
    # never write confirmation_state="confirmed" onto working/session state.

    if gate_action == ConfirmationGateTurn.NO and gate_booking_intent:
        # Semantic rejection only: invalidate the working turn, emit evidence.
        # Stage 04 recomputes missing slots; Decision selects outcome; Stage 09
        # renders wording. Do not rebuild payload or call renderers here.
        _hydrate_working_slots_from_session(working_turn, session_state)
        apply_invalidation(
            payload,
            InvalidationTrigger.REJECT_CONFIRMATION,
            reason="reject",
        )
        slots = dict(payload.get("slots") or {})
        payload["slots"] = slots
        payload["_effective_collected_slots"] = slots
        working_turn.effective_collected_slots = slots
        # Persist marker: keep post-reject temporal retention under planning ownership.
        payload["_booking_confirmation_rejected"] = True
        return ConfirmationDecision(
            confirmation_state=None,
            reject_evidence=ConfirmationRejectEvidence(
                rejected=True,
                intent_name=gate_booking_intent,
                reason_code="REJECT_CONFIRMATION",
            ),
            slots_adjusted=True,
        )

    availability_op = is_availability_turn_operation(turn_operation)

    preserve_current_turn_time = bool(payload.get("_current_turn_has_time"))



    if availability_op:

        # Availability operations always drop a prior bound selection. Keep only

        # time evidence supplied by the current turn.

        supersede_pending_with_search = (
            gate_action == ConfirmationGateTurn.ANOTHER_REQUEST
            and confirmation_state == "pending"
        )

        if gate_action == ConfirmationGateTurn.ANOTHER_REQUEST:

            consume_confirmation_state(payload, reason="confirmation_superseded")

            if isinstance(session_state, dict):

                consume_confirmation_state(

                    session_state, reason="confirmation_superseded"

                )

            confirmation_state = None

        bound_datetime_clear = _bound_datetime_clear_evidence(
            preserve_current_turn_time=preserve_current_turn_time,
        )

        slots_adjusted = True

        if supersede_pending_with_search:

            # Typed evidence only — Decision applies trust invalidation; no payload flag.

            availability_invalidation = AvailabilityInvalidationEvidence(
                invalidated=True,
                reason_code="AVAILABILITY_SUPERSEDES_PENDING_CONFIRMATION",
            )

        else:

            _preserve_session_availability_cache(payload, session_state)

            # Date/service criteria revisions invalidate availability trust in
            # Stage 03. Do not reshow / browse the prior cache when this turn
            # introduced a different explicit search date (or equivalent).
            if (
                availability.availability_ready
                and not payload.get("_revision_invalidated_availability")
            ):

                availability_reshow = True

        same_turn_time_rebind = False

    elif gate_action == ConfirmationGateTurn.ANOTHER_REQUEST:

        # Confirmation authorizes a specific bound selection. Genuine supersession
        # (revision, availability change, durable workflow progress) consumes that
        # authorization. Unrecognized no-evidence turns are not supersession —
        # keep pending so recovery can re-ask.

        if _non_superseding_unrecognized_pending(payload, confirmation_state):
            same_turn_time_rebind = False
        else:

            consume_confirmation_state(payload, reason="confirmation_superseded")

            if isinstance(session_state, dict):

                consume_confirmation_state(

                    session_state, reason="confirmation_superseded"

                )

            confirmation_state = None

            same_turn_time_rebind = _same_turn_time_rebind(working_turn, turn_operation)

            if same_turn_time_rebind:

                slots_adjusted = False

            else:

                bound_datetime_clear = _bound_datetime_clear_evidence(
                    preserve_current_turn_time=False,
                )

                slots_adjusted = True

    else:

        same_turn_time_rebind = False



    # Re-enter pending when commit-ready, including same-turn time corrections

    # that superseded the previous confirmation authorization.

    # Availability operations never enter confirmation presentation here.

    if (

        (

            gate_action != ConfirmationGateTurn.ANOTHER_REQUEST

            or same_turn_time_rebind

        )

        and not availability_reshow

        and not availability_op

    ):

        confirmation_state = _maybe_enter_booking_confirmation_pending(

            slot_state.intent_name,

            payload,

            missing_slots=slot_state.missing_slots,

            needs_clarification=slot_state.needs_clarification,

            availability_ready=availability.availability_ready,

            confirmation_state=confirmation_state,

            session_state=session_state,

        )



    # Stage 01 sets confirm_booking_continuation on gate YES; treat YES itself
    # as acceptance so unit callers that pass gate_action alone stay correct.
    acceptance = bool(
        confirm_booking_continuation
        or gate_action == ConfirmationGateTurn.YES
    )
    user_confirmation_satisfied = derive_user_confirmation_satisfied(
        confirmation_state,
        confirm_booking_continuation=acceptance,
    )

    awaiting = bool(

        confirmation_state == "pending" and not user_confirmation_satisfied

    )

    return ConfirmationDecision(

        confirmation_state=confirmation_state,

        user_confirmation_satisfied=user_confirmation_satisfied,

        awaiting_user_confirmation=awaiting,

        availability_reshow=availability_reshow,

        slots_adjusted=slots_adjusted,

        availability_invalidation=availability_invalidation,

        bound_datetime_clear=bound_datetime_clear,

    )


