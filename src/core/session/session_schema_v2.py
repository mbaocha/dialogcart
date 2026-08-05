"""Canonical Session schema V2 — construction, normalization, and compatibility.

This module owns session **shape** only. It does not perform planning, execution,
or rendering decisions.

Ownership (durable fields):
- Planning owns ``planning.*``, including slots and bound datetime.
- Execution results are ephemeral; only durable outcomes are projected.
- Booking contains only successfully committed backend identifiers.
- SessionProjector owns durable writes (via ``SessionProjectorV2``).
- Confirmation gate owns ``confirmation_state``.
- API ingress owns resolve-or-create timing for tenant ``customer_id``; session
  persists the canonical commerce ``customers.id`` once resolved.
- API owns persisted ``conversation.history``.
- Capabilities produce ``capability`` artifacts; projector persists minimal continuation state.
- Rendering owns no durable fields.
- NLU owns no persisted fields.
"""

from __future__ import annotations

import copy
import logging
from typing import Any, Dict, List, Mapping, Optional

logger = logging.getLogger(__name__)

SESSION_SCHEMA_VERSION = 2

# Minimal capability continuation facts allowed in V2 (not the raw NLU facts bag).
_CAPABILITY_RESULT_KEYS = frozenset(
    {
        "payment_satisfied",
    }
)

def empty_session_v2() -> Dict[str, Any]:
    """Return a defensive copy of the canonical empty Session V2 document."""
    return copy.deepcopy(_EMPTY_SESSION_V2)


_EMPTY_SESSION_V2: Dict[str, Any] = {
    "schema_version": SESSION_SCHEMA_VERSION,
    "conversation": {
        "history": [],
        "memory": {},
    },
    "planning": {
        "intent_name": None,
        "status": None,
        "slots": {},
        "bound_datetime": None,
        "missing_slots": [],
        "ask_next": None,
        "declined_slots": [],
        "retry": {
            "slot_attempts": {},
            "last_filled_slot": None,
        },
        "proposals": {
            "date": None,
            "time": None,
        },
        "constraints": {
            "date": None,
            "time": None,
        },
        "temporal": None,
        "service_candidates": None,
        "modification_context": None,
        "context": {
            "date_roles": None,
        },
    },
    "booking": {
        "booking_id": None,
        "booking_code": None,
        "identity_reconfirm_required": False,
    },
    "availability": {
        "fingerprint": None,
        "cache": {
            "search_result": None,
        },
        "presentation": {
            "presented": None,
            "page_index": 0,
            "page_size": None,
        },
    },
    "confirmation_state": None,
    "customer_id": None,
    "capability": {
        "active": None,
        "results": {},
    },
}


def detect_schema_version(session: Optional[Mapping[str, Any]]) -> int:
    """Return ``2`` when the document is V2-shaped, otherwise ``1``."""
    if not isinstance(session, Mapping):
        return 1
    version = session.get("schema_version")
    if version == SESSION_SCHEMA_VERSION and isinstance(session.get("planning"), dict):
        return SESSION_SCHEMA_VERSION
    return 1


def is_session_v2(session: Optional[Mapping[str, Any]]) -> bool:
    return detect_schema_version(session) == SESSION_SCHEMA_VERSION


def defensive_copy_session(session: Mapping[str, Any]) -> Dict[str, Any]:
    """Return a deep copy safe for mutation during projection."""
    return copy.deepcopy(dict(session))


def validate_session_v2_sections(session: Mapping[str, Any]) -> None:
    """Validate required section types for a V2 session document."""
    if session.get("schema_version") != SESSION_SCHEMA_VERSION:
        raise ValueError("schema_version must be 2")

    conversation = session.get("conversation")
    if not isinstance(conversation, dict):
        raise TypeError("conversation must be a dict")
    if not isinstance(conversation.get("history"), list):
        raise TypeError("conversation.history must be a list")
    if not isinstance(conversation.get("memory"), dict):
        raise TypeError("conversation.memory must be a dict")

    planning = session.get("planning")
    if not isinstance(planning, dict):
        raise TypeError("planning must be a dict")
    if not isinstance(planning.get("slots"), dict):
        raise TypeError("planning.slots must be a dict")
    retry = planning.get("retry")
    if not isinstance(retry, dict):
        raise TypeError("planning.retry must be a dict")
    if not isinstance(retry.get("slot_attempts"), dict):
        raise TypeError("planning.retry.slot_attempts must be a dict")
    proposals = planning.get("proposals")
    if not isinstance(proposals, dict):
        raise TypeError("planning.proposals must be a dict")
    constraints = planning.get("constraints")
    if not isinstance(constraints, dict):
        raise TypeError("planning.constraints must be a dict")
    planning_context = planning.get("context")
    if not isinstance(planning_context, dict):
        raise TypeError("planning.context must be a dict")
    if not isinstance(planning.get("missing_slots"), list):
        raise TypeError("planning.missing_slots must be a list")
    if not isinstance(planning.get("declined_slots"), list):
        raise TypeError("planning.declined_slots must be a list")

    booking = session.get("booking")
    if not isinstance(booking, dict):
        raise TypeError("booking must be a dict")

    availability = session.get("availability")
    if not isinstance(availability, dict):
        raise TypeError("availability must be a dict")
    cache = availability.get("cache")
    if not isinstance(cache, dict):
        raise TypeError("availability.cache must be a dict")
    presentation = availability.get("presentation")
    if not isinstance(presentation, dict):
        raise TypeError("availability.presentation must be a dict")

    capability = session.get("capability")
    if not isinstance(capability, dict):
        raise TypeError("capability must be a dict")
    if not isinstance(capability.get("results"), dict):
        raise TypeError("capability.results must be a dict")


def normalize_session_to_v2(session: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """Normalize any loaded session document to canonical Session V2.

    Idempotent: V2 input yields equivalent V2 output. Top-level legacy values win
    over nested mirrors when both are present during migration.
    """
    if not isinstance(session, Mapping):
        return empty_session_v2()

    working = defensive_copy_session(session)
    v2 = empty_session_v2()

    planning = v2["planning"]
    booking = v2["booking"]
    availability = v2["availability"]
    capability = v2["capability"]
    conversation = v2["conversation"]

    nested_planning = working.get("planning") if isinstance(working.get("planning"), dict) else {}
    nested_booking = working.get("booking") if isinstance(working.get("booking"), dict) else {}
    nested_availability = (
        working.get("availability") if isinstance(working.get("availability"), dict) else {}
    )
    nested_capability = (
        working.get("capability") if isinstance(working.get("capability"), dict) else {}
    )
    nested_conversation = (
        working.get("conversation") if isinstance(working.get("conversation"), dict) else {}
    )

    planning["intent_name"] = _pick_scalar(
        working.get("intent_name"),
        working.get("intent"),
        nested_planning.get("intent_name"),
    )
    planning["status"] = _pick_scalar(working.get("status"), nested_planning.get("status"))

    planning["missing_slots"] = _pick_list(
        working.get("missing_slots"),
        nested_planning.get("missing_slots"),
    )
    planning["ask_next"] = _pick_scalar(
        working.get("ask_next"),
        nested_planning.get("ask_next"),
    )
    planning["declined_slots"] = _pick_list(
        working.get("declined_slots"),
        nested_planning.get("declined_slots"),
    )

    nested_retry = (
        nested_planning.get("retry") if isinstance(nested_planning.get("retry"), dict) else {}
    )
    planning["retry"] = {
        "slot_attempts": _pick_dict(
            working.get("slot_attempts"),
            nested_retry.get("slot_attempts"),
        ),
        "last_filled_slot": _pick_scalar(
            working.get("last_filled_slot"),
            nested_retry.get("last_filled_slot"),
        ),
    }

    nested_proposals = (
        nested_planning.get("proposals")
        if isinstance(nested_planning.get("proposals"), dict)
        else {}
    )
    planning["proposals"] = {
        "date": _pick_scalar(
            working.get("date_proposal"),
            nested_proposals.get("date"),
        ),
        "time": _pick_scalar(
            working.get("time_proposal"),
            nested_proposals.get("time"),
        ),
    }

    nested_constraints = (
        nested_planning.get("constraints")
        if isinstance(nested_planning.get("constraints"), dict)
        else {}
    )
    _ = nested_constraints
    planning["constraints"] = {
        "date": None,
        "time": None,
    }

    planning["temporal"] = _pick_scalar(
        working.get("temporal"),
        nested_planning.get("temporal"),
    )

    planning["service_candidates"] = _pick_scalar(
        working.get("service_candidates"),
        nested_planning.get("service_candidates"),
    )
    planning["modification_context"] = _pick_scalar(
        working.get("_modification_context"),
        nested_planning.get("modification_context"),
    )

    legacy_context = working.get("context") if isinstance(working.get("context"), dict) else {}
    nested_planning_context = (
        nested_planning.get("context")
        if isinstance(nested_planning.get("context"), dict)
        else {}
    )
    planning["context"] = {
        "date_roles": _pick_scalar(
            legacy_context.get("date_roles"),
            nested_planning_context.get("date_roles"),
        ),
    }

    facts = working.get("facts") if isinstance(working.get("facts"), dict) else {}
    fact_slots = facts.get("slots") if isinstance(facts.get("slots"), dict) else {}
    nested_planning_slots = (
        nested_planning.get("slots")
        if isinstance(nested_planning.get("slots"), dict)
        else {}
    )
    legacy_v2_slots = (
        nested_booking.get("slots")
        if isinstance(nested_booking.get("slots"), dict)
        else {}
    )
    planning["slots"] = _pick_dict(
        working.get("slots"),
        fact_slots,
        nested_planning_slots,
        legacy_v2_slots,
    )

    planning["bound_datetime"] = _pick_scalar(
        working.get("resolved_datetime_range"),
        nested_planning.get("bound_datetime"),
        nested_booking.get("bound_datetime"),
    )

    nested_committed = (
        nested_booking.get("committed")
        if isinstance(nested_booking.get("committed"), dict)
        else {}
    )
    booking["booking_id"] = _pick_scalar(
        working.get("booking_id"),
        nested_booking.get("booking_id"),
        nested_booking.get("id"),
        planning["slots"].get("booking_id"),
        nested_committed.get("booking_id"),
    )
    booking["booking_code"] = _pick_scalar(
        working.get("booking_code"),
        nested_booking.get("booking_code"),
        nested_booking.get("code"),
        planning["slots"].get("booking_code"),
        nested_committed.get("booking_code"),
    )
    booking["identity_reconfirm_required"] = bool(
        working.get("identity_reconfirm_required")
        or nested_booking.get("identity_reconfirm_required")
    )
    # Committed identifiers belong only to booking, never planning slots.
    planning["slots"].pop("booking_id", None)
    planning["slots"].pop("booking_code", None)

    nested_cache = (
        nested_availability.get("cache")
        if isinstance(nested_availability.get("cache"), dict)
        else {}
    )
    nested_presentation = (
        nested_availability.get("presentation")
        if isinstance(nested_availability.get("presentation"), dict)
        else {}
    )
    availability_presentation = (
        working.get("availability_presentation")
        if isinstance(working.get("availability_presentation"), dict)
        else {}
    )
    availability["fingerprint"] = _pick_scalar(
        working.get("availability_fingerprint"),
        nested_availability.get("fingerprint"),
    )
    availability["cache"] = {
        "search_result": _pick_scalar(
            working.get("last_execution_result"),
            nested_cache.get("search_result"),
        ),
    }
    availability["presentation"] = {
        "presented": _pick_scalar(
            working.get("presented_availability"),
            nested_presentation.get("presented"),
        ),
        "page_index": _pick_scalar(
            availability_presentation.get("page_index"),
            nested_presentation.get("page_index"),
            0,
        ),
        "page_size": _pick_scalar(
            availability_presentation.get("page_size"),
            nested_presentation.get("page_size"),
        ),
    }

    v2["confirmation_state"] = _resolve_confirmation_state(working)
    v2["customer_id"] = _pick_scalar(working.get("customer_id"))

    capability["active"] = _pick_scalar(
        working.get("active_capability"),
        nested_capability.get("active"),
    )
    capability["results"] = _extract_capability_results(
        working.get("facts"),
        nested_capability.get("results"),
    )

    conversation["history"] = _pick_list(
        working.get("messages"),
        nested_conversation.get("history"),
        nested_conversation.get("messages"),
    )
    legacy_conversation = (
        working.get("conversation") if isinstance(working.get("conversation"), dict) else {}
    )
    if "history" in legacy_conversation or "messages" in legacy_conversation:
        conversation["memory"] = _pick_dict(
            legacy_conversation.get("memory"),
            legacy_conversation.get("context"),
            nested_conversation.get("memory"),
            nested_conversation.get("context"),
        )
    else:
        conversation["memory"] = _pick_dict(
            legacy_conversation,
            nested_conversation.get("memory"),
            nested_conversation.get("context"),
        )

    validate_session_v2_sections(v2)
    return v2


def hydrate_v1_compat_shims(v2_session: Mapping[str, Any]) -> Dict[str, Any]:
    """Attach legacy top-level mirrors for in-memory consumers (not persisted)."""
    v2 = normalize_session_to_v2(v2_session)
    working: Dict[str, Any] = defensive_copy_session(v2)
    planning = working["planning"]
    availability = working["availability"]
    capability = working["capability"]
    conversation = working["conversation"]

    working["intent_name"] = planning.get("intent_name")
    if planning.get("intent_name"):
        working["intent"] = planning.get("intent_name")
    working["status"] = planning.get("status")

    working["missing_slots"] = list(planning.get("missing_slots") or [])
    working["ask_next"] = planning.get("ask_next")
    working["declined_slots"] = list(planning.get("declined_slots") or [])
    retry = planning.get("retry") or {}
    working["slot_attempts"] = copy.deepcopy(retry.get("slot_attempts") or {})
    working["last_filled_slot"] = retry.get("last_filled_slot")

    proposals = planning.get("proposals") or {}
    if proposals.get("date") is not None:
        working["date_proposal"] = proposals.get("date")
    if proposals.get("time") is not None:
        working["time_proposal"] = proposals.get("time")

    constraints = planning.get("constraints") or {}
    _ = constraints  # legacy nested key retained empty; Temporal is canonical

    if planning.get("temporal") is not None:
        working["temporal"] = copy.deepcopy(planning.get("temporal"))
    working.pop("date_constraint", None)
    working.pop("time_constraint", None)
    if planning.get("service_candidates") is not None:
        working["service_candidates"] = planning.get("service_candidates")
    if planning.get("modification_context") is not None:
        working["_modification_context"] = planning.get("modification_context")

    planning_context = planning.get("context") or {}
    if planning_context.get("date_roles") is not None:
        working["context"] = {"date_roles": planning_context.get("date_roles")}

    working["slots"] = copy.deepcopy(planning.get("slots") or {})
    if planning.get("bound_datetime") is not None:
        working["resolved_datetime_range"] = planning.get("bound_datetime")

    booking = working["booking"]
    if booking.get("booking_id") is not None:
        working.setdefault("slots", {})
        working["slots"].setdefault("booking_id", booking.get("booking_id"))
    if booking.get("booking_code") is not None:
        working.setdefault("slots", {})
        working["slots"].setdefault("booking_code", booking.get("booking_code"))

    if booking.get("identity_reconfirm_required"):
        working["identity_reconfirm_required"] = True
    else:
        working.pop("identity_reconfirm_required", None)

    # Availability mirrors are no longer hydrated: runtime consumers use
    # nested availability.* via canonical accessors.

    working["confirmation_state"] = v2.get("confirmation_state")
    working["customer_id"] = v2.get("customer_id")

    if capability.get("active") is not None:
        working["active_capability"] = capability.get("active")

    if conversation.get("history"):
        working["messages"] = copy.deepcopy(conversation.get("history") or [])

    working["facts"] = _build_compat_facts(working, capability.get("results") or {})
    return working


def sync_working_session_to_pure_v2(working: Mapping[str, Any]) -> Dict[str, Any]:
    """Build the persisted V2 document from a possibly-mutated working session."""
    merged = defensive_copy_session(working)
    capability_results = _extract_capability_results(merged.get("facts"))
    nested_results = {}
    capability_section = merged.get("capability")
    if isinstance(capability_section, dict) and isinstance(
        capability_section.get("results"), dict
    ):
        nested_results = capability_section.get("results") or {}
    merged_capability_results = {**nested_results, **capability_results}

    # Flat legacy fields win over nested mirrors during sync.
    v2 = normalize_session_to_v2(merged)
    v2["capability"]["results"] = merged_capability_results
    validate_session_v2_sections(v2)
    return v2


def prepare_session_for_load(session: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """Normalize persisted data and hydrate legacy mirrors for runtime consumers."""
    v2 = normalize_session_to_v2(session)
    return hydrate_v1_compat_shims(v2)


def prepare_session_for_persist(working: Mapping[str, Any]) -> Dict[str, Any]:
    """Return the canonical V2 document to write to storage."""
    return sync_working_session_to_pure_v2(working)


# ---------------------------------------------------------------------------
# Compatibility accessors (read paths for legacy consumers)
# ---------------------------------------------------------------------------


def get_intent_name(session: Optional[Mapping[str, Any]]) -> Optional[str]:
    if not isinstance(session, Mapping):
        return None
    if session.get("intent_name") is not None:
        return session.get("intent_name")  # type: ignore[return-value]
    if session.get("intent") is not None:
        return session.get("intent")  # type: ignore[return-value]
    planning = session.get("planning")
    if isinstance(planning, dict):
        return planning.get("intent_name")
    return None


def get_planning_status(session: Optional[Mapping[str, Any]]) -> Optional[str]:
    if not isinstance(session, Mapping):
        return None
    if "status" in session:
        return session.get("status")  # type: ignore[return-value]
    planning = session.get("planning")
    if isinstance(planning, dict):
        return planning.get("status")
    return None


def get_booking_slots(session: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    if not isinstance(session, Mapping):
        return {}
    slots = session.get("slots")
    if isinstance(slots, dict):
        return dict(slots)
    planning = session.get("planning")
    if isinstance(planning, dict) and isinstance(planning.get("slots"), dict):
        return dict(planning["slots"])
    # Existing Session V2 layout compatibility.
    booking = session.get("booking")
    if isinstance(booking, dict) and isinstance(booking.get("slots"), dict):
        return dict(booking["slots"])
    facts = session.get("facts")
    if isinstance(facts, dict) and isinstance(facts.get("slots"), dict):
        return dict(facts["slots"])
    return {}


def get_slot_attempts(session: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    if not isinstance(session, Mapping):
        return {}
    attempts = session.get("slot_attempts")
    if isinstance(attempts, dict):
        return dict(attempts)
    planning = session.get("planning")
    if isinstance(planning, dict):
        retry = planning.get("retry")
        if isinstance(retry, dict) and isinstance(retry.get("slot_attempts"), dict):
            return dict(retry["slot_attempts"])
    return {}


def get_conversation_history(
    session: Optional[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    if not isinstance(session, Mapping):
        return []
    messages = session.get("messages")
    if isinstance(messages, list):
        return copy.deepcopy(messages)
    conversation = session.get("conversation")
    if isinstance(conversation, dict):
        history = conversation.get("history")
        if isinstance(history, list):
            return copy.deepcopy(history)
        legacy_messages = conversation.get("messages")
        if isinstance(legacy_messages, list):
            return copy.deepcopy(legacy_messages)
    return []


def get_conversation_memory(
    session: Optional[Mapping[str, Any]],
) -> Dict[str, Any]:
    if not isinstance(session, Mapping):
        return {}
    conversation = session.get("conversation")
    if not isinstance(conversation, dict):
        return {}
    memory = conversation.get("memory")
    if isinstance(memory, dict):
        return copy.deepcopy(memory)
    context = conversation.get("context")
    if isinstance(context, dict):
        return copy.deepcopy(context)
    # V1 stored the memory payload directly under ``conversation``.
    return copy.deepcopy(conversation)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _pick_scalar(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return copy.deepcopy(value)
    return None


def _pick_list(*values: Any) -> List[Any]:
    for value in values:
        if isinstance(value, list):
            return copy.deepcopy(value)
    return []


def _pick_dict(*values: Any) -> Dict[str, Any]:
    chosen: Optional[Dict[str, Any]] = None
    for value in values:
        if isinstance(value, dict):
            if value:
                return copy.deepcopy(value)
            if chosen is None:
                chosen = {}
    return copy.deepcopy(chosen) if chosen is not None else {}


def _resolve_confirmation_state(working: Mapping[str, Any]) -> Optional[str]:
    if "confirmation_state" in working:
        return working.get("confirmation_state")  # type: ignore[return-value]
    booking = working.get("booking")
    if isinstance(booking, dict) and booking.get("confirmation_state") is not None:
        return booking.get("confirmation_state")  # type: ignore[return-value]
    nested = working.get("confirmation_state")
    return nested  # type: ignore[return-value]


def _extract_capability_results(
    facts: Any,
    existing_results: Any = None,
) -> Dict[str, Any]:
    results: Dict[str, Any] = {}
    if isinstance(existing_results, dict):
        results.update(copy.deepcopy(existing_results))
    if isinstance(facts, dict):
        for key in _CAPABILITY_RESULT_KEYS:
            if key in facts and facts[key] is not None:
                results[key] = copy.deepcopy(facts[key])
    return results


def _build_compat_facts(
    session: Mapping[str, Any],
    capability_results: Mapping[str, Any],
) -> Dict[str, Any]:
    """Rebuild the minimal legacy facts dict required by existing consumers."""
    facts: Dict[str, Any] = copy.deepcopy(dict(capability_results))
    slots = get_booking_slots(session)
    if slots:
        facts["slots"] = copy.deepcopy(slots)
    attempts = get_slot_attempts(session)
    if attempts:
        facts["slot_attempts"] = copy.deepcopy(attempts)
    return facts


__all__ = [
    "SESSION_SCHEMA_VERSION",
    "defensive_copy_session",
    "detect_schema_version",
    "empty_session_v2",
    "get_booking_slots",
    "get_conversation_history",
    "get_conversation_memory",
    "get_intent_name",
    "get_planning_status",
    "get_slot_attempts",
    "hydrate_v1_compat_shims",
    "is_session_v2",
    "normalize_session_to_v2",
    "prepare_session_for_load",
    "prepare_session_for_persist",
    "sync_working_session_to_pure_v2",
    "validate_session_v2_sections",
]
