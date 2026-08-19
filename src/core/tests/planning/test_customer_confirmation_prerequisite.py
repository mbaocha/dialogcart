"""Customer identification protects every confirmation-entry route."""

from core.customer_identification import customer_name_confirmation_prerequisite
from core.planning.pipeline.decision_finalization import (
    TimeResolutionEvidence,
    finalize_decision_after_time_resolution,
)
from core.planning.pipeline.stage06_confirmation import (
    _maybe_enter_booking_confirmation_pending,
)
from core.session.confirmation_gate import get_confirmation_state
from core.planning.time_resolution import TIME_MATCH_EXACT


def _bound_slots():
    return {
        "service_id": 1001,
        "date": "2026-08-20",
        "time": "10:00",
        "has_datetime": True,
    }


def test_existing_authoritative_customer_name_is_ready():
    evidence = customer_name_confirmation_prerequisite({
        "customer_id": 91,
        "customer_contact": {
            "customer_id": 91,
            "authoritative_name": "Persisted Name",
            "name_status": "authoritative",
        },
    })
    assert evidence.satisfied is True
    assert evidence.required_input is None


def test_existing_customer_without_authoritative_name_is_not_ready():
    evidence = customer_name_confirmation_prerequisite({"customer_id": 91})
    assert evidence.satisfied is False
    assert evidence.required_input == "CUSTOMER_CONTACT_NAME"


def test_malformed_contact_is_not_authoritative_even_with_customer_id():
    for contact in (
        {"customer_id": 91, "authoritative_name": "Persisted Name"},
        {
            "customer_id": 92,
            "authoritative_name": "Persisted Name",
            "name_status": "authoritative",
        },
        {
            "customer_id": 91,
            "authoritative_name": "Guest",
            "name_status": "authoritative",
        },
    ):
        evidence = customer_name_confirmation_prerequisite({
            "customer_id": 91,
            "customer_contact": contact,
        })
        assert evidence.satisfied is False


def test_planning_time_confirmation_does_not_enter_without_customer_name():
    payload = {"slots": _bound_slots(), "_effective_collected_slots": _bound_slots()}
    prerequisite = customer_name_confirmation_prerequisite({"customer_id": 91})
    state = _maybe_enter_booking_confirmation_pending(
        "CREATE_APPOINTMENT",
        payload,
        missing_slots=[],
        needs_clarification=False,
        availability_ready=True,
        confirmation_state=None,
        session_state={"customer_id": 91},
        customer_name_prerequisite=prerequisite,
    )
    assert state is None
    assert get_confirmation_state(payload) is None


def test_post_availability_confirmation_routes_to_customer_name_first():
    plan = {
        "status": "READY",
        "stage": "AVAILABILITY",
        "action": "SEARCH_AVAILABILITY",
        "missing_slots": [],
        "_customer_name_prerequisite": {
            "satisfied": False,
            "required_input": "CUSTOMER_CONTACT_NAME",
        },
        "_merged_luma_response": {},
    }
    finalize_decision_after_time_resolution(
        plan,
        evidence=TimeResolutionEvidence(
            outcome=TIME_MATCH_EXACT,
            time_resolution={"outcome": TIME_MATCH_EXACT},
            bind_result={
                "slots": _bound_slots(),
                "resolved_datetime_range": {
                    "start": "2026-08-20T10:00:00+00:00",
                    "end": "2026-08-20T11:00:00+00:00",
                },
            },
        ),
    )
    assert plan["status"] == "NEEDS_CLARIFICATION"
    assert plan["awaiting"] == "CUSTOMER_CONTACT_NAME"
    assert plan["action"] is None
    assert get_confirmation_state(plan) is None
