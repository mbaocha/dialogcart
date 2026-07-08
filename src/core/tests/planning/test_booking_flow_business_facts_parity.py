"""Parity tests: booking-flow steps driven by business facts in policy."""

from core.orchestration.availability_fingerprint import (
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


def _flags(intent_name, slots, **kwargs):
    return build_policy_execution_flags(
        intent_name=intent_name,
        slots=slots,
        session_state=kwargs.get("session_state"),
        luma_response=kwargs.get("luma_response", {}),
        missing_slots=kwargs.get("missing_slots", []),
        needs_clarification=kwargs.get("needs_clarification", False),
        availability_resolved=kwargs.get("availability_resolved", False),
        confirmation_state=kwargs.get("confirmation_state"),
    )


def _select(intent_name, slots, **kwargs):
    flags = _flags(intent_name, slots, **kwargs)
    return select_next_execution_step(intent_name, slots, flags), flags


class TestFetchBookingBusinessFacts:
    def test_cancel_without_booking_id_selects_fetch(self):
        step, flags = _select("CANCEL_BOOKING", {})
        assert flags["booking_identification_required"] is True
        assert step is not None
        assert step["action"] == "FETCH_BOOKING"

    def test_cancel_with_booking_id_skips_fetch(self):
        step, flags = _select("CANCEL_BOOKING", {"booking_id": "bk-1"})
        assert flags["booking_identified"] is True
        assert flags["booking_identification_required"] is False
        assert step is not None
        assert step["action"] == "CONFIRM_CANCELLATION"

    def test_modify_without_booking_id_selects_fetch(self):
        step, flags = _select("MODIFY_BOOKING", {"date": "2026-07-10"})
        assert flags["booking_identification_required"] is True
        assert step is not None
        assert step["action"] == "FETCH_BOOKING"


class TestCreateBookingHoldBusinessFacts:
    def test_hold_selected_when_required(self):
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
        assert flags["booking_hold_required"] is True
        assert step is not None
        assert step["action"] == "CREATE_BOOKING_HOLD"

    def test_hold_skipped_when_hold_exists(self):
        slots = {
            "service_id": "svc-room",
            "date_range": {"start": "2026-07-10", "end": "2026-07-12"},
            "booking_id": "hold-99",
            "booking_code": "ABC123",
            "organization_id": "org-1",
        }
        session = _availability_cache_session(slots, intent_name="CREATE_RESERVATION")
        step, flags = _select(
            "CREATE_RESERVATION",
            slots,
            session_state=session,
            availability_resolved=True,
        )
        assert flags["booking_hold_required"] is False
        assert flags["booking_hold_ready"] is True
        assert step is not None
        assert step["action"] == "FINALIZE_RESERVATION"


class TestConfirmAppointmentBusinessFacts:
    def test_confirm_not_selected_when_pending(self):
        slots = {
            "service_id": "svc-haircut",
            "date": "2026-07-10",
            "time": "14:00",
            "organization_id": "org-1",
        }
        session = {
            **_availability_cache_session(slots, intent_name="CREATE_APPOINTMENT"),
            "booking": {"confirmation_state": "pending"},
        }
        step, flags = _select(
            "CREATE_APPOINTMENT",
            slots,
            session_state=session,
            availability_resolved=True,
            confirmation_state="pending",
        )
        assert flags["user_confirmation_satisfied"] is False
        assert step is None or step["action"] != "CONFIRM_APPOINTMENT"

    def test_confirm_selected_when_confirmed(self):
        slots = {
            "service_id": "svc-haircut",
            "date": "2026-07-10",
            "time": "14:00",
            "organization_id": "org-1",
        }
        session = {
            **_availability_cache_session(slots, intent_name="CREATE_APPOINTMENT"),
            "booking": {"confirmation_state": "confirmed"},
        }
        step, flags = _select(
            "CREATE_APPOINTMENT",
            slots,
            session_state=session,
            availability_resolved=True,
            confirmation_state="confirmed",
        )
        assert flags["user_confirmation_satisfied"] is True
        assert flags["availability_ready"] is True
        assert flags["time_selection_ready"] is True
        assert step is not None
        assert step["action"] == "CONFIRM_APPOINTMENT"


class TestApplyModificationBusinessFacts:
    def test_apply_not_selected_until_confirmed(self):
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
            confirmation_state=None,
        )
        assert flags["user_confirmation_satisfied"] is False
        assert step is not None
        assert step["action"] == "SEARCH_AVAILABILITY"

    def test_apply_selected_when_confirmed(self):
        slots = {
            "booking_id": "bk-1",
            "date": "2026-07-10",
            "time": "14:00",
        }
        session = {
            **_availability_cache_session(slots, intent_name="MODIFY_BOOKING"),
            "booking": {"confirmation_state": "confirmed"},
        }
        step, flags = _select(
            "MODIFY_BOOKING",
            slots,
            session_state=session,
            availability_resolved=True,
            confirmation_state="confirmed",
        )
        assert flags["user_confirmation_satisfied"] is True
        assert step is not None
        assert step["action"] == "APPLY_MODIFICATION"


class TestExecutionOnlyPlanAction:
    """plan.action is policy-selected execution only; presentation uses status/stage/awaiting."""

    def test_pending_confirmation_has_null_execution_action(self):
        from core.planning.orchestration.plan_builder import build_decision_plan

        slots = {
            "service_id": "svc-haircut",
            "date": "2026-07-10",
            "time": "14:00",
            "organization_id": "org-1",
        }
        session = {
            **_availability_cache_session(slots, intent_name="CREATE_APPOINTMENT"),
            "booking": {"confirmation_state": "pending"},
        }
        luma_response = {
            "intent": {"name": "CREATE_APPOINTMENT"},
            "slots": slots,
            "_effective_collected_slots": slots,
            "missing_slots": [],
            "needs_clarification": False,
            "booking": {"confirmation_state": "pending"},
        }
        plan = build_decision_plan(
            intent_name="CREATE_APPOINTMENT",
            luma_response=luma_response,
            domain="service",
            availability_resolved=True,
            session_state=session,
        )
        assert plan["status"] == "AWAITING_CONFIRMATION"
        assert plan["awaiting"] == "USER_CONFIRMATION"
        assert plan["stage"] == "CONFIRM"
        assert plan["action"] is None

    def test_confirmed_selects_commit_execution_action(self):
        from core.planning.orchestration.plan_builder import build_decision_plan

        slots = {
            "service_id": "svc-haircut",
            "date": "2026-07-10",
            "time": "14:00",
            "organization_id": "org-1",
        }
        session = {
            **_availability_cache_session(slots, intent_name="CREATE_APPOINTMENT"),
            "booking": {"confirmation_state": "confirmed"},
        }
        luma_response = {
            "intent": {"name": "CREATE_APPOINTMENT"},
            "slots": slots,
            "_effective_collected_slots": slots,
            "missing_slots": [],
            "needs_clarification": False,
            "booking": {"confirmation_state": "confirmed"},
        }
        plan = build_decision_plan(
            intent_name="CREATE_APPOINTMENT",
            luma_response=luma_response,
            domain="service",
            availability_resolved=True,
            session_state=session,
        )
        assert plan["status"] == "READY"
        assert plan["action"] == "CONFIRM_APPOINTMENT"
        assert plan["stage"] == "CONFIRM"


class TestTimeMatchExactConfirmCommit:
    """TIME_MATCH_EXACT must not block commit after the user accepts confirmation."""

    def _slots(self):
        return {
            "service_id": "svc-haircut",
            "date": "2026-07-10",
            "time": "12:00",
            "organization_id": "org-1",
        }

    def test_exact_match_pending_turn_suppresses_commit(self):
        from core.orchestration.time_resolution import TIME_MATCH_EXACT
        from core.planning.orchestration.plan_builder import build_decision_plan

        slots = self._slots()
        session = {
            **_availability_cache_session(slots, intent_name="CREATE_APPOINTMENT"),
            "booking": {"confirmation_state": "pending"},
            "resolved_datetime_range": {
                "start": "2026-07-10T12:00:00Z",
                "end": "2026-07-10T12:30:00Z",
            },
        }
        luma_response = {
            "intent": {"name": "CREATE_APPOINTMENT"},
            "slots": slots,
            "_effective_collected_slots": slots,
            "missing_slots": [],
            "needs_clarification": False,
            "booking": {"confirmation_state": "pending"},
            "time_match_outcome": TIME_MATCH_EXACT,
            "time_resolution": {"outcome": TIME_MATCH_EXACT, "requested_time": "12:00"},
        }
        plan = build_decision_plan(
            intent_name="CREATE_APPOINTMENT",
            luma_response=luma_response,
            domain="service",
            availability_resolved=True,
            session_state=session,
        )
        assert plan["status"] == "AWAITING_CONFIRMATION"
        assert plan["action"] is None
        assert plan["awaiting"] == "USER_CONFIRMATION"

    def test_exact_match_confirmed_turn_executes_commit(self):
        from core.orchestration.time_resolution import TIME_MATCH_EXACT
        from core.planning.orchestration.plan_builder import build_decision_plan

        slots = self._slots()
        session = {
            **_availability_cache_session(slots, intent_name="CREATE_APPOINTMENT"),
            "booking": {"confirmation_state": "confirmed"},
            "resolved_datetime_range": {
                "start": "2026-07-10T12:00:00Z",
                "end": "2026-07-10T12:30:00Z",
            },
        }
        luma_response = {
            "intent": {"name": "CREATE_APPOINTMENT"},
            "slots": slots,
            "_effective_collected_slots": slots,
            "missing_slots": [],
            "needs_clarification": False,
            "booking": {"confirmation_state": "confirmed"},
            "time_match_outcome": TIME_MATCH_EXACT,
            "time_resolution": {"outcome": TIME_MATCH_EXACT, "requested_time": "12:00"},
        }
        plan = build_decision_plan(
            intent_name="CREATE_APPOINTMENT",
            luma_response=luma_response,
            domain="service",
            availability_resolved=True,
            session_state=session,
        )
        assert plan["status"] == "READY"
        assert plan["action"] == "CONFIRM_APPOINTMENT"
        assert plan["stage"] == "CONFIRM"
        assert plan.get("awaiting") is None

    def test_exact_match_gate_accept_turn_executes_commit(self):
        from core.orchestration.time_resolution import TIME_MATCH_EXACT
        from core.planning.orchestration.plan_builder import build_decision_plan

        slots = self._slots()
        session = {
            **_availability_cache_session(slots, intent_name="CREATE_APPOINTMENT"),
            "booking": {"confirmation_state": "pending"},
            "resolved_datetime_range": {
                "start": "2026-07-10T12:00:00Z",
                "end": "2026-07-10T12:30:00Z",
            },
        }
        luma_response = {
            "intent": {"name": "CREATE_APPOINTMENT"},
            "slots": slots,
            "_effective_collected_slots": slots,
            "missing_slots": [],
            "needs_clarification": False,
            "booking": {"confirmation_state": "pending"},
            "time_match_outcome": TIME_MATCH_EXACT,
            "time_resolution": {"outcome": TIME_MATCH_EXACT, "requested_time": "12:00"},
            "_confirm_booking_continuation": True,
        }
        plan = build_decision_plan(
            intent_name="CREATE_APPOINTMENT",
            luma_response=luma_response,
            domain="service",
            availability_resolved=True,
            session_state=session,
        )
        assert plan["status"] == "READY"
        assert plan["action"] == "CONFIRM_APPOINTMENT"
        assert plan["stage"] == "CONFIRM"
        assert plan.get("awaiting") is None
