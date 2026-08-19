"""Stage 06 — confirmation policy."""



from __future__ import annotations



import logging

from typing import Any, Dict, Optional



from core.planning.booking_revision import has_committed_create_appointment

from core.planning.facts import derive_user_confirmation_satisfied

from core.planning.pipeline.decision import (
    AvailabilityInvalidationEvidence,
    BoundDatetimeClearEvidence,
    ConfirmationConsumeEvidence,
    ConfirmationLifecycleEvidence,
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

    get_confirmation_state,

    set_confirmation_state,

)



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

    customer_name_prerequisite: Any,

) -> Optional[str]:

    if intent_name != "CREATE_APPOINTMENT":

        return confirmation_state



    effective_slots = payload.get("_effective_collected_slots") or payload.get("slots", {})

    session_slots = (

        session_state.get("slots") if isinstance(session_state, dict) else None

    )

    from core.session.booking_lifecycle import BookingLifecycle, derive_booking_lifecycle

    if (
        derive_booking_lifecycle(session_state) == BookingLifecycle.COMMITTED
        or has_committed_create_appointment(effective_slots)
        or has_committed_create_appointment(session_slots, session_state=session_state)
    ):
        return None

    if confirmation_state is not None:

        return confirmation_state

    if missing_slots or needs_clarification or not availability_ready:

        return confirmation_state

    if not has_bound_booking_datetime(effective_slots, session_state, payload):

        return confirmation_state

    if not customer_name_prerequisite.satisfied:

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
    from core.workflows.availability.presentation import (
        apply_availability_artifacts,
        availability_cache_from_session,
        availability_fingerprint_from_session,
        availability_pagination_from_session,
        presented_availability_from_session,
    )

    payload_availability = payload.get("availability")
    if isinstance(payload_availability, dict) and (
        payload_availability.get("fingerprint") is not None
        or (
            isinstance(payload_availability.get("cache"), dict)
            and payload_availability["cache"].get("search_result") is not None
        )
        or (
            isinstance(payload_availability.get("presentation"), dict)
            and payload_availability["presentation"].get("presented") is not None
        )
    ):
        return

    apply_availability_artifacts(
        payload,
        fingerprint=availability_fingerprint_from_session(session_state),
        search_result=availability_cache_from_session(session_state),
        presented=presented_availability_from_session(session_state),
        presentation=availability_pagination_from_session(session_state),
    )





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


def _supersede_lifecycle_evidence(
    *,
    reason: str = "confirmation_superseded",
) -> tuple:
    """Build paired lifecycle + consume evidence for pending supersession."""
    lifecycle = ConfirmationLifecycleEvidence(
        action="supersede",
        reason=reason,
    )
    consume = ConfirmationConsumeEvidence(
        consume=True,
        reason=reason,
    )
    return lifecycle, consume


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

    from core.customer_identification import customer_name_confirmation_prerequisite

    customer_name_prerequisite = customer_name_confirmation_prerequisite(
        session_state
    )

    availability_reshow = False

    slots_adjusted = False

    availability_invalidation = None

    bound_datetime_clear = None

    lifecycle_evidence = None

    consume_evidence = None

    gate_action = attached_request.gate_action

    turn_operation = attached_request.turn_operation

    confirm_booking_continuation = attached_request.confirm_booking_continuation



    confirmation_state = get_confirmation_state(payload)

    if confirmation_state is None and session_state:

        confirmation_state = get_confirmation_state(session_state)

    if (
        slot_state.intent_name == "CREATE_APPOINTMENT"
        and not customer_name_prerequisite.satisfied
        and confirmation_state == "pending"
    ):
        lifecycle_evidence, consume_evidence = _supersede_lifecycle_evidence(
            reason="customer_name_prerequisite_missing"
        )
        confirmation_state = None



    # Gate YES / continuation: user acceptance is turn evidence only
    # (user_confirmation_satisfied via confirm_booking_continuation). Durable
    # confirmation_state stays pending until commit consume or invalidation —
    # never write confirmation_state="confirmed" onto working/session state.

    if gate_action == ConfirmationGateTurn.NO and gate_booking_intent:
        # Semantic rejection only — planning mutation boundary applies
        # REJECT_CONFIRMATION. Stage 04 recomputes missing slots; Decision
        # selects outcome; Stage 09 renders wording.
        return ConfirmationDecision(
            confirmation_state=None,
            reject_evidence=ConfirmationRejectEvidence(
                rejected=True,
                intent_name=gate_booking_intent,
                reason_code="REJECT_CONFIRMATION",
            ),
            lifecycle_evidence=ConfirmationLifecycleEvidence(
                action="reject",
                reason="reject",
                intent_name=gate_booking_intent,
                reason_code="REJECT_CONFIRMATION",
            ),
            slots_adjusted=True,
            customer_name_prerequisite=customer_name_prerequisite,
        )

    availability_op = is_availability_turn_operation(turn_operation)

    # Preserve only an explicit current-turn time value — not a stale flag left
    # after merge rebound of the prior confirmation selection.
    preserve_current_turn_time = bool(
        payload.get("_current_turn_has_time") and payload.get("_current_turn_time")
    )

    def _normalized_clock(value: Any) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        # Accept HH:MM or HH:MM:SS / ISO fragments.
        if "T" in text:
            text = text.split("T", 1)[1]
        text = text.split("+", 1)[0].split("Z", 1)[0]
        parts = text.split(":")
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            return f"{int(parts[0]):02d}:{int(parts[1]):02d}"
        return text

    def _prior_bound_clock() -> Optional[str]:
        slots = payload.get("slots") if isinstance(payload.get("slots"), dict) else {}
        prior = slots.get("time")
        if prior is None and isinstance(session_state, dict):
            session_slots = session_state.get("slots")
            if isinstance(session_slots, dict):
                prior = session_slots.get("time")
        if prior is None:
            resolved = payload.get("resolved_datetime_range")
            if not isinstance(resolved, dict) and isinstance(session_state, dict):
                resolved = session_state.get("resolved_datetime_range")
            if isinstance(resolved, dict):
                prior = resolved.get("start")
        return _normalized_clock(prior)



    if availability_op:

        # Availability operations always drop a prior bound selection. Keep only

        # time evidence supplied by the current turn.

        supersede_pending_with_search = (
            gate_action == ConfirmationGateTurn.ANOTHER_REQUEST
            and confirmation_state == "pending"
        )

        if (
            supersede_pending_with_search
            and preserve_current_turn_time
            and _normalized_clock(payload.get("_current_turn_time"))
            == _prior_bound_clock()
        ):
            # NLU may echo the prior bound clock into facts/temporal on a bare
            # availability reshow; that is not a new time selection.
            preserve_current_turn_time = False

        if gate_action == ConfirmationGateTurn.ANOTHER_REQUEST:

            lifecycle_evidence, consume_evidence = _supersede_lifecycle_evidence()

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

            # Interpretive clear; emit consume only if pending is not re-entered
            # below (re-entry would be wiped if consume applied after enter).
            confirmation_state = None

            same_turn_time_rebind = _same_turn_time_rebind(working_turn, turn_operation)

            if same_turn_time_rebind:

                slots_adjusted = False

            else:

                lifecycle_evidence, consume_evidence = _supersede_lifecycle_evidence()

                if (
                    preserve_current_turn_time
                    and _normalized_clock(payload.get("_current_turn_time"))
                    == _prior_bound_clock()
                ):
                    preserve_current_turn_time = False

                bound_datetime_clear = _bound_datetime_clear_evidence(
                    preserve_current_turn_time=preserve_current_turn_time,
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

            customer_name_prerequisite=customer_name_prerequisite,

        )

        # Same-turn rebind re-entered pending: do not request consume (would
        # clear the newly written pending). If enter failed, request consume so
        # stale pending is cleared on the working turn.
        if same_turn_time_rebind and confirmation_state != "pending":
            lifecycle_evidence, consume_evidence = _supersede_lifecycle_evidence()



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

        reject_evidence=None,

        lifecycle_evidence=lifecycle_evidence,

        consume_evidence=consume_evidence,

        availability_reshow=availability_reshow,

        slots_adjusted=slots_adjusted,

        availability_invalidation=availability_invalidation,

        bound_datetime_clear=bound_datetime_clear,

        customer_name_prerequisite=customer_name_prerequisite,

    )


