"""Invented NLU service facts must not overwrite a durable selected service."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import fields
from typing import Any, Dict, Optional

from core.planning.pipeline.requests import AttachedRequest
from core.planning.pipeline.stage02_working_turn import build_working_turn
from core.planning.pipeline.stage03_revision import apply_revision_policy

FLEXI = "flexi haircut + prunning"
PREMIUM = "premium haircut"
ALIASES = {PREMIUM: 1001, FLEXI: 1002}


def _attached(turn_operation: str = "PROVIDE_SLOT_VALUE") -> AttachedRequest:
    kwargs = {f.name: None for f in fields(AttachedRequest)}
    kwargs.update(
        {
            "planning_intent": "CREATE_APPOINTMENT",
            "turn_operation": turn_operation,
            "session_reset_occurred": False,
            "confirm_booking_continuation": False,
        }
    )
    return AttachedRequest(**kwargs)


def _flexi_session() -> Dict[str, Any]:
    return {
        "intent_name": "CREATE_APPOINTMENT",
        "intent": "CREATE_APPOINTMENT",
        "status": "AWAITING_CONFIRMATION",
        "confirmation_state": "pending",
        "slots": {
            "service_id": FLEXI,
            "_catalog_item_id": 1002,
            "date": "2026-07-03",
        },
        "missing_slots": ["time"],
    }


def _luma(
    *,
    facts: Optional[Dict[str, Any]] = None,
    intent: str = "CREATE_APPOINTMENT",
    temporal: Optional[Dict[str, Any]] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "intent": {"name": intent, "confidence": 0.95},
        "facts": dict(facts or {}),
        "temporal": temporal
        or {
            "mode": "none",
            "start_date": None,
            "start_time": None,
            "end_date": None,
            "end_time": None,
            "expression": None,
            "confidence": 0.0,
        },
    }
    if extra:
        payload.update(extra)
    return payload


def _run(
    luma: Dict[str, Any],
    *,
    text: str,
    session: Optional[Dict[str, Any]] = None,
    turn_operation: str = "PROVIDE_SLOT_VALUE",
):
    sess = session if session is not None else _flexi_session()
    working = build_working_turn(
        luma_response=deepcopy(luma),
        raw_luma_response_deep_copy=deepcopy(luma),
        attached_request=_attached(turn_operation),
        session_state=deepcopy(sess),
        original_session_state=deepcopy(sess),
        source_text=text,
        tenant_context={"aliases": dict(ALIASES)},
        apply_domain_filter=True,
    )
    apply_revision_policy(working, sess)
    return working


def _assert_flexi_durable(working) -> None:
    slots = working.payload.get("slots") or {}
    effective = working.effective_collected_slots or {}
    assert slots.get("service_id") == FLEXI
    assert slots.get("_catalog_item_id") == 1002
    assert effective.get("service_id") == FLEXI
    assert working.payload.get("_current_turn_has_service") is False


def test_time_only_invented_premium_does_not_replace_flexi():
    working = _run(
        _luma(
            facts={"service_id": PREMIUM, "service": PREMIUM, "times": ["09:30"]},
            temporal={
                "mode": "none",
                "start_date": None,
                "start_time": "09:30",
                "expression": "9:30",
                "confidence": 0.95,
            },
        ),
        text="9:30",
    )
    _assert_flexi_durable(working)
    assert working.payload.get("_current_turn_has_time") is True


def test_date_only_invented_premium_does_not_replace_flexi():
    working = _run(
        _luma(
            facts={"service_id": PREMIUM, "service": PREMIUM, "dates": ["2026-07-12"]},
            temporal={
                "mode": "single_day",
                "start_date": "2026-07-12",
                "start_time": None,
                "expression": "July 12",
                "confidence": 0.95,
            },
        ),
        text="July 12",
    )
    _assert_flexi_durable(working)
    assert working.payload.get("_current_turn_has_date") is True


def test_confirm_act_invented_premium_does_not_replace_flexi():
    working = _run(
        _luma(
            facts={"service_id": PREMIUM, "service": PREMIUM},
            intent="CONFIRM_ACTION",
        ),
        text="yes",
    )
    _assert_flexi_durable(working)


def test_explicit_service_correction_replaces_durable_flexi_with_premium():
    working = _run(
        _luma(facts={"service_id": PREMIUM, "service": PREMIUM}),
        text="switch to premium haircut",
        session={
            "intent_name": "CREATE_APPOINTMENT",
            "slots": {"service_id": FLEXI, "_catalog_item_id": 1002},
            "missing_slots": ["date", "time"],
        },
        turn_operation="CORRECTION",
    )
    slots = working.payload.get("slots") or {}
    effective = working.effective_collected_slots or {}
    assert working.payload.get("_current_turn_has_service") is True
    assert slots.get("service_id") == PREMIUM
    assert slots.get("_catalog_item_id") == 1001
    assert effective.get("service_id") == PREMIUM
    assert effective.get("_catalog_item_id") == 1001

def test_same_turn_service_and_time_correction_replaces_flexi():
    working = _run(
        _luma(
            facts={"service_id": PREMIUM, "service": PREMIUM, "times": ["11:00"]},
            temporal={
                "mode": "none",
                "start_date": None,
                "start_time": "11:00",
                "expression": "11am",
                "confidence": 0.95,
            },
        ),
        text="switch to premium haircut at 11am",
    )
    slots = working.payload.get("slots") or {}
    assert working.payload.get("_current_turn_has_service") is True
    assert slots.get("service_id") == PREMIUM
    assert slots.get("_catalog_item_id") == 1001


def test_time_revision_utterance_with_invented_service_keeps_flexi():
    working = _run(
        _luma(
            facts={"service_id": PREMIUM, "service": PREMIUM, "times": ["10:00"]},
            intent="CORRECTION",
            temporal={
                "mode": "none",
                "start_date": None,
                "start_time": "10:00",
                "expression": "10am",
                "confidence": 0.95,
            },
        ),
        text="switch to 10am",
    )
    _assert_flexi_durable(working)
    assert working.payload.get("_current_turn_has_time") is True

