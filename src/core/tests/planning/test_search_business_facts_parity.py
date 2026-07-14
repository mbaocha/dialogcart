"""Parity tests: SEARCH_AVAILABILITY driven by availability_check_required business fact."""

from core.workflows.availability.fingerprint import (
    build_availability_fingerprint_slots,
    compute_availability_fingerprint,
)
from core.planning.facts import build_policy_execution_flags
from core.policy.intent_policy import select_next_execution_step


def _availability_cache_session(
    slots,
    *,
    intent_name="CREATE_APPOINTMENT",
    organization_id=None,
    session_state=None,
    luma_response=None,
):
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


def _flags(
    intent_name,
    slots,
    session_state=None,
    availability_resolved=False,
    confirmation_state=None,
    **kwargs,
):
    return build_policy_execution_flags(
        intent_name=intent_name,
        slots=slots,
        session_state=session_state,
        luma_response=kwargs.get("luma_response", {}),
        missing_slots=kwargs.get("missing_slots", []),
        needs_clarification=kwargs.get("needs_clarification", False),
        availability_resolved=availability_resolved,
        confirmation_state=confirmation_state,
    )


def _select(intent_name, slots, **kwargs):
    flags = _flags(intent_name, slots, **kwargs)
    return select_next_execution_step(intent_name, slots, flags), flags


class TestSearchBusinessFactParity:
    def test_first_booking_selects_search(self):
        slots = {"service_id": "svc-haircut", "organization_id": "org-1"}
        step, flags = _select("CREATE_APPOINTMENT", slots)
        assert flags["availability_check_required"] is True
        assert step is not None
        assert step["action"] == "SEARCH_AVAILABILITY"

    def test_valid_availability_skips_search(self):
        slots = {
            "service_id": "svc-haircut",
            "date": "2026-07-10",
            "organization_id": "org-1",
        }
        session = _availability_cache_session(slots)
        step, flags = _select(
            "CREATE_APPOINTMENT",
            slots,
            session_state=session,
            availability_resolved=True,
        )
        assert flags["availability_check_required"] is False
        assert step is None or step["action"] != "SEARCH_AVAILABILITY"

    def test_service_change_reselects_search(self):
        original = {
            "service_id": "svc-haircut",
            "date": "2026-07-10",
            "organization_id": "org-1",
        }
        session = _availability_cache_session(original, intent_name="CREATE_APPOINTMENT")
        revised = {**original, "service_id": "svc-spa"}
        step, flags = _select(
            "CREATE_APPOINTMENT",
            revised,
            session_state=session,
            availability_resolved=False,
        )
        assert flags["availability_check_required"] is True
        assert step is not None
        assert step["action"] == "SEARCH_AVAILABILITY"

    def test_date_change_reselects_search(self):
        original = {
            "service_id": "svc-haircut",
            "date": "2026-07-10",
            "organization_id": "org-1",
        }
        session = _availability_cache_session(original, intent_name="CREATE_APPOINTMENT")
        revised = {**original, "date": "2026-07-15"}
        step, flags = _select(
            "CREATE_APPOINTMENT",
            revised,
            session_state=session,
            availability_resolved=False,
        )
        assert flags["availability_check_required"] is True
        assert step is not None
        assert step["action"] == "SEARCH_AVAILABILITY"

    def test_unchanged_slots_after_search_do_not_reselect_search(self):
        slots = {
            "service_id": "svc-haircut",
            "date": "2026-07-10",
            "organization_id": "org-1",
        }
        session = _availability_cache_session(slots)
        step, flags = _select(
            "CREATE_APPOINTMENT",
            slots,
            session_state=session,
            availability_resolved=True,
        )
        assert flags["availability_check_required"] is False
        assert step is None or step["action"] != "SEARCH_AVAILABILITY"

    def test_bound_datetime_without_evidence_still_requires_search(self):
        slots = {
            "service_id": "svc-haircut",
            "date": "2026-07-10",
            "time": "14:00",
            "organization_id": "org-1",
        }
        step, flags = _select(
            "CREATE_APPOINTMENT",
            slots,
            availability_resolved=False,
        )
        assert flags["availability_check_required"] is True
        assert step is not None
        assert step["action"] == "SEARCH_AVAILABILITY"

    def test_create_reservation_search_uses_business_fact(self):
        slots = {
            "service_id": "svc-room",
            "date_range": {"start": "2026-07-10", "end": "2026-07-12"},
            "organization_id": "org-1",
        }
        step, flags = _select("CREATE_RESERVATION", slots)
        assert flags["availability_check_required"] is True
        assert step is not None
        assert step["action"] == "SEARCH_AVAILABILITY"

    def test_create_reservation_skips_search_when_availability_ready(self):
        slots = {
            "service_id": "svc-room",
            "date_range": {"start": "2026-07-10", "end": "2026-07-12"},
            "organization_id": "org-1",
        }
        session = _availability_cache_session(slots, intent_name="CREATE_RESERVATION")
        step, flags = _select(
            "CREATE_RESERVATION",
            slots,
            session_state=session,
            availability_resolved=True,
        )
        assert flags["availability_check_required"] is False
        assert step is not None
        assert step["action"] == "CREATE_BOOKING_HOLD"

    def test_modify_search_requires_business_fact_and_not_confirmed(self):
        slots = {"booking_id": "bk-1", "date": "2026-07-10"}
        step, flags = _select(
            "MODIFY_BOOKING",
            slots,
            availability_resolved=False,
            confirmation_state=None,
        )
        assert flags["availability_check_required"] is True
        assert step is not None
        assert step["action"] == "SEARCH_AVAILABILITY"

    def test_modify_skips_search_when_confirmed(self):
        slots = {
            "booking_id": "bk-1",
            "date": "2026-07-10",
            "time": "14:00",
        }
        session = _availability_cache_session(slots, intent_name="MODIFY_BOOKING")
        step, flags = _select(
            "MODIFY_BOOKING",
            slots,
            session_state=session,
            availability_resolved=True,
            confirmation_state="confirmed",
        )
        assert flags["availability_check_required"] is False
        assert step is not None
        assert step["action"] == "APPLY_MODIFICATION"

    def test_modify_still_selects_search_when_ready_but_not_confirmed(self):
        slots = {
            "booking_id": "bk-1",
            "date": "2026-07-10",
            "time": "14:00",
            "organization_id": "org-1",
        }
        session = _availability_cache_session(slots, intent_name="MODIFY_BOOKING")
        step, flags = _select(
            "MODIFY_BOOKING",
            slots,
            session_state=session,
            availability_resolved=True,
            confirmation_state=None,
        )
        assert flags["availability_ready"] is True
        assert flags["availability_check_required"] is True
        assert step is not None
        assert step["action"] == "SEARCH_AVAILABILITY"
