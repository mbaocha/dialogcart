"""Planning mutation coordinator — sole apply boundary for working-session writes.

Architectural roles
-------------------
- **Merge** computes revision / bind / ambiguity facts and *requests* mutations.
- **Invalidation** declares trigger → clear-set rules (``InvalidationTrigger``).
- **This module** applies mutations onto the working turn / merged payload.
- Later stages (04–09) consume the mutated working state.

Callers must not invoke ``apply_invalidation`` / ``clear_booking_state`` /
``apply_bound_datetime_clear`` directly for planning turns. Use the helpers
below instead.

Does not change Stage 06 evidence emission or Stage 08 decision ownership.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from core.session.confirmation_gate import consume_confirmation_state
from core.session.invalidation import (
    InvalidationTrigger,
    apply_bound_datetime_clear,
    apply_confirmation_bound_clear_evidence,
    apply_invalidation,
    hydrate_working_slots_from_session,
    sync_working_slot_projections,
    clear_booking_state,
)


def apply_authoritative_entity_resolution_mutations(
    working_turn: Any,
) -> None:
    """Apply NLU entity grounding evidence to the working planning state.

    Omitted entities produce no evidence entry and therefore no mutation.
    Resolved canonical values replace compatibility projections. Unresolved or
    ambiguous mentions block a stale value and carry clarification evidence.
    """
    from core.adapters.nlu.entity_resolution_contract import EntityResolutionState
    from core.adapters.nlu.entity_schema_builder import (
        field_availability_criteria,
        field_for_planning_slot,
        promotable_slot_keys_from_entity_schema,
    )

    evidence = getattr(working_turn, "entity_resolution_evidence", None)
    if not isinstance(evidence, dict):
        return
    payload = working_turn.payload
    slots = dict(payload.get("slots") or {})
    facts = dict(payload.get("facts") or {})
    raw_turn_slots = dict(payload.get("_raw_luma_slots") or {})
    schema = payload.get("_entity_schema")
    promotable_slot_keys = promotable_slot_keys_from_entity_schema(schema)
    blocked = []
    pending = []

    for item in evidence.values():
        slot_key = item.slot_key
        # Request-scoped profile evidence is consumed by its owning projection
        # path. It must remain in entity_resolution_evidence without becoming a
        # planning mutation or durable booking slot.
        if slot_key not in promotable_slot_keys:
            continue
        try:
            from core.tracing.decision_trace import decide as trace_decide
            from core.tracing.reason_codes import (
                ENTITY_RESOLUTION_APPLIED,
                ENTITY_RESOLUTION_BLOCKED,
            )

            trace_decide(
                "entity_resolution_application",
                subsystem="planning",
                winner=item.resolution.value,
                reason_code=(
                    ENTITY_RESOLUTION_APPLIED
                    if item.resolution == EntityResolutionState.RESOLVED
                    else ENTITY_RESOLUTION_BLOCKED
                ),
                reason_text=(
                    f"NLU {item.resolution.value} evidence controls planning slot {slot_key}."
                ),
                node_id=f"decision.planner.entity_resolution.{item.entity_name}",
                inputs_evaluated={"entity_name": item.entity_name, "slot_key": slot_key},
            )
        except Exception:
            pass
        if item.resolution == EntityResolutionState.RESOLVED:
            slots[slot_key] = item.value
            facts[slot_key] = item.value
            raw_turn_slots[slot_key] = item.value
            continue

        payload["slots"] = dict(slots)
        payload["facts"] = dict(facts)
        field = field_for_planning_slot(schema, slot_key)
        affects_availability = bool(field and field_availability_criteria(field))
        clear_booking_state(
            payload,
            clear_time=False,
            clear_availability=affects_availability,
            clear_service=slot_key == "service_id",
            clear_extra_slots={slot_key},
            clear_confirmation=True,
            reason=f"entity_resolution_{item.resolution.value.lower()}",
        )
        slots = dict(payload.get("slots") or slots)
        facts.pop(slot_key, None)
        raw_turn_slots.pop(slot_key, None)
        blocked.append(slot_key)
        pending_item = {
            "entity_name": item.entity_name,
            "slot_key": slot_key,
            "resolution": item.resolution.value,
        }
        if item.resolution == EntityResolutionState.AMBIGUOUS:
            pending_item["candidate_values"] = list(item.candidate_values)
            if slot_key == "service_id":
                # Existing rendering/context transport; candidates remain the
                # canonical NLU values and are never recomputed in Core.
                payload["service_candidates"] = list(item.candidate_values)
        pending.append(pending_item)

    payload["facts"] = facts
    payload["_raw_luma_slots"] = raw_turn_slots
    if blocked:
        payload["_blocked_entity_slots"] = blocked
        payload["_pending_entity_resolutions"] = pending
        payload["needs_clarification"] = True
        first = pending[0]
        payload["clarification_reason"] = (
            "ENTITY_AMBIGUOUS"
            if first["resolution"] == EntityResolutionState.AMBIGUOUS.value
            else "ENTITY_UNRESOLVED"
        )
        payload["clarification_data"] = {"entity_resolution": first}
        payload["issues"] = {"entity_resolutions": pending}
    slots = sync_working_slot_projections(payload, slots)
    working_turn.effective_collected_slots = slots


def _clock_hhmm(value: Any) -> Optional[str]:
    """Normalize a clock-like value to HH:MM for reject restatement checks."""
    if value is None:
        return None
    text_value = str(value).strip()
    if not text_value:
        return None
    if "T" in text_value:
        text_value = text_value.split("T", 1)[1]
    text_value = text_value.split("+", 1)[0].split("Z", 1)[0]
    parts = text_value.split(":")
    if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
        return f"{int(parts[0]):02d}:{int(parts[1]):02d}"
    return None


def _clear_rejected_restated_time(state: Dict[str, Any]) -> None:
    """Drop echoed clock mirrors after confirmation reject without a new time."""
    state.pop("time_constraint", None)
    facts = state.get("facts")
    if isinstance(facts, dict):
        facts = dict(facts)
        for key in (
            "times",
            "time_proposal",
            "time_constraint",
            "resolved_datetime_range",
        ):
            facts.pop(key, None)
        fact_slots = facts.get("slots")
        if isinstance(fact_slots, dict):
            fact_slots = dict(fact_slots)
            fact_slots.pop("time", None)
            fact_slots.pop("has_datetime", None)
            fact_slots.pop("datetime_range", None)
            facts["slots"] = fact_slots
        state["facts"] = facts
    temporal = state.get("temporal")
    if isinstance(temporal, dict):
        temporal = dict(temporal)
        for key in (
            "start_time",
            "end_time",
            "start_time_expression",
            "end_time_expression",
        ):
            temporal[key] = None
        state["temporal"] = temporal


def apply_empty_availability_recovery_acceptance(
    working_turn: Any,
    *,
    luma_response: Dict[str, Any],
    session_state: Optional[Dict[str, Any]],
) -> bool:
    """Accept a structured another-date offer without repeating stale criteria."""
    from core.planning.recovery_actions import accepts_empty_availability_recovery

    if not accepts_empty_availability_recovery(luma_response, session_state):
        return False
    clear_booking_state(
        working_turn.payload,
        clear_time=True,
        clear_date=True,
        clear_availability=True,
        clear_confirmation=False,
        clear_extra_state={"date_proposal", "time_proposal"},
        reason="accepted_empty_availability_recovery",
    )
    working_turn.effective_collected_slots = dict(
        working_turn.payload.get("slots") or {}
    )
    working_turn.payload["_accepted_empty_availability_recovery"] = True
    return True


def apply_assistant_proposal_promotion(working_turn: Any, relationship: Any) -> bool:
    """Promote a bound accepted proposal through the planning working state."""
    if relationship is None or getattr(relationship, "resolution", None) != "BOUND":
        return False
    if getattr(relationship, "response_act", None) != "CONFIRM_ACTION":
        return False
    proposal = getattr(relationship, "proposal", None)
    if not isinstance(proposal, dict):
        return False
    slot_key = proposal.get("slot_key")
    value = proposal.get("canonical_id")
    if not isinstance(slot_key, str) or not slot_key or value is None:
        return False

    payload = working_turn.payload
    explicit = payload.get("_raw_luma_slots") or {}
    if isinstance(explicit, dict) and explicit.get(slot_key) is not None:
        return False
    slots = dict(payload.get("slots") or {})
    slots[slot_key] = value
    payload["slots"] = slots
    facts = dict(payload.get("facts") or {})
    facts[slot_key] = value
    payload["facts"] = facts
    working_turn.effective_collected_slots = {
        **working_turn.effective_collected_slots,
        slot_key: value,
    }
    payload["_assistant_proposal_updates"] = [{
        "proposal_id": str(proposal.get("proposal_id")),
        "status": "CONSUMED",
    }]
    payload["_assistant_proposal_promoted"] = True
    return True


def apply_assistant_proposal_rejection(working_turn: Any, relationship: Any) -> bool:
    """Project rejection evidence without promoting the proposed entity."""
    if (
        relationship is None
        or getattr(relationship, "resolution", None) != "BOUND"
        or getattr(relationship, "response_act", None) != "REJECT_ACTION"
    ):
        return False
    proposal = getattr(relationship, "proposal", None)
    if not isinstance(proposal, dict) or not proposal.get("proposal_id"):
        return False
    working_turn.payload["_assistant_proposal_updates"] = [{
        "proposal_id": str(proposal["proposal_id"]),
        "status": "REJECTED",
    }]
    return True


def apply_trigger(
    state: Dict[str, Any],
    trigger: InvalidationTrigger,
    *,
    reason: str = "",
    **kwargs: Any,
) -> Dict[str, Any]:
    """Apply a registered invalidation trigger onto working planning state.

    Thin coordinator entry over the invalidation registry. Merge and Stage 03
    must call this instead of ``apply_invalidation`` directly.
    """
    return apply_invalidation(state, trigger, reason=reason, **kwargs)


def _normalize_date_value(value: Any) -> Optional[str]:
    if not value or not isinstance(value, str):
        return None
    return value.split("T")[0].split(" ")[0]


def apply_booking_revision_mutations(
    working_turn: Any,
    revision: Any,
    *,
    reason: str = "planning_revision",
) -> None:
    """Apply field-aware booking-revision invalidation onto the working turn.

    Clears stale durable fields via ``BOOKING_REVISION``, then restores
    current-turn replacement values. Identical semantics to the former
    Stage 03 inline mutator.
    """
    payload = working_turn.payload
    # Prefer full turn slots so optional search-criteria keys (e.g. staff_id)
    # survive restore after invalidation. ``_effective_collected_slots`` only
    # retains required planning keys and would drop optional criteria.
    current_slots = dict(payload.get("slots") or {})
    for src in (
        working_turn.effective_collected_slots,
        payload.get("_effective_collected_slots"),
    ):
        if not isinstance(src, dict):
            continue
        for key, value in src.items():
            if key not in current_slots and value is not None:
                current_slots[key] = value

    apply_trigger(
        payload,
        InvalidationTrigger.BOOKING_REVISION,
        revision=revision,
        reason=reason,
    )
    invalidated_slots = dict(payload.get("slots") or {})

    if revision.service:
        for key in ("service_id", "_canonical_service_id"):
            if current_slots.get(key) is not None:
                invalidated_slots[key] = current_slots[key]

    if revision.criteria:
        for change in revision.changes:
            if change.field in ("service", "date", "time"):
                continue
            value = current_slots.get(change.field)
            if value is None:
                value = change.to_value
            if value is not None:
                invalidated_slots[change.field] = value

    if revision.date:
        # New date often arrives only as date_proposal. Restore durable date
        # keys only when they already carry the revised value — never the
        # pre-revision session date that additive merge preserved.
        new_date = None
        for change in revision.changes:
            if change.field == "date":
                new_date = _normalize_date_value(change.to_value)
                break
        for key in ("date", "date_range", "start_date", "end_date"):
            value = current_slots.get(key)
            if value is None:
                continue
            if new_date and _normalize_date_value(str(value)) == new_date:
                invalidated_slots[key] = value

    slots = sync_working_slot_projections(payload, invalidated_slots)
    working_turn.effective_collected_slots = slots

    if revision.invalidates_availability:
        # Genuine mid-flow replacements only (first acquisition is not a
        # revision). Stale time proposals would rebind against the old presented
        # offers and undo availability invalidation.
        if not payload.get("_current_turn_has_time"):
            payload.pop("time_constraint", None)
            payload.pop("date_constraint", None)
            payload.pop("time_proposal", None)
            temporal = payload.get("temporal")
            if isinstance(temporal, dict):
                temporal = dict(temporal)
                temporal["start_time"] = None
                temporal["end_time"] = None
                temporal["start_time_expression"] = None
                temporal["end_time_expression"] = None
                payload["temporal"] = temporal
        # Service/staff criteria revision must keep the active search date_proposal
        # so the new criteria are searched on the same day. Date revisions arrive
        # as a current-turn proposal and replace the prior value without popping.
        payload["_revision_invalidated_availability"] = True
        payload.pop("resolved_datetime_range", None)


def apply_confirmation_planning_mutations(
    working_turn: Any,
    confirmation: Any,
    *,
    session_state: Optional[Dict[str, Any]] = None,
) -> None:
    """Apply Stage 06 confirmation evidence onto the working turn.

    Applies, in order:
    1. reject → REJECT_CONFIRMATION invalidation + persist marker
    2. consume / supersede → consume_confirmation_state
    3. bound-datetime clear

    Mutates the working turn payload. When ``session_state`` is the live
    request-scoped working session (distinct from the payload), the same
    lifecycle mutations are mirrored onto it so fact resolution and persist
    cannot resurrect consumed confirmation or cleared bound time from the
    pre-mutation session snapshot. The immutable request-start deepcopy
    (``previous_session``) is never passed here.

    Stage 06 must emit evidence only; this coordinator is the sole mutator.
    """
    payload = working_turn.payload
    lifecycle = getattr(confirmation, "lifecycle_evidence", None)
    reject = getattr(confirmation, "reject_evidence", None)
    consume = getattr(confirmation, "consume_evidence", None)

    is_reject = bool(
        (reject is not None and getattr(reject, "rejected", False))
        or (
            lifecycle is not None
            and getattr(lifecycle, "action", None) == "reject"
        )
    )
    reject_reason = str(
        getattr(lifecycle, "reason", None)
        or getattr(reject, "reason_code", None)
        or "reject"
    )
    keep_turn_proposal = False
    if is_reject:
        hydrate_working_slots_from_session(working_turn, session_state)
        # Bare reject / NLU echo of the rejected clock must not restore it.
        # Reject-plus-new-time keeps current-turn proposal so Stage 04/08 rebind.
        # Prefer durable session time: merge may already have rebound payload slots.
        prior_time = None
        if isinstance(session_state, dict):
            session_slots = session_state.get("slots")
            if isinstance(session_slots, dict):
                prior_time = session_slots.get("time")
            if prior_time is None:
                prior_proposal = session_state.get("time_proposal")
                if isinstance(prior_proposal, dict):
                    prior_time = prior_proposal.get("value")
        if prior_time is None:
            prior_slots = (
                payload.get("slots") if isinstance(payload.get("slots"), dict) else {}
            )
            prior_time = prior_slots.get("time") if isinstance(prior_slots, dict) else None
        turn_clock = _clock_hhmm(payload.get("_current_turn_time"))
        prior_clock = _clock_hhmm(prior_time)
        keep_turn_proposal = bool(
            payload.get("_current_turn_has_time")
            and turn_clock
            and turn_clock != prior_clock
        )
        if not keep_turn_proposal:
            payload["_current_turn_has_time"] = False
            payload.pop("_current_turn_time", None)
        saved_turn_proposal = (
            dict(payload["time_proposal"])
            if keep_turn_proposal and isinstance(payload.get("time_proposal"), dict)
            else None
        )
        apply_trigger(
            payload,
            InvalidationTrigger.REJECT_CONFIRMATION,
            reason=reject_reason,
        )
        if saved_turn_proposal is not None:
            payload["time_proposal"] = saved_turn_proposal
        elif not keep_turn_proposal:
            _clear_rejected_restated_time(payload)
        slots = sync_working_slot_projections(
            payload, dict(payload.get("slots") or {})
        )
        working_turn.effective_collected_slots = slots
        payload["_booking_confirmation_rejected"] = True
    else:
        should_consume = bool(
            (consume is not None and getattr(consume, "consume", False))
            or (
                lifecycle is not None
                and getattr(lifecycle, "action", None) in ("supersede", "consume")
            )
        )
        if should_consume:
            reason = (
                getattr(consume, "reason", None)
                or getattr(lifecycle, "reason", None)
                or "confirmation_superseded"
            )
            consume_confirmation_state(payload, reason=str(reason))

    apply_confirmation_bound_clear_evidence(working_turn, confirmation)

    # Mirror onto live working session when it is a separate object from the
    # planning payload (typical HTTP path: deepcopy previous_session, mutate
    # working_session). Shared-object cases are already covered above.
    if isinstance(session_state, dict) and session_state is not payload:
        if is_reject:
            apply_trigger(
                session_state,
                InvalidationTrigger.REJECT_CONFIRMATION,
                reason=reject_reason,
            )
            session_state["_booking_confirmation_rejected"] = True
            if not keep_turn_proposal:
                session_state["_current_turn_has_time"] = False
                session_state.pop("_current_turn_time", None)
                _clear_rejected_restated_time(session_state)
        elif (
            (consume is not None and getattr(consume, "consume", False))
            or (
                lifecycle is not None
                and getattr(lifecycle, "action", None) in ("supersede", "consume")
            )
        ):
            reason = (
                getattr(consume, "reason", None)
                or getattr(lifecycle, "reason", None)
                or "confirmation_superseded"
            )
            consume_confirmation_state(session_state, reason=str(reason))

        evidence = getattr(confirmation, "bound_datetime_clear", None)
        if evidence is not None and getattr(evidence, "cleared", False):
            apply_bound_datetime_clear(
                session_state,
                preserve_current_turn_time=bool(
                    getattr(evidence, "preserve_current_turn_time", False)
                ),
                reason=str(
                    getattr(evidence, "reason_code", None) or "bound_datetime_cleared"
                ),
            )
