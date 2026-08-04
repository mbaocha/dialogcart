"""Core-owned current-turn planning evidence.

NLU may emit ``turn.understanding`` for observability. Whether this turn
produced useful booking/planning evidence is owned by Core after promotion.

Computed exactly once in Stage 02 (after promotion, before merge). Immutable
for the remainder of the turn. Consumers must only ``read_planning_evidence``.

Session-carried slots alone are never evidence. Only structured current-turn
deltas count: promoted platform/schema slots, declines, temporal proposals,
operations, and raw planning dialog acts (CONFIRM_ACTION / REJECT_ACTION).
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Set

from core.adapters.nlu.entity_schema_builder import (
    promotable_slot_keys_from_entity_schema,
)
from core.planning.luma_facts_adapter import facts_to_slots

# Dialog acts that advance booking/confirmation planning without slot deltas.
# Must be evaluated from the raw NLU act — never from normalized planner intent.
_PLANNING_DIALOG_ACTS = frozenset({"CONFIRM_ACTION", "REJECT_ACTION"})

# Structured NLU operations that advance availability / discovery planning.
_PLANNING_OPERATIONS = frozenset(
    {
        "browse_next",
        "browse_previous",
        "browse_more_times",
        "browse_more_days",
        "AVAILABILITY",
        "CHECK_AVAILABILITY",
    }
)

# Always eligible platform planning keys (independent of entity_schema).
_PLATFORM_SLOT_KEYS = frozenset(
    {
        "service_id",
        "booking_id",
        "date",
        "time",
        "start_date",
        "end_date",
        "date_range",
        "staff_id",
        "location",
        "resource",
        "resource_id",
    }
)

_PAYLOAD_EVIDENCE_KEY = "_current_turn_planning_evidence"
_OUTCOME_EVIDENCE_KEY = "current_turn_planning_evidence"


def planning_evidence_payload_key() -> str:
    return _PAYLOAD_EVIDENCE_KEY


def planning_evidence_outcome_key() -> str:
    return _OUTCOME_EVIDENCE_KEY


def _meaningful(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str) and not value.strip():
        return False
    if isinstance(value, (list, tuple, set, dict)) and not value:
        return False
    return True


def _entity_schema_from(payload: Optional[Mapping[str, Any]]) -> Optional[Mapping[str, Any]]:
    if not isinstance(payload, Mapping):
        return None
    schema = payload.get("_entity_schema")
    if isinstance(schema, Mapping):
        return schema
    facts = payload.get("facts")
    if isinstance(facts, Mapping):
        nested = facts.get("_entity_schema")
        if isinstance(nested, Mapping):
            return nested
    return None


def _operation(
    payload: Mapping[str, Any],
    *,
    operation: Optional[str] = None,
) -> Optional[str]:
    if isinstance(operation, str) and operation.strip():
        return operation.strip()
    op = payload.get("operation")
    if isinstance(op, str) and op.strip():
        return op.strip()
    facts = payload.get("facts")
    if isinstance(facts, Mapping):
        nested = facts.get("operation")
        if isinstance(nested, str) and nested.strip():
            return nested.strip()
    return None


def _allowlisted_slot_keys(entity_schema: Optional[Mapping[str, Any]]) -> Set[str]:
    keys = set(_PLATFORM_SLOT_KEYS)
    keys |= set(promotable_slot_keys_from_entity_schema(entity_schema))
    return keys


def _turn_slots_from_payload(
    payload: Mapping[str, Any],
    *,
    raw_turn_slots: Optional[Mapping[str, Any]] = None,
    entity_schema: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Resolve current-turn slot candidates (not session-merged durable slots)."""
    if isinstance(raw_turn_slots, Mapping) and raw_turn_slots:
        return {k: v for k, v in raw_turn_slots.items() if _meaningful(v)}

    raw = payload.get("_raw_luma_slots")
    if isinstance(raw, Mapping) and raw:
        return {k: v for k, v in raw.items() if _meaningful(v)}

    schema = entity_schema or _entity_schema_from(payload)
    facts = payload.get("facts")
    promoted = (
        facts_to_slots(facts, entity_schema=schema)
        if isinstance(facts, Mapping)
        else {}
    )
    nested: Dict[str, Any] = {}
    if isinstance(facts, Mapping) and isinstance(facts.get("slots"), Mapping):
        nested = dict(facts.get("slots") or {})
    out = {**nested, **promoted}
    return {k: v for k, v in out.items() if _meaningful(v)}


def current_turn_slot_deltas(
    payload: Mapping[str, Any],
    *,
    session_slots: Optional[Mapping[str, Any]] = None,
    raw_turn_slots: Optional[Mapping[str, Any]] = None,
    entity_schema: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Allowlisted slot keys newly provided or changed this turn."""
    schema = entity_schema or _entity_schema_from(payload)
    allow = _allowlisted_slot_keys(schema)
    prior = session_slots if isinstance(session_slots, Mapping) else {}
    turn_slots = _turn_slots_from_payload(
        payload, raw_turn_slots=raw_turn_slots, entity_schema=schema
    )

    deltas: Dict[str, Any] = {}
    for key, value in turn_slots.items():
        if key.startswith("_"):
            continue
        if key not in allow:
            continue
        if not _meaningful(value):
            continue
        if key not in prior or prior.get(key) != value:
            deltas[key] = value
    return deltas


def has_current_turn_planning_evidence(
    payload: Optional[Mapping[str, Any]],
    *,
    session_slots: Optional[Mapping[str, Any]] = None,
    raw_turn_slots: Optional[Mapping[str, Any]] = None,
    entity_schema: Optional[Mapping[str, Any]] = None,
    raw_dialogue_act: Optional[str] = None,
    operation: Optional[str] = None,
) -> bool:
    """True when structured current-turn outputs advance planning.

    Does not inspect raw user text. Does not treat durable session slots alone
    as evidence. Does not use normalized planner intent — pass ``raw_dialogue_act``.
    """
    if not isinstance(payload, Mapping):
        return False

    act = (raw_dialogue_act or "").strip()
    if act in _PLANNING_DIALOG_ACTS:
        return True

    op = _operation(payload, operation=operation)
    if op and op in _PLANNING_OPERATIONS:
        return True

    declined = payload.get("declined_entities")
    if isinstance(declined, list) and any(
        isinstance(item, str) and item.strip() for item in declined
    ):
        return True
    declined_slots = payload.get("declined_slots")
    if isinstance(declined_slots, list) and any(
        isinstance(item, str) and item.strip() for item in declined_slots
    ):
        return True

    # Provenance flags stamped by Stage 02 before merge.
    if payload.get("_current_turn_has_date") or payload.get("_current_turn_has_time"):
        return True

    from core.planning.booking_revision import has_revision_facts

    if has_revision_facts(dict(payload)):
        return True

    if current_turn_slot_deltas(
        payload,
        session_slots=session_slots,
        raw_turn_slots=raw_turn_slots,
        entity_schema=entity_schema,
    ):
        return True

    return False


def stamp_planning_evidence(
    payload: Dict[str, Any],
    *,
    session_slots: Optional[Mapping[str, Any]] = None,
    raw_turn_slots: Optional[Mapping[str, Any]] = None,
    entity_schema: Optional[Mapping[str, Any]] = None,
    raw_dialogue_act: Optional[str] = None,
    operation: Optional[str] = None,
) -> bool:
    """Compute once, stamp immutably, and return current-turn planning evidence.

    Must only be called from Stage 02 before merge. Downstream must read only.
    """
    if _PAYLOAD_EVIDENCE_KEY in payload:
        return bool(payload.get(_PAYLOAD_EVIDENCE_KEY))

    evidence = has_current_turn_planning_evidence(
        payload,
        session_slots=session_slots,
        raw_turn_slots=raw_turn_slots,
        entity_schema=entity_schema,
        raw_dialogue_act=raw_dialogue_act,
        operation=operation,
    )
    payload[_PAYLOAD_EVIDENCE_KEY] = evidence
    return evidence


def read_planning_evidence(
    *sources: Optional[Mapping[str, Any]],
) -> Optional[bool]:
    """Read stamped planning-evidence flag from payload / plan / outcome facts."""
    for source in sources:
        if not isinstance(source, Mapping):
            continue
        if _PAYLOAD_EVIDENCE_KEY in source:
            return bool(source.get(_PAYLOAD_EVIDENCE_KEY))
        if _OUTCOME_EVIDENCE_KEY in source:
            return bool(source.get(_OUTCOME_EVIDENCE_KEY))
        facts = source.get("facts")
        if isinstance(facts, Mapping) and _OUTCOME_EVIDENCE_KEY in facts:
            return bool(facts.get(_OUTCOME_EVIDENCE_KEY))
        plan = source.get("plan")
        if isinstance(plan, Mapping):
            if _PAYLOAD_EVIDENCE_KEY in plan:
                return bool(plan.get(_PAYLOAD_EVIDENCE_KEY))
            if _OUTCOME_EVIDENCE_KEY in plan:
                return bool(plan.get(_OUTCOME_EVIDENCE_KEY))
    return None


def require_planning_evidence(*sources: Optional[Mapping[str, Any]]) -> bool:
    """Read stamped evidence; missing stamp is treated as False (no recompute)."""
    stamped = read_planning_evidence(*sources)
    return bool(stamped)
