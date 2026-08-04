"""Parity: planning mutation coordinator matches direct invalidation application."""

from __future__ import annotations

import copy
from typing import Any, Dict

from core.planning.booking_revision import BookingRevision, FieldChange
from core.planning.pipeline.types import WorkingTurn
from core.planning.planning_mutations import (
    apply_booking_revision_mutations,
    apply_confirmation_planning_mutations,
    apply_trigger,
)
from core.session.invalidation import InvalidationTrigger, apply_invalidation


def _base_pending_state() -> Dict[str, Any]:
    return {
        "intent_name": "CREATE_APPOINTMENT",
        "confirmation_state": "pending",
        "slots": {
            "service_id": "premium haircut",
            "date": "2026-07-10",
            "time": "10:00",
            "has_datetime": True,
        },
        "resolved_datetime_range": {
            "start": "2026-07-10T10:00:00Z",
            "end": "2026-07-10T10:30:00Z",
        },
        "presented_availability": {"slots": [{"time": "10:00"}]},
        "availability_fingerprint": "fp-1",
        "time_proposal": {"mode": "exact", "value": "10:00"},
    }


def test_parity_apply_trigger_matches_apply_invalidation_time_rebound():
    via_registry = _base_pending_state()
    via_coordinator = copy.deepcopy(via_registry)
    apply_invalidation(
        via_registry,
        InvalidationTrigger.TIME_REBOUND,
        reason="rebound_selection",
    )
    apply_trigger(
        via_coordinator,
        InvalidationTrigger.TIME_REBOUND,
        reason="rebound_selection",
    )
    assert via_coordinator == via_registry


def test_parity_apply_trigger_matches_reject_confirmation():
    via_registry = _base_pending_state()
    via_coordinator = copy.deepcopy(via_registry)
    apply_invalidation(
        via_registry,
        InvalidationTrigger.REJECT_CONFIRMATION,
        reason="reject",
    )
    apply_trigger(
        via_coordinator,
        InvalidationTrigger.REJECT_CONFIRMATION,
        reason="reject",
    )
    assert via_coordinator == via_registry


def test_parity_booking_revision_mutations_service_change():
    revision = BookingRevision(
        changes=(
            FieldChange(
                field="service",
                from_value="premium haircut",
                to_value="flexi haircut",
            ),
        ),
        service=True,
        date=False,
        time=False,
        criteria=False,
    )

    payload = _base_pending_state()
    payload["_current_turn_has_time"] = False
    current = {
        "service_id": "flexi haircut",
        "_canonical_service_id": "flexi-id",
        "date": "2026-07-10",
    }
    payload["slots"] = dict(current)
    payload["_effective_collected_slots"] = dict(current)
    working = WorkingTurn(
        payload=payload,
        effective_collected_slots=dict(current),
    )
    apply_booking_revision_mutations(
        working,
        revision,
        reason="planning_revision",
    )

    assert working.payload["slots"]["service_id"] == "flexi haircut"
    assert working.payload["slots"]["_canonical_service_id"] == "flexi-id"
    # Service revision clears prior date/time; restore only replaces service keys.
    assert "date" not in working.payload["slots"]
    assert "time" not in working.payload["slots"]
    assert working.payload.get("_revision_invalidated_availability") is True
    assert working.payload.get("resolved_datetime_range") is None
    assert working.effective_collected_slots == working.payload["slots"]


def test_parity_confirmation_mutations_reject_via_coordinator():
    from types import SimpleNamespace

    payload = _base_pending_state()
    working = WorkingTurn(
        payload=payload,
        effective_collected_slots=dict(payload["slots"]),
    )
    confirmation = SimpleNamespace(
        lifecycle_evidence=SimpleNamespace(action="reject", reason="user_reject"),
        reject_evidence=SimpleNamespace(rejected=True, reason_code="REJECT"),
        consume_evidence=None,
        bound_datetime_clear=None,
    )
    session = copy.deepcopy(payload)
    apply_confirmation_planning_mutations(
        working,
        confirmation,
        session_state=session,
    )
    assert working.payload.get("_booking_confirmation_rejected") is True
    assert working.payload.get("confirmation_state") is None
    assert "time" not in (working.payload.get("slots") or {})
    assert session.get("_booking_confirmation_rejected") is True
    assert session.get("confirmation_state") is None
