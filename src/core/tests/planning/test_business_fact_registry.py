"""Unit tests for planner business fact derivation."""

from core.workflows.availability.fingerprint import (
    build_availability_fingerprint_slots,
    compute_availability_fingerprint,
)
from core.planning.facts import BusinessFacts, PlanningFactContext, derive_business_facts


def _ctx(**kwargs) -> PlanningFactContext:
    defaults = {
        "intent_name": "CREATE_APPOINTMENT",
        "slots": {},
        "session_state": None,
        "luma_response": {},
        "missing_slots": [],
        "needs_clarification": False,
    }
    defaults.update(kwargs)
    return PlanningFactContext(**defaults)


def _fingerprint_for(slots):
    return compute_availability_fingerprint(slots, intent_name="CREATE_APPOINTMENT")


def _availability_cache_session(
    slots,
    *,
    intent_name="CREATE_APPOINTMENT",
    organization_id=None,
    session_state=None,
    luma_response=None,
):
    """Session with fingerprint + successful availability execution evidence."""
    session_state = dict(session_state or {})
    luma_response = luma_response or {}
    fp_slots = build_availability_fingerprint_slots(
        slots,
        intent_name=intent_name,
        organization_id=organization_id,
        luma_response=luma_response,
        session_state=session_state,
    )
    search_date = fp_slots.get("date")
    return {
        **session_state,
        "availability_fingerprint": compute_availability_fingerprint(
            fp_slots, intent_name=intent_name
        ),
        "last_execution_result": {
            "type": "availability",
            "status": "success",
            "slots": [
                {
                    "starts_at": f"{search_date or '2026-07-10'}T14:00:00Z",
                    "ends_at": f"{search_date or '2026-07-10'}T14:30:00Z",
                }
            ],
            "search_date": search_date,
        },
    }


class TestAvailabilityFacts:
    def test_first_booking_requires_availability_search(self):
        facts = derive_business_facts(
            _ctx(
                intent_name="CREATE_APPOINTMENT",
                slots={"service_id": "svc-haircut", "organization_id": "org-1"},
            )
        )
        assert facts.availability_check_required is True
        assert facts.availability_ready is False

    def test_no_service_yet_does_not_require_availability_search(self):
        facts = derive_business_facts(
            _ctx(
                intent_name="CREATE_APPOINTMENT",
                slots={"organization_id": "org-1"},
            )
        )
        assert facts.availability_check_required is False
        assert facts.availability_ready is False

    def test_after_successful_search_availability_check_not_required(self):
        slots = {
            "service_id": "svc-haircut",
            "date": "2026-07-10",
            "organization_id": "org-1",
        }
        session = _availability_cache_session(slots)
        facts = derive_business_facts(
            _ctx(
                intent_name="CREATE_APPOINTMENT",
                slots=slots,
                session_state=session,
            )
        )
        assert facts.availability_ready is True
        assert facts.availability_check_required is False

    def test_changing_service_requires_availability_search_again(self):
        original_slots = {
            "service_id": "svc-haircut",
            "date": "2026-07-10",
            "organization_id": "org-1",
        }
        session = _availability_cache_session(original_slots)
        revised_slots = {
            **original_slots,
            "service_id": "svc-spa",
        }
        facts = derive_business_facts(
            _ctx(
                intent_name="CREATE_APPOINTMENT",
                slots=revised_slots,
                session_state=session,
            )
        )
        assert facts.availability_ready is False
        assert facts.availability_check_required is True

    def test_changing_date_requires_availability_search_again(self):
        original_slots = {
            "service_id": "svc-haircut",
            "date": "2026-07-10",
            "organization_id": "org-1",
        }
        session = _availability_cache_session(original_slots)
        revised_slots = {
            **original_slots,
            "date": "2026-07-15",
        }
        facts = derive_business_facts(
            _ctx(
                intent_name="CREATE_APPOINTMENT",
                slots=revised_slots,
                session_state=session,
            )
        )
        assert facts.availability_ready is False
        assert facts.availability_check_required is True

    def test_bound_datetime_without_search_evidence_does_not_make_availability_ready(self):
        slots = {
            "service_id": "svc-haircut",
            "date": "2026-07-10",
            "time": "14:00",
            "organization_id": "org-1",
        }
        facts = derive_business_facts(
            _ctx(
                intent_name="CREATE_APPOINTMENT",
                slots=slots,
            )
        )
        assert facts.availability_ready is False
        assert facts.availability_check_required is True

    def test_proposals_without_search_evidence_require_availability_search(self):
        facts = derive_business_facts(
            _ctx(
                intent_name="CREATE_APPOINTMENT",
                slots={"service_id": "flexi haircut + prunning"},
                session_state={
                    "date_proposal": {"mode": "single_day", "start": "2026-07-08"},
                    "time_proposal": {"mode": "exact", "value": "09:00"},
                },
                luma_response={
                    "date_proposal": {"mode": "single_day", "start": "2026-07-08"},
                    "time_proposal": {"mode": "exact", "value": "09:00"},
                },
                missing_slots=[],
            )
        )
        assert facts.availability_ready is False
        assert facts.availability_check_required is True


class TestBookingIdentificationFacts:
    def test_booking_id_present_identification_not_required(self):
        facts = derive_business_facts(
            _ctx(
                intent_name="MODIFY_BOOKING",
                slots={"booking_id": "bk-123"},
            )
        )
        assert facts.booking_identified is True
        assert facts.booking_identification_required is False

    def test_missing_booking_id_requires_identification(self):
        facts = derive_business_facts(
            _ctx(
                intent_name="CANCEL_BOOKING",
                slots={},
            )
        )
        assert facts.booking_identified is False
        assert facts.booking_identification_required is True

    def test_create_appointment_does_not_require_booking_identification(self):
        facts = derive_business_facts(
            _ctx(
                intent_name="CREATE_APPOINTMENT",
                slots={"service_id": "svc-haircut"},
            )
        )
        assert facts.booking_identification_required is False


class TestConfirmationFacts:
    def test_confirmation_pending_requires_user_confirmation(self):
        session = {
            "booking": {"confirmation_state": "pending"},
        }
        facts = derive_business_facts(
            _ctx(
                intent_name="CREATE_APPOINTMENT",
                slots={
                    "service_id": "svc-haircut",
                    "date": "2026-07-10",
                    "time": "14:00",
                },
                session_state=session,
            )
        )
        assert facts.user_confirmation_required is True
        assert facts.user_confirmation_satisfied is False

    def test_confirmation_confirmed_satisfies_user_confirmation(self):
        session = {
            "booking": {"confirmation_state": "confirmed"},
        }
        facts = derive_business_facts(
            _ctx(
                intent_name="CREATE_APPOINTMENT",
                slots={
                    "service_id": "svc-haircut",
                    "date": "2026-07-10",
                    "time": "14:00",
                },
                session_state=session,
            )
        )
        assert facts.user_confirmation_satisfied is True
        assert facts.user_confirmation_required is False

    def test_gate_accept_continuation_satisfies_user_confirmation(self):
        """ACCEPT turn may satisfy confirmation before persisted state catches up."""
        session = {
            "booking": {"confirmation_state": "pending"},
        }
        facts = derive_business_facts(
            _ctx(
                intent_name="CREATE_APPOINTMENT",
                slots={
                    "service_id": "svc-haircut",
                    "date": "2026-07-10",
                    "time": "14:00",
                },
                session_state=session,
                luma_response={"_confirm_booking_continuation": True},
                confirmation_state="pending",
            )
        )
        assert facts.user_confirmation_satisfied is True
        assert facts.awaiting_user_confirmation is False

    def test_commit_ready_create_appointment_requires_user_confirmation(self):
        slots = {
            "service_id": "svc-haircut",
            "date": "2026-07-10",
            "time": "14:00",
            "organization_id": "org-1",
        }
        session = _availability_cache_session(slots)
        facts = derive_business_facts(
            _ctx(
                intent_name="CREATE_APPOINTMENT",
                slots=slots,
                session_state=session,
                missing_slots=[],
            )
        )
        assert facts.availability_ready is True
        assert facts.time_selection_ready is True
        assert facts.user_confirmation_required is True
        assert facts.user_confirmation_satisfied is False
        assert facts.awaiting_user_confirmation is True


class TestBookingHoldFacts:
    def test_reservation_hold_required_when_availability_ready_and_no_hold(self):
        slots = {
            "service_id": "svc-room",
            "date_range": {"start": "2026-07-10", "end": "2026-07-12"},
            "organization_id": "org-1",
        }
        session = _availability_cache_session(slots, intent_name="CREATE_RESERVATION")
        facts = derive_business_facts(
            _ctx(
                intent_name="CREATE_RESERVATION",
                slots=slots,
                session_state=session,
            )
        )
        assert facts.availability_ready is True
        assert facts.booking_hold_ready is False
        assert facts.booking_hold_required is True

    def test_reservation_hold_not_required_when_hold_exists(self):
        slots = {
            "service_id": "svc-room",
            "date_range": {"start": "2026-07-10", "end": "2026-07-12"},
            "booking_id": "hold-99",
            "organization_id": "org-1",
        }
        session = _availability_cache_session(slots, intent_name="CREATE_RESERVATION")
        facts = derive_business_facts(
            _ctx(
                intent_name="CREATE_RESERVATION",
                slots=slots,
                session_state=session,
            )
        )
        assert facts.booking_hold_ready is True
        assert facts.booking_hold_required is False


class TestTimeSelectionFacts:
    def test_appointment_without_bound_time_requires_time_selection(self):
        facts = derive_business_facts(
            _ctx(
                intent_name="CREATE_APPOINTMENT",
                slots={"service_id": "svc-haircut", "date": "2026-07-10"},
            )
        )
        assert facts.time_selection_required is True
        assert facts.time_selection_ready is False

    def test_appointment_with_bound_time_is_time_selection_ready(self):
        facts = derive_business_facts(
            _ctx(
                intent_name="CREATE_APPOINTMENT",
                slots={
                    "service_id": "svc-haircut",
                    "date": "2026-07-10",
                    "time": "14:00",
                },
            )
        )
        assert facts.time_selection_ready is True
        assert facts.time_selection_required is False

    def test_reservation_does_not_require_time_selection(self):
        facts = derive_business_facts(
            _ctx(
                intent_name="CREATE_RESERVATION",
                slots={
                    "service_id": "svc-room",
                    "date_range": {"start": "2026-07-10", "end": "2026-07-12"},
                },
            )
        )
        assert facts.time_selection_required is False


class TestModifyAvailabilityCheckFacts:
    def test_modify_still_requires_search_when_ready_but_not_confirmed(self):
        slots = {
            "booking_id": "bk-1",
            "date": "2026-07-10",
            "time": "14:00",
            "organization_id": "org-1",
        }
        session = _availability_cache_session(slots, intent_name="MODIFY_BOOKING")
        facts = derive_business_facts(
            _ctx(
                intent_name="MODIFY_BOOKING",
                slots=slots,
                session_state=session,
            )
        )
        assert facts.availability_ready is True
        assert facts.user_confirmation_satisfied is False
        assert facts.availability_check_required is True

    def test_modify_skips_search_when_confirmed(self):
        slots = {
            "booking_id": "bk-1",
            "date": "2026-07-10",
            "time": "14:00",
        }
        session = {"booking": {"confirmation_state": "confirmed"}}
        facts = derive_business_facts(
            _ctx(
                intent_name="MODIFY_BOOKING",
                slots=slots,
                session_state=session,
            )
        )
        assert facts.user_confirmation_satisfied is True
        assert facts.availability_check_required is False


class TestFingerprintOrganizationParity:
    def test_browse_turn_availability_ready_without_org_in_planning_slots(self):
        planning_slots = {"service_id": "svc-haircut"}
        proposal_session = {"date_proposal": {"mode": "single_day", "start": "2026-07-09"}}
        session_state = _availability_cache_session(
            planning_slots,
            intent_name="CREATE_APPOINTMENT",
            organization_id=1,
            session_state=proposal_session,
        )
        facts = derive_business_facts(
            _ctx(
                slots=planning_slots,
                session_state=session_state,
                luma_response={
                    "intent": {"name": "AVAILABILITY"},
                    "operation": "browse_next",
                },
                organization_id=1,
            )
        )
        assert facts.availability_ready is True
        assert facts.availability_check_required is False

    def test_service_change_still_requires_search(self):
        session_state = _availability_cache_session(
            {"service_id": "svc-haircut", "date": "2026-07-10"},
            intent_name="CREATE_APPOINTMENT",
            organization_id=1,
        )
        facts = derive_business_facts(
            _ctx(
                slots={"service_id": "svc-spa", "date": "2026-07-10"},
                session_state=session_state,
                organization_id=1,
            )
        )
        assert facts.availability_ready is False
        assert facts.availability_check_required is True

    def test_date_change_still_requires_search(self):
        session_state = _availability_cache_session(
            {"service_id": "svc-haircut", "date": "2026-07-10"},
            intent_name="CREATE_APPOINTMENT",
            organization_id=1,
        )
        facts = derive_business_facts(
            _ctx(
                slots={"service_id": "svc-haircut", "date": "2026-07-11"},
                session_state=session_state,
                organization_id=1,
            )
        )
        assert facts.availability_ready is False
        assert facts.availability_check_required is True


class TestBusinessFactsImmutability:
    def test_business_facts_is_frozen(self):
        facts = derive_business_facts(
            _ctx(slots={"service_id": "svc-haircut"})
        )
        assert isinstance(facts, BusinessFacts)
        try:
            facts.availability_check_required = False  # type: ignore[misc]
            raised = False
        except AttributeError:
            raised = True
        assert raised is True
