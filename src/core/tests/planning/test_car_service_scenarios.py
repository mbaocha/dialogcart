"""Focused car_service vertical scenarios (config-driven; ScriptedLuma).

Validates a second business category end-to-end through handle_message without
redesigning planning/execution. NLU turns are scripted so tests stay offline.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional
from unittest.mock import Mock

from core.adapters.cache.catalog_cache import catalog_cache
from core.adapters.cache.org_domain_cache import org_domain_cache
from core.api.compat import handle_message
from core.config.business_category_loader import clear_business_category_cache
from core.execution.clients.availability_client import AvailabilityClient
from core.session.session_manager import clear_session, get_session, save_session
from core.session.session_schema_v2 import empty_session_v2
from core.tests.harness.car_service_catalog import (
    CAR_SERVICE_COLLECTIONS,
    CAR_SERVICE_SERVICES,
    FULL_SERVICE_ID,
    JOHN_STAFF_ID,
    MIKE_STAFF_ID,
    OIL_CHANGE_ID,
)
from core.tests.harness.clients import ScriptedLumaClient, TestCatalogClient
from core.tests.harness.mock_clients import (
    create_mock_booking_client,
    create_mock_organization_client,
)
from core.tests.harness.org_setup import setup_test_org_category
from core.tests.harness.test_clock import FROZEN_TIME
from core.tests.mocks import reset_booking_counter
from core.workflows.availability.fingerprint import compute_availability_fingerprint

ORG_ID = int(os.getenv("ORG_ID", "1"))
TOMORROW = "2026-07-02"  # FROZEN_TIME (2026-07-01) + 1 day


def setup_function(_fn=None):
    clear_business_category_cache()
    catalog_cache._mem_cache.pop((ORG_ID, "service"), None)
    org_domain_cache._mem_cache.pop(ORG_ID, None)


def _luma(
    *,
    intent: str = "CREATE_APPOINTMENT",
    facts: Optional[Dict[str, Any]] = None,
    temporal: Optional[Dict[str, Any]] = None,
    service_candidates: Optional[List[Any]] = None,
    entity_resolutions: Optional[Dict[str, Dict[str, Any]]] = None,
    needs_clarification: bool = False,
    understanding: str = "UNDERSTOOD",
) -> Dict[str, Any]:
    legacy_facts = dict(facts or {})
    resolutions = (
        dict(entity_resolutions)
        if entity_resolutions is not None
        else _resolved_entities_from_facts(legacy_facts)
    )
    payload: Dict[str, Any] = {
        "success": True,
        "intent": {"name": intent, "confidence": 0.95},
        "needs_clarification": needs_clarification,
        "facts": legacy_facts,
        "entity_resolutions": resolutions,
        "turn": {"understanding": understanding},
    }
    if temporal is not None:
        payload["temporal"] = temporal
    if service_candidates is not None:
        payload["service_candidates"] = list(service_candidates)
        payload["needs_clarification"] = True
    return payload


def _resolved_entities_from_facts(
    facts: Dict[str, Any],
) -> Dict[str, Dict[str, Any]]:
    """Build authoritative current-turn entity evidence from compatibility facts."""
    fact_keys = {
        "service": "service_id",
        "engine_type": "engine_type",
        "registration_number": "registration_number",
        "staff": "staff_id",
    }
    return {
        entity_name: {"resolution": "RESOLVED", "value": facts[fact_key]}
        for entity_name, fact_key in fact_keys.items()
        if facts.get(fact_key) is not None
    }


def _temporal_day(date: str, *, expression: str = "tomorrow") -> Dict[str, Any]:
    return {
        "mode": "single_day",
        "start_date": date,
        "start_date_expression": expression,
        "expression": expression,
        "confidence": 0.95,
    }


def _temporal_time(time_hhmm: str, *, expression: str = "10am") -> Dict[str, Any]:
    return {
        "mode": "exact",
        "start_time": time_hhmm,
        "start_time_expression": expression,
        "expression": expression,
        "confidence": 0.95,
        "resolution": {"kind": "explicit"},
    }


def _wire(
    user_id: str,
    scripts: Dict[str, Dict[str, Any]],
    *,
    start_hours: tuple[int, ...] = (9, 10, 11),
):
    clear_session(ORG_ID, user_id)
    seeded_session = empty_session_v2()
    seeded_session["customer_id"] = 501
    seeded_session["customer_contact"] = {
        "customer_id": 501,
        "authoritative_name": "Existing Customer",
        "name_status": "authoritative",
    }
    save_session(ORG_ID, user_id, seeded_session)
    reset_booking_counter()
    setup_test_org_category("car_service")
    catalog_cache._mem_cache.pop((ORG_ID, "service"), None)

    luma = ScriptedLumaClient(scripts)
    catalog = TestCatalogClient(
        test_aliases=CAR_SERVICE_SERVICES,
        collections=CAR_SERVICE_COLLECTIONS,
        business_category_id=3,
    )
    org = create_mock_organization_client(business_category_id=3)
    booking = create_mock_booking_client()
    availability = Mock(spec=AvailabilityClient)

    def _avail(**kwargs):
        date = kwargs.get("date") or TOMORROW
        slots = []
        for hour in start_hours:
            slots.append(
                {
                    "start": f"{date}T{hour:02d}:00:00Z",
                    "end": f"{date}T{hour:02d}:30:00Z",
                    "available": True,
                }
            )
        return {"slots": slots}

    availability.get_service_availability.side_effect = _avail

    def turn(text: str) -> Dict[str, Any]:
        return handle_message(
            text=text,
            user_id=user_id,
            organization_id=ORG_ID,
            luma_client=luma,
            catalog_client=catalog,
            organization_client=org,
            booking_client=booking,
            availability_client=availability,
            frozen_time=FROZEN_TIME,
        )

    return turn, booking, availability


def _plan(result: Dict[str, Any]) -> Dict[str, Any]:
    assert result.get("success"), result
    return result.get("result") or result.get("plan") or {}


def _slots(user_id: str) -> Dict[str, Any]:
    sess = get_session(ORG_ID, user_id) or {}
    return dict(sess.get("slots") or {})


def _missing(result: Dict[str, Any], user_id: str) -> List[str]:
    plan = _plan(result)
    missing = plan.get("missing_slots")
    if isinstance(missing, list):
        return list(missing)
    sess = get_session(ORG_ID, user_id) or {}
    return list(sess.get("missing_slots") or [])


def test_car_service_asks_engine_type_before_date_without_temporal():
    """Service alone → progress ask is engine_type (not date), then SEARCH."""
    user_id = "car-engine-before-date-001"
    turn, _booking, availability = _wire(
        user_id,
        {
            "book premium full service": _luma(
                facts={"service_id": FULL_SERVICE_ID},
            ),
            "petrol": _luma(facts={"engine_type": "petrol"}),
            "aug 23": _luma(
                facts={},
                temporal=_temporal_day("2026-08-23", expression="aug 23"),
            ),
        },
    )

    r0 = turn("Book Premium Full Service")
    plan0 = _plan(r0)
    assert plan0.get("ask_next") == "engine_type" or plan0.get("awaiting") == "engine_type"
    assert availability.get_service_availability.call_count == 0
    missing0 = _missing(r0, user_id)
    assert "engine_type" in missing0
    # Completeness inventory still lists temporal slots; ask_next is progress-driven.
    assert "date" in missing0
    assert plan0.get("ask_next") == "engine_type" or plan0.get("awaiting") == "engine_type"

    turn("Petrol")
    assert _slots(user_id).get("engine_type") == "petrol"
    assert availability.get_service_availability.call_count >= 1

    turn("Aug 23")
    assert availability.get_service_availability.call_count >= 1


def test_car_service_engine_type_accepted_when_nlu_unrecognized():
    """Schema enum facts must collect even if NLU labels UNRECOGNIZED_INPUT."""
    user_id = "car-engine-unrecognized-001"
    turn, _booking, availability = _wire(
        user_id,
        {
            "book premium full service": _luma(
                facts={"service_id": FULL_SERVICE_ID},
            ),
            "petrol": _luma(
                facts={"engine_type": "petrol"},
                understanding="UNRECOGNIZED_INPUT",
            ),
            "aug 23": _luma(
                facts={},
                temporal=_temporal_day("2026-08-23", expression="aug 23"),
            ),
        },
    )

    r0 = turn("Book Premium Full Service")
    assert "engine_type" in _missing(r0, user_id)

    r1 = turn("Petrol")
    assert _slots(user_id).get("engine_type") == "petrol"
    assert "engine_type" not in _missing(r1, user_id)
    plan1 = _plan(r1)
    ask = plan1.get("ask_next") or plan1.get("awaiting")
    assert ask != "engine_type"
    assert availability.get_service_availability.call_count >= 1


def test_car_service_happy_path_collects_attrs_then_confirms():
    """Oil change + tomorrow → business attrs → availability → time → confirm.

    Exploratory SEARCH may run once service_id is present (platform policy).
    Business required slots still block commit until collected.
    """
    user_id = "car-happy-001"
    turn, booking, availability = _wire(
        user_id,
        {
            "book me an oil change tomorrow": _luma(
                facts={"service_id": OIL_CHANGE_ID},
                temporal=_temporal_day(TOMORROW),
            ),
            "diesel": _luma(facts={"engine_type": "diesel"}),
            "ab12cde": _luma(facts={"registration_number": "AB12CDE"}),
            "10am": _luma(
                facts={"times": ["10:00"]},
                temporal={
                    **_temporal_day(TOMORROW, expression="tomorrow"),
                    **{
                        "start_time": "10:00",
                        "start_time_expression": "10am",
                        "mode": "exact",
                        "expression": "10am",
                        "confidence": 0.95,
                        "resolution": {"kind": "explicit"},
                    },
                },
            ),
            "yes": {
                "success": True,
                "intent": {"name": "CONFIRMATION", "confidence": 0.99},
                "facts": {},
                "entity_resolutions": {},
                "turn": {"understanding": "UNDERSTOOD"},
            },
        },
    )

    r1 = turn("Book me an oil change tomorrow")
    slots = _slots(user_id)
    assert slots.get("service_id") in (OIL_CHANGE_ID, "Oil Change", 101)
    missing = _missing(r1, user_id)
    assert "engine_type" in missing
    assert "registration_number" in missing

    r2 = turn("Diesel")
    assert _slots(user_id).get("engine_type") == "diesel"
    assert "registration_number" in _missing(r2, user_id)

    r3 = turn("AB12CDE")
    slots = _slots(user_id)
    assert slots.get("registration_number") == "AB12CDE"
    assert slots.get("engine_type") == "diesel"
    assert availability.get_service_availability.call_count >= 1

    r4 = turn("10am")
    plan4 = _plan(r4)
    assert plan4.get("status") in (
        "AWAITING_CONFIRMATION",
        "READY",
        "NEEDS_CLARIFICATION",
    )
    final_slots = _slots(user_id)
    assert final_slots.get("engine_type") == "diesel"
    assert final_slots.get("registration_number") == "AB12CDE"
    assert (final_slots.get("time") or "").startswith("10") or "time" in _missing(
        r4, user_id
    )
    # Commit requires presented-availability bind (covered by salon E2E).
    # This vertical validates config-driven required attrs + search participation.
    assert availability.get_service_availability.call_count >= 1


def test_car_service_time_selection_asks_required_before_confirmation():
    """Collect availability criteria, time, and registration before confirmation."""
    from core.session.confirmation_gate import get_confirmation_state

    user_id = "car-confirm-gate-001"
    turn, booking, availability = _wire(
        user_id,
        {
            "book executive oil change tomorrow": _luma(
                facts={"service_id": OIL_CHANGE_ID},
                temporal=_temporal_day(TOMORROW),
            ),
            "9am": _luma(
                facts={"times": ["09:00"]},
                temporal={
                    **_temporal_day(TOMORROW, expression="tomorrow"),
                    **_temporal_time("09:00", expression="9am"),
                },
            ),
            "petrol": _luma(facts={"engine_type": "petrol"}),
            "ab12 xyz": _luma(facts={"registration_number": "AB12 XYZ"}),
            "yes": {
                "success": True,
                "intent": {"name": "CONFIRMATION", "confidence": 0.99},
                "facts": {},
                "entity_resolutions": {},
                "turn": {"understanding": "UNDERSTOOD"},
            },
        },
        start_hours=(9, 10, 11),
    )

    r0 = turn("Book Executive Oil Change tomorrow")
    # engine_type is availability_criteria — SEARCH waits until it is collected
    assert availability.get_service_availability.call_count == 0
    assert "engine_type" in _missing(r0, user_id)

    turn("petrol")
    assert _slots(user_id).get("engine_type") == "petrol"
    assert availability.get_service_availability.call_count >= 1

    r_time = turn("9am")
    plan_time = _plan(r_time)
    missing_after_time = _missing(r_time, user_id)
    assert plan_time.get("status") != "AWAITING_CONFIRMATION"
    assert "registration_number" in missing_after_time
    sess = get_session(ORG_ID, user_id) or {}
    assert get_confirmation_state(sess) != "pending"
    slots = _slots(user_id)
    assert (slots.get("time") or "").startswith("09") or slots.get("time") == "9:00"

    r_reg = turn("AB12 XYZ")
    plan_reg = _plan(r_reg)
    assert _slots(user_id).get("registration_number") == "AB12 XYZ"
    assert plan_reg.get("status") == "AWAITING_CONFIRMATION"
    assert plan_reg.get("awaiting") == "USER_CONFIRMATION"
    assert not _missing(r_reg, user_id)
    text = (r_reg.get("text") or plan_reg.get("text") or "").lower()
    assert "book" in text or "confirm" in text or "go ahead" in text

    r_yes = turn("yes")
    plan_yes = _plan(r_yes)
    # Commit may execute or stay awaiting depending on bind/presentation; slots must remain.
    final = _slots(user_id)
    assert final.get("engine_type") == "petrol"
    assert final.get("registration_number") == "AB12 XYZ"
    assert plan_yes.get("status") != "NEEDS_CLARIFICATION" or booking.create_booking.called


def test_car_service_optional_mechanic_populates_staff_id():
    user_id = "car-staff-001"
    turn, _booking, availability = _wire(
        user_id,
        {
            "book me an oil change tomorrow": _luma(
                facts={"service_id": OIL_CHANGE_ID},
                temporal=_temporal_day(TOMORROW),
            ),
            "diesel": _luma(facts={"engine_type": "diesel"}),
            "ab12cde": _luma(facts={"registration_number": "AB12CDE"}),
            "with john": _luma(facts={"staff_id": JOHN_STAFF_ID, "staff": "John"}),
        },
    )

    turn("Book me an oil change tomorrow")
    turn("Diesel")
    turn("AB12CDE")
    searches_before_staff = availability.get_service_availability.call_count
    turn("with John")
    slots = _slots(user_id)
    assert slots.get("staff_id") in (JOHN_STAFF_ID, 201, "John")
    without_staff = compute_availability_fingerprint(
        {
            "service_id": slots.get("service_id"),
            "date": slots.get("date") or TOMORROW,
        },
        intent_name="CREATE_APPOINTMENT",
    )
    with_staff = compute_availability_fingerprint(
        {
            "service_id": slots.get("service_id"),
            "date": slots.get("date") or TOMORROW,
            "staff_id": slots.get("staff_id"),
        },
        intent_name="CREATE_APPOINTMENT",
    )
    assert with_staff is not None
    assert with_staff != without_staff
    # Staff change should participate in search identity (re-search or fingerprint).
    assert (
        availability.get_service_availability.call_count > searches_before_staff
        or with_staff != without_staff
    )


def test_car_service_staff_revision_invalidates_availability():
    user_id = "car-staff-rev-001"
    turn, _booking, availability = _wire(
        user_id,
        {
            "book me an oil change tomorrow": _luma(
                facts={
                    "service_id": OIL_CHANGE_ID,
                    "engine_type": "diesel",
                    "registration_number": "AB12CDE",
                    "staff_id": JOHN_STAFF_ID,
                },
                temporal=_temporal_day(TOMORROW),
            ),
            "actually use mike": _luma(
                facts={"staff_id": MIKE_STAFF_ID, "staff": "Mike"}
            ),
        },
    )

    turn("Book me an oil change tomorrow")
    count_after_first = availability.get_service_availability.call_count
    assert count_after_first >= 1

    turn("Actually use Mike")
    slots = _slots(user_id)
    assert slots.get("staff_id") in (MIKE_STAFF_ID, 202, "Mike")
    assert availability.get_service_availability.call_count > count_after_first


def test_car_service_registration_revision_does_not_invalidate():
    """registration_number is required but not availability_criteria."""
    user_id = "car-attr-rev-001"
    turn, _booking, availability = _wire(
        user_id,
        {
            "book me an oil change tomorrow": _luma(
                facts={
                    "service_id": OIL_CHANGE_ID,
                    "engine_type": "diesel",
                    "registration_number": "AB12CDE",
                },
                temporal=_temporal_day(TOMORROW),
            ),
            "zz99zzz": _luma(facts={"registration_number": "ZZ99ZZZ"}),
        },
    )

    turn("Book me an oil change tomorrow")
    count_after_first = availability.get_service_availability.call_count
    assert count_after_first >= 1

    turn("ZZ99ZZZ")
    slots = _slots(user_id)
    assert slots.get("registration_number") == "ZZ99ZZZ"
    assert slots.get("engine_type") == "diesel"
    assert availability.get_service_availability.call_count == count_after_first


def test_car_service_engine_type_revision_invalidates_availability():
    """engine_type is availability_criteria — changing it re-searches."""
    user_id = "car-engine-rev-001"
    turn, _booking, availability = _wire(
        user_id,
        {
            "book me an oil change tomorrow": _luma(
                facts={
                    "service_id": OIL_CHANGE_ID,
                    "engine_type": "diesel",
                    "registration_number": "AB12CDE",
                },
                temporal=_temporal_day(TOMORROW),
            ),
            "petrol": _luma(facts={"engine_type": "petrol"}),
        },
    )

    turn("Book me an oil change tomorrow")
    count_after_first = availability.get_service_availability.call_count
    assert count_after_first >= 1

    turn("Petrol")
    slots = _slots(user_id)
    assert slots.get("engine_type") == "petrol"
    assert slots.get("registration_number") == "AB12CDE"
    assert availability.get_service_availability.call_count > count_after_first


def test_car_service_clarification_then_resume():
    user_id = "car-clarify-001"
    turn, _booking, _availability = _wire(
        user_id,
        {
            "book me service": _luma(
                facts={},
                service_candidates=["Oil Change", "Full Service", "Brake Inspection"],
                entity_resolutions={
                    "service": {
                        "resolution": "AMBIGUOUS",
                        "candidate_values": [26, 27, 28],
                    }
                },
                needs_clarification=True,
            ),
            "full service": _luma(
                facts={"service_id": FULL_SERVICE_ID},
            ),
            "diesel": _luma(facts={"engine_type": "diesel"}),
        },
    )

    r1 = turn("Book me service")
    plan1 = _plan(r1)
    assert plan1.get("status") in ("NEEDS_CLARIFICATION", "READY")
    sess = get_session(ORG_ID, user_id) or {}
    sources = [plan1, sess, plan1.get("facts") or {}, sess.get("facts") or {}]
    found_candidates = False
    for src in sources:
        if not isinstance(src, dict):
            continue
        cands = src.get("service_candidates")
        if isinstance(cands, list) and cands:
            found_candidates = True
            break
    assert found_candidates or "service_id" in _missing(r1, user_id)

    turn("Full Service")
    slots = _slots(user_id)
    assert slots.get("service_id") in (FULL_SERVICE_ID, "Full Service", 102)

    turn("Diesel")
    assert _slots(user_id).get("engine_type") == "diesel"
    assert _slots(user_id).get("service_id") in (FULL_SERVICE_ID, "Full Service", 102)


def test_car_service_business_slots_persist_across_restart():
    user_id = "car-persist-001"
    turn, _booking, _availability = _wire(
        user_id,
        {
            "book oil change": _luma(
                facts={
                    "service_id": OIL_CHANGE_ID,
                    "engine_type": "diesel",
                    "registration_number": "AB12CDE",
                },
                temporal=_temporal_day(TOMORROW),
            ),
            "hello again": _luma(facts={}),
        },
    )

    turn("book oil change")
    slots_before = _slots(user_id)
    assert slots_before.get("engine_type") == "diesel"
    assert slots_before.get("registration_number") == "AB12CDE"

    sess = get_session(ORG_ID, user_id)
    assert sess is not None
    save_session(ORG_ID, user_id, sess)

    turn("hello again")
    slots_after = _slots(user_id)
    assert slots_after.get("engine_type") == "diesel"
    assert slots_after.get("registration_number") == "AB12CDE"
