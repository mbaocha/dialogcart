"""Central registry for explicit slot/state invalidation during session merge.

Merge is additive by default; durable slots and workflow state are removed only
through registered invalidation triggers. This module is the single entry point
for those drops (persist gates and intent filters live elsewhere).
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any, Callable, Dict, Optional, Set, Tuple

from core.planning.booking_revision import BookingRevision
from core.session.confirmation_gate import set_confirmation_state

logger = logging.getLogger(__name__)

_AVAILABILITY_STATE_KEYS = frozenset(
    {
        "presented_availability",
        "availability_fingerprint",
        "last_execution_result",
        "availability_presentation",
    }
)


def clear_booking_state(
    state: Optional[Dict[str, Any]],
    *,
    clear_time: bool = True,
    clear_date: bool = False,
    clear_availability: bool = False,
    clear_service: bool = False,
    reason: str = "",
) -> Dict[str, Any]:
    """Apply planner-selected booking invalidation to a processing state."""
    if not isinstance(state, dict):
        return {}

    set_confirmation_state(state, None)

    if clear_time or clear_date or clear_service:
        slots = dict(state.get("slots") or {})
        if clear_time:
            slots.pop("time", None)
            slots.pop("has_datetime", None)
            slots.pop("datetime_range", None)
        if clear_date:
            slots.pop("date", None)
            slots.pop("date_range", None)
        if clear_service:
            slots.pop("service_id", None)
            slots.pop("_canonical_service_id", None)
        state["slots"] = slots
        state.pop("resolved_datetime_range", None)

        facts = state.get("facts")
        if isinstance(facts, dict):
            facts = dict(facts)
            facts.pop("resolved_datetime_range", None)
            fact_slots = facts.get("slots")
            if isinstance(fact_slots, dict):
                fact_slots = dict(fact_slots)
                if clear_time:
                    fact_slots.pop("time", None)
                    fact_slots.pop("has_datetime", None)
                    fact_slots.pop("datetime_range", None)
                if clear_date:
                    fact_slots.pop("date", None)
                    fact_slots.pop("date_range", None)
                if clear_service:
                    fact_slots.pop("service_id", None)
                    fact_slots.pop("_canonical_service_id", None)
                facts["slots"] = fact_slots
            state["facts"] = facts

    if clear_availability:
        for key in _AVAILABILITY_STATE_KEYS:
            state.pop(key, None)

    if reason:
        logger.debug(
            "[BOOKING_INVALIDATION] reason=%s clear_time=%s clear_date=%s "
            "clear_availability=%s clear_service=%s",
            reason,
            clear_time,
            clear_date,
            clear_availability,
            clear_service,
        )
    return state


class InvalidationTrigger(str, Enum):
    """Explicit invalidation events (one trigger per call site)."""

    REJECT_CONFIRMATION = "reject_confirmation"
    TIME_REBOUND = "time_rebound"
    UNBOUND_PROPOSAL_WHILE_PENDING = "unbound_proposal_while_pending"
    BOOKING_REVISION = "booking_revision"
    AMBIGUOUS_SERVICE = "ambiguous_service"
    NEW_BOOKING_REQUEST = "new_booking_request"


# Declarative invalidation rules.
# - confirmation rules: clear_slots / clear_state interpreted by _apply_confirmation_rule
# - handler rules: custom merge-specific invalidation (see _CUSTOM_HANDLERS)
INVALIDATION_RULES: Dict[InvalidationTrigger, Dict[str, Any]] = {
    InvalidationTrigger.REJECT_CONFIRMATION: {
        "clear_confirmation": True,
        "clear_slots": frozenset({"time"}),
        "clear_state": frozenset(),
        "default_reason": "reject",
    },
    InvalidationTrigger.TIME_REBOUND: {
        "clear_confirmation": True,
        "clear_slots": frozenset(),
        "clear_state": frozenset(),
        "default_reason": "rebound_selection",
    },
    InvalidationTrigger.UNBOUND_PROPOSAL_WHILE_PENDING: {
        "clear_confirmation": True,
        "clear_slots": frozenset(),
        "clear_state": frozenset(),
        "default_reason": "unbound_proposal_revision",
    },
    InvalidationTrigger.BOOKING_REVISION: {
        "clear_confirmation": True,
        "revision_derived": True,
        "default_reason": "booking_revision",
    },
    InvalidationTrigger.AMBIGUOUS_SERVICE: {
        "handler": "ambiguous_service",
    },
    InvalidationTrigger.NEW_BOOKING_REQUEST: {
        "handler": "new_booking_request",
    },
}


def apply_invalidation(
    state: Optional[Dict[str, Any]],
    trigger: InvalidationTrigger,
    *,
    reason: str = "",
    revision: Optional[BookingRevision] = None,
    merged_slots: Optional[Dict[str, Any]] = None,
    session_state: Optional[Dict[str, Any]] = None,
    raw_service_id_from_session: Any = None,
    current_candidates: Optional[Any] = None,
    luma_slots: Optional[Dict[str, Any]] = None,
    merged_intent_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Apply a registered invalidation rule. Mutates ``state`` in place."""
    if not isinstance(state, dict):
        return {}

    rule = INVALIDATION_RULES.get(trigger)
    if rule is None:
        logger.warning("Unknown invalidation trigger %r — no-op", trigger)
        return state

    handler_name = rule.get("handler")
    if handler_name:
        handler = _CUSTOM_HANDLERS.get(handler_name)
        if handler is None:
            logger.warning(
                "Unknown invalidation handler %r — no-op", handler_name)
            return state
        handler(
            state,
            merged_slots=merged_slots,
            session_state=session_state,
            raw_service_id_from_session=raw_service_id_from_session,
            current_candidates=current_candidates,
            luma_slots=luma_slots,
            merged_intent_name=merged_intent_name,
        )
        return state

    effective_reason = reason or rule.get("default_reason", "")

    if rule.get("revision_derived"):
        if revision is None or not revision.any:
            return state
        clear_slots, clear_state = _revision_clear_sets(revision)
    else:
        clear_slots = set(rule.get("clear_slots") or ())
        clear_state = set(rule.get("clear_state") or ())

    if rule.get("clear_confirmation") or clear_slots or clear_state:
        _apply_confirmation_rule(
            state,
            clear_slots=clear_slots,
            clear_state=clear_state,
            reason=effective_reason,
        )
    return state


def _revision_clear_sets(
    revision: BookingRevision,
) -> Tuple[Set[str], Set[str]]:
    """Map booking revision fields to slot/state drop sets (confirmation_gate parity)."""
    clear_slots: Set[str] = set()
    clear_state: Set[str] = set()

    if revision.time or revision.date or revision.service:
        clear_slots.add("time")
    if revision.date or revision.service:
        clear_slots.update({"date", "date_range"})
    if revision.service:
        clear_slots.add("service_id")
    if revision.date or revision.service:
        clear_state.update(_AVAILABILITY_STATE_KEYS)

    return clear_slots, clear_state


def _confirmation_flags_from_sets(
    clear_slots: Set[str],
    clear_state: Set[str],
) -> Dict[str, bool]:
    """Translate declarative slot/state sets into booking invalidation flags."""
    return {
        "clear_time": "time" in clear_slots,
        "clear_date": "date" in clear_slots or "date_range" in clear_slots,
        "clear_service": "service_id" in clear_slots,
        "clear_availability": bool(clear_state & _AVAILABILITY_STATE_KEYS),
    }


def _apply_confirmation_rule(
    state: Dict[str, Any],
    *,
    clear_slots: Set[str],
    clear_state: Set[str],
    reason: str,
) -> Dict[str, Any]:
    flags = _confirmation_flags_from_sets(clear_slots, clear_state)
    return clear_booking_state(state, reason=reason, **flags)


def _invalidate_ambiguous_service(
    merged: Dict[str, Any],
    merged_slots: Dict[str, Any],
    *,
    raw_service_id_from_session: Any,
    current_candidates: Any,
) -> None:
    """Drop stale session service_id when user mentioned an ambiguous service."""
    if not raw_service_id_from_session or not current_candidates:
        return

    merged_slots.pop("service_id", None)
    merged_slots.pop("_canonical_service_id", None)
    merged.setdefault("_intentionally_dropped_slots", set()).add("service_id")

    merged_facts_obj = merged.get("facts")
    if isinstance(merged_facts_obj, dict):
        nested_facts_slots = merged_facts_obj.get("slots")
        if isinstance(nested_facts_slots, dict) and "service_id" in nested_facts_slots:
            cleaned_nested = {
                k: v for k, v in nested_facts_slots.items() if k != "service_id"
            }
            if cleaned_nested:
                merged["facts"] = {**merged_facts_obj, "slots": cleaned_nested}
            else:
                merged["facts"] = {
                    k: v for k, v in merged_facts_obj.items() if k != "slots"
                }

    raw_snapshot = merged.get("_raw_luma_slots")
    if isinstance(raw_snapshot, dict):
        merged["_raw_luma_slots"] = {
            k: v
            for k, v in raw_snapshot.items()
            if k not in ("service_id", "_canonical_service_id")
        }

    logger.debug(
        "Dropped session service_id=%s: user mentioned a service (candidates=%s)",
        raw_service_id_from_session,
        current_candidates,
    )


def _invalidate_new_booking_request(
    merged_slots: Dict[str, Any],
    session_state: Optional[Dict[str, Any]],
    *,
    merged_intent_name: str,
    luma_slots: Dict[str, Any],
) -> bool:
    """Clear booking_id and session fingerprint when a new booking request starts."""
    if merged_intent_name != "CREATE_APPOINTMENT" or "booking_id" not in merged_slots:
        return False

    has_new_booking_slots = any(
        key in luma_slots and luma_slots.get(key) is not None
        for key in ("service_id", "date", "time")
    )
    if not has_new_booking_slots:
        return False

    del merged_slots["booking_id"]
    if session_state and "availability_fingerprint" in session_state:
        del session_state["availability_fingerprint"]
        logger.info(
            "Cleared availability_fingerprint due to new booking request")

    logger.info(
        "Cleared booking_id due to new booking request: intent=%s, new_slots=%s",
        merged_intent_name,
        [k for k in ("service_id", "date", "time") if k in luma_slots],
    )
    return True


def _handle_ambiguous_service(
    state: Dict[str, Any],
    *,
    merged_slots: Optional[Dict[str, Any]] = None,
    raw_service_id_from_session: Any = None,
    current_candidates: Optional[Any] = None,
    **_: Any,
) -> None:
    slots = merged_slots if isinstance(
        merged_slots, dict) else state.get("slots")
    if isinstance(slots, dict):
        _invalidate_ambiguous_service(
            state,
            slots,
            raw_service_id_from_session=raw_service_id_from_session,
            current_candidates=current_candidates or [],
        )


def _handle_new_booking_request(
    state: Dict[str, Any],
    *,
    merged_slots: Optional[Dict[str, Any]] = None,
    session_state: Optional[Dict[str, Any]] = None,
    merged_intent_name: Optional[str] = None,
    luma_slots: Optional[Dict[str, Any]] = None,
    **_: Any,
) -> None:
    slots = merged_slots if isinstance(
        merged_slots, dict) else state.get("slots")
    if isinstance(slots, dict):
        _invalidate_new_booking_request(
            slots,
            session_state,
            merged_intent_name=merged_intent_name or "",
            luma_slots=luma_slots or {},
        )


_CUSTOM_HANDLERS: Dict[str, Callable[..., None]] = {
    "ambiguous_service": _handle_ambiguous_service,
    "new_booking_request": _handle_new_booking_request,
}
