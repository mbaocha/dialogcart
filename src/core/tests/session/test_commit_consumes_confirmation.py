"""Confirmation lifecycle: Stage 7 policy, commit consumption, and gate closure."""

from core.planning.facts.business_fact_registry import (
    PlanningFactContext,
    derive_business_facts,
)
from core.planning.booking_revision import has_committed_create_appointment
from core.planning.pipeline.requests import AttachedRequest
from core.planning.pipeline.stage06_confirmation import resolve_confirmation
from core.planning.pipeline.types import (
    AvailabilityDecision,
    SlotTurnState,
    WorkingTurn,
)
from core.workflows.booking.workflow import BookingWorkflow
from core.session.confirmation_gate import (
    ConfirmationGateTurn,
    consume_create_appointment_confirmation,
    get_confirmation_state,
    is_confirmation_gate_open,
)


def _commit_ready_payload(**slot_overrides):
    slots = {
        "service_id": "premium",
        "date": "2026-07-10",
        "time": "10:00",
        **slot_overrides,
    }
    return {
        "slots": slots,
        "_effective_collected_slots": dict(slots),
        "resolved_datetime_range": {
            "start": "2026-07-10T10:00:00Z",
            "end": "2026-07-10T10:30:00Z",
        },
    }


def _resolve_confirmation(**overrides):
    payload = overrides.pop("payload", _commit_ready_payload())
    turn_operation = overrides.pop("turn_operation", "NONE")
    confirm_booking_continuation = overrides.pop("confirm_booking_continuation", False)
    gate_action = overrides.pop("intent_decision_gate_action", None)
    working_turn = overrides.pop(
        "working_turn",
        WorkingTurn(
            payload=payload,
            effective_collected_slots=dict(payload["_effective_collected_slots"]),
        ),
    )
    slot_state = overrides.pop(
        "slot_state",
        SlotTurnState(
            intent_name="CREATE_APPOINTMENT",
            missing_slots=[],
            effective_collected_slots=dict(payload["_effective_collected_slots"]),
            base_status="READY",
            needs_clarification=False,
        ),
    )
    availability = overrides.pop(
        "availability",
        AvailabilityDecision(availability_ready=True),
    )
    attached_request = AttachedRequest(
        planning_intent="CREATE_APPOINTMENT",
        turn_operation=turn_operation,
        session_reset_occurred=False,
        confirm_booking_continuation=confirm_booking_continuation,
        gate_action=gate_action,
    )
    defaults = {
        "attached_request": attached_request,
        "session_state": None,
        "gate_booking_intent": "CREATE_APPOINTMENT",
        "user_id": "test",
    }
    defaults.update(overrides)
    return resolve_confirmation(
        slot_state=slot_state,
        working_turn=working_turn,
        availability=availability,
        **defaults,
    )


def test_has_committed_create_appointment():
    assert has_committed_create_appointment({"booking_id": "bk-1"}) is True
    assert has_committed_create_appointment({"booking_id": ""}) is False
    assert has_committed_create_appointment({}) is False


def test_booking_workflow_consumes_confirmation_after_successful_commit():
    session_state = {"confirmation_state": "pending"}
    merged = {"confirmation_state": "pending"}
    plan = {
        "action": "CONFIRM_APPOINTMENT",
        "slots": {},
        "_merged_luma_response": merged,
    }

    slots = BookingWorkflow().process_result(
        execution_result={
            "status": "succeeded",
            "refs": {"booking_id": "bk-99"},
            "subject": {},
        },
        plan=plan,
        slots={},
        action="CONFIRM_APPOINTMENT",
        session_state=session_state,
    )

    assert slots["booking_id"] == "bk-99"
    assert get_confirmation_state(session_state) is None
    assert get_confirmation_state(merged) is None


def test_stage_confirmation_yes_keeps_pending_and_satisfies():
    """Gate YES must not write durable confirmed; turn evidence satisfies policy."""
    payload = _commit_ready_payload()
    payload["confirmation_state"] = "pending"
    decision = _resolve_confirmation(
        payload=payload,
        intent_decision_gate_action=ConfirmationGateTurn.YES,
        confirm_booking_continuation=True,
    )

    assert decision.confirmation_state == "pending"
    assert get_confirmation_state(payload) == "pending"
    assert decision.user_confirmation_satisfied is True
    assert decision.awaiting_user_confirmation is False
    assert get_confirmation_state(payload) != "confirmed"


def test_stage_confirmation_yes_without_continuation_flag_still_satisfies():
    """Gate YES alone is acceptance evidence even if continuation flag is unset."""
    payload = _commit_ready_payload()
    payload["confirmation_state"] = "pending"
    decision = _resolve_confirmation(
        payload=payload,
        intent_decision_gate_action=ConfirmationGateTurn.YES,
        confirm_booking_continuation=False,
    )

    assert decision.confirmation_state == "pending"
    assert decision.user_confirmation_satisfied is True
    assert decision.awaiting_user_confirmation is False
    assert get_confirmation_state(payload) == "pending"


def test_stage08_selects_confirm_appointment_after_yes_without_durable_confirmed():
    """YES evidence + pending durable state must still select CONFIRM_APPOINTMENT."""
    from core.planning.pipeline.decision import DecisionInput, decide
    from core.planning.pipeline.types import CapabilityDecision

    payload = _commit_ready_payload()
    payload["confirmation_state"] = "pending"
    payload["time_match_outcome"] = "TIME_MATCH_EXACT"
    working_turn = WorkingTurn(
        payload=payload,
        effective_collected_slots=dict(payload["_effective_collected_slots"]),
    )
    confirmation = _resolve_confirmation(
        payload=payload,
        working_turn=working_turn,
        intent_decision_gate_action=ConfirmationGateTurn.YES,
        confirm_booking_continuation=True,
    )
    assert confirmation.confirmation_state == "pending"
    assert confirmation.user_confirmation_satisfied is True

    slots = dict(payload["_effective_collected_slots"])
    decision_plan = decide(
        DecisionInput(
            attached_request=AttachedRequest(
                planning_intent="CREATE_APPOINTMENT",
                turn_operation="NONE",
                session_reset_occurred=False,
                confirm_booking_continuation=True,
                gate_action=ConfirmationGateTurn.YES,
            ),
            working_turn=working_turn,
            slot_state=SlotTurnState(
                intent_name="CREATE_APPOINTMENT",
                missing_slots=[],
                effective_collected_slots=slots,
                base_status="READY",
                needs_clarification=False,
            ),
            availability=AvailabilityDecision(availability_ready=True),
            confirmation=confirmation,
            capability=CapabilityDecision(),
            session_state={"confirmation_state": "pending", "slots": slots},
            organization_id=1,
        )
    )
    plan = decision_plan.plan
    assert plan.get("action") == "CONFIRM_APPOINTMENT"
    assert plan.get("status") == "READY"
    assert get_confirmation_state(payload) == "pending"
    assert get_confirmation_state(payload) != "confirmed"


def test_stale_yes_after_revision_cannot_select_confirm_appointment():
    """A YES turn cannot revive authorization cleared by date/service/time revision."""
    from core.planning.pipeline.decision import DecisionInput, decide
    from core.planning.pipeline.types import CapabilityDecision

    cases = {
        "date": {
            "slots": {"service_id": "premium"},
            "missing_slots": ["date", "time"],
            "turn_operation": "SEARCH",
        },
        "service": {
            "slots": {"service_id": "flexi"},
            "missing_slots": ["date", "time"],
            "turn_operation": "SEARCH",
        },
        "time": {
            "slots": {"service_id": "premium", "date": "2026-07-11"},
            "missing_slots": ["time"],
            "turn_operation": "NONE",
        },
    }

    for revision_type, case in cases.items():
        slots = dict(case["slots"])
        payload = {
            "intent": {"name": "CREATE_APPOINTMENT"},
            "slots": slots,
            "_effective_collected_slots": dict(slots),
            "missing_slots": list(case["missing_slots"]),
            "confirmation_state": None,
            "_revision_invalidated_availability": True,
        }
        working_turn = WorkingTurn(
            payload=payload,
            effective_collected_slots=dict(slots),
        )
        confirmation = _resolve_confirmation(
            payload=payload,
            working_turn=working_turn,
            intent_decision_gate_action=ConfirmationGateTurn.YES,
            confirm_booking_continuation=True,
        )
        decision = decide(
            DecisionInput(
                attached_request=AttachedRequest(
                    planning_intent="CREATE_APPOINTMENT",
                    turn_operation=case["turn_operation"],
                    session_reset_occurred=False,
                    confirm_booking_continuation=True,
                    gate_action=ConfirmationGateTurn.YES,
                ),
                working_turn=working_turn,
                slot_state=SlotTurnState(
                    intent_name="CREATE_APPOINTMENT",
                    missing_slots=list(case["missing_slots"]),
                    ask_next=case["missing_slots"][0],
                    effective_collected_slots=dict(slots),
                    base_status="NEEDS_CLARIFICATION",
                    needs_clarification=True,
                ),
                availability=AvailabilityDecision(availability_ready=False),
                confirmation=confirmation,
                capability=CapabilityDecision(),
                session_state={
                    "confirmation_state": None,
                    "slots": dict(slots),
                },
                organization_id=1,
            )
        )

        assert confirmation.confirmation_state is None, revision_type
        assert decision.plan.get("action") != "CONFIRM_APPOINTMENT", revision_type
        assert decision.plan.get("status") != "succeeded", revision_type


def test_stage_confirmation_enters_pending_when_commit_ready():
    payload = _commit_ready_payload()
    decision = _resolve_confirmation(payload=payload)

    assert decision.confirmation_state == "pending"
    assert decision.awaiting_user_confirmation is True
    assert get_confirmation_state(payload) == "pending"


def test_stage_confirmation_skips_pending_when_booking_id_exists():
    payload = _commit_ready_payload(booking_id="bk-1")
    decision = _resolve_confirmation(payload=payload)

    assert decision.confirmation_state is None
    assert decision.awaiting_user_confirmation is False
    assert get_confirmation_state(payload) is None


def test_stage_confirmation_clears_on_no():
    from core.planning.planning_mutations import apply_confirmation_planning_mutations

    session = {
        "intent_name": "CREATE_APPOINTMENT",
        "confirmation_state": "pending",
        "slots": {
            "service_id": "premium",
            "date": "2026-07-10",
            "time": "10:00",
        },
        "resolved_datetime_range": {
            "start": "2026-07-10T10:00:00Z",
            "end": "2026-07-10T10:30:00Z",
        },
        "presented_availability": {"search_date": "2026-07-10", "slots": []},
    }
    # Production Stage 02 already merged session slots onto the working turn.
    payload = {
        "slots": dict(session["slots"]),
        "_effective_collected_slots": dict(session["slots"]),
        "resolved_datetime_range": dict(session["resolved_datetime_range"]),
        "presented_availability": session["presented_availability"],
        "confirmation_state": "pending",
        "time_proposal": {"mode": "exact", "value": "10:00"},
        "date_proposal": {"mode": "single_day", "start": "2026-07-10"},
    }
    working_turn = WorkingTurn(
        payload=payload,
        effective_collected_slots=dict(session["slots"]),
    )
    decision = resolve_confirmation(
        attached_request=AttachedRequest(
            planning_intent="CREATE_APPOINTMENT",
            turn_operation="NONE",
            session_reset_occurred=False,
            gate_action=ConfirmationGateTurn.NO,
        ),
        slot_state=SlotTurnState(
            intent_name="CREATE_APPOINTMENT",
            missing_slots=[],
            effective_collected_slots=dict(session["slots"]),
            base_status="AWAITING_CONFIRMATION",
        ),
        working_turn=working_turn,
        availability=AvailabilityDecision(availability_ready=True),
        session_state=session,
        gate_booking_intent="CREATE_APPOINTMENT",
        user_id="test",
    )

    assert decision.confirmation_state is None
    assert decision.reject_evidence is not None
    assert decision.reject_evidence.rejected is True
    assert decision.reject_evidence.reason_code == "REJECT_CONFIRMATION"
    assert decision.lifecycle_evidence is not None
    assert decision.lifecycle_evidence.action == "reject"
    assert decision.slots_adjusted is True
    assert not hasattr(decision, "reject_text") or getattr(decision, "reject_text", None) is None
    # Stage 06 emits evidence only — booking fields stay until planning apply.
    assert get_confirmation_state(working_turn.payload) == "pending"
    assert working_turn.payload.get("_booking_confirmation_rejected") is not True
    assert working_turn.payload.get("slots", {}).get("time") == "10:00"

    apply_confirmation_planning_mutations(
        working_turn, decision, session_state=session
    )

    assert get_confirmation_state(working_turn.payload) is None
    assert working_turn.payload.get("_booking_confirmation_rejected") is True
    slots = working_turn.payload.get("slots") or {}
    assert slots.get("service_id") == "premium"
    assert slots.get("date") == "2026-07-10"
    assert slots.get("time") in (None, "")
    assert working_turn.payload.get("resolved_datetime_range") is None
    assert "time_proposal" not in working_turn.payload
    # Availability presentation preserved (REJECT does not clear availability).
    from core.workflows.availability.presentation import presented_availability_from_session
    assert presented_availability_from_session(working_turn.payload)
    # Live request-scoped session mirrors reject invalidation (previous_session
    # deepcopy is not passed here).
    assert get_confirmation_state(session) is None
    assert session["slots"].get("time") in (None, "")
    assert session.get("_booking_confirmation_rejected") is True

    # Stage 04 after reject invalidation must derive missing time.
    from core.planning.pipeline.stage04_slots import resolve_slot_turn_state

    slot_state = resolve_slot_turn_state(
        working_turn=working_turn,
        intent_name="CREATE_APPOINTMENT",
        session_state=session,
    )
    assert "time" in (slot_state.missing_slots or [])
    assert slot_state.effective_collected_slots.get("service_id") == "premium"
    assert slot_state.effective_collected_slots.get("date") == "2026-07-10"
    assert "time" not in (slot_state.effective_collected_slots or {})


def test_stage06_reject_does_not_import_renderer():
    """Stage 06 rejection must not pull booking confirmation rendering."""
    import core.planning.pipeline.stage06_confirmation as stage06

    assert not hasattr(stage06, "render_booking_confirmation_rejected")
    source = open(stage06.__file__, encoding="utf-8").read()
    assert "render_booking_confirmation_rejected" not in source
    assert "booking_confirmation_renderer" not in source
    assert "consume_confirmation_state" not in source
    assert "apply_invalidation" not in source
    assert "clear_booking_state" not in source
    assert "InvalidationTrigger" not in source


def test_decide_confirmation_reject_uses_slot_state_missing():
    from core.planning.pipeline.decision import (
        ConfirmationRejectEvidence,
        _decide_confirmation_reject,
    )

    slot_state = SlotTurnState(
        intent_name="CREATE_APPOINTMENT",
        missing_slots=["time"],
        effective_collected_slots={
            "service_id": "premium",
            "date": "2026-07-10",
        },
        base_status="NEEDS_CLARIFICATION",
    )
    plan = _decide_confirmation_reject(
        ConfirmationRejectEvidence(
            rejected=True,
            intent_name="CREATE_APPOINTMENT",
            reason_code="REJECT_CONFIRMATION",
        ),
        slot_state=slot_state,
    )
    assert plan.plan.get("status") == "NEEDS_CLARIFICATION"
    assert plan.plan.get("stage") == "AVAILABILITY"
    assert plan.plan.get("action") is None
    assert plan.plan.get("missing_slots") == ["time"]
    assert plan.facts.get("slots", {}).get("service_id") == "premium"
    assert plan.facts.get("slots", {}).get("time") in (None, "")


def test_stage09_renders_reject_wording_from_evidence():
    from core.planning.pipeline.decision import ConfirmationRejectEvidence
    from core.planning.pipeline.stage09_outcome import assemble_planning_outcome
    from core.planning.pipeline.types import (
        ConfirmationDecision,
        DecisionPlan,
        WorkflowRoute,
    )
    from core.rendering.booking_confirmation_renderer import (
        render_booking_confirmation_rejected,
    )

    slots = {"service_id": "premium", "date": "2026-07-10"}
    outcome = assemble_planning_outcome(
        decision_plan=DecisionPlan(
            plan={
                "status": "NEEDS_CLARIFICATION",
                "stage": "AVAILABILITY",
                "action": None,
                "awaiting": None,
                "missing_slots": ["time"],
            },
            facts={"slots": slots, "missing_slots": ["time"]},
            intent_name="CREATE_APPOINTMENT",
        ),
        workflow_route=WorkflowRoute(route=None, client_name=None),
        working_turn=WorkingTurn(
            payload={
                "slots": slots,
                "_booking_confirmation_rejected": True,
            },
            effective_collected_slots=slots,
        ),
        slot_state=SlotTurnState(
            intent_name="CREATE_APPOINTMENT",
            missing_slots=["time"],
            effective_collected_slots=slots,
            base_status="NEEDS_CLARIFICATION",
        ),
        confirmation=ConfirmationDecision(
            confirmation_state=None,
            reject_evidence=ConfirmationRejectEvidence(
                rejected=True,
                intent_name="CREATE_APPOINTMENT",
            ),
        ),
        session_state=None,
        domain="service",
        user_id="test",
        organization_id=1,
    )
    assert outcome.text == render_booking_confirmation_rejected()
    assert "won't book" in (outcome.text or "").lower()
    assert outcome.outcome.get("missing_slots") == ["time"]
    assert outcome.outcome.get("slots", {}).get("service_id") == "premium"

def test_stage_confirmation_clears_on_another_request():
    """AVAILABILITY + ANOTHER_REQUEST supersedes pending confirmation and invalidates trust."""
    from core.planning.planning_mutations import apply_confirmation_planning_mutations

    payload = _commit_ready_payload()
    payload["confirmation_state"] = "pending"
    session = {
        "intent_name": "CREATE_APPOINTMENT",
        "confirmation_state": "pending",
        "slots": {
            "service_id": "premium",
            "date": "2026-07-10",
            "time": "10:00",
        },
        "resolved_datetime_range": {
            "start": "2026-07-10T10:00:00Z",
            "end": "2026-07-10T10:30:00Z",
        },
        "presented_availability": {"search_date": "2026-07-10", "slots": []},
        "availability_fingerprint": "fp-test",
    }
    working_turn = WorkingTurn(
        payload=payload,
        effective_collected_slots=dict(payload["_effective_collected_slots"]),
    )
    decision = resolve_confirmation(
        attached_request=AttachedRequest(
            planning_intent="CREATE_APPOINTMENT",
            turn_operation="AVAILABILITY",
            session_reset_occurred=False,
            confirm_booking_continuation=False,
            gate_action=ConfirmationGateTurn.ANOTHER_REQUEST,
        ),
        slot_state=SlotTurnState(
            intent_name="CREATE_APPOINTMENT",
            missing_slots=[],
            effective_collected_slots=dict(payload["_effective_collected_slots"]),
            base_status="AWAITING_CONFIRMATION",
        ),
        working_turn=working_turn,
        availability=AvailabilityDecision(availability_ready=True),
        session_state=session,
        gate_booking_intent="CREATE_APPOINTMENT",
        user_id="test",
    )

    assert decision.confirmation_state is None
    assert decision.awaiting_user_confirmation is False
    assert decision.user_confirmation_satisfied is False
    assert decision.availability_reshow is False
    assert decision.availability_invalidation is not None
    assert decision.availability_invalidation.invalidated is True
    assert (
        decision.availability_invalidation.reason_code
        == "AVAILABILITY_SUPERSEDES_PENDING_CONFIRMATION"
    )
    assert decision.bound_datetime_clear is not None
    assert decision.bound_datetime_clear.cleared is True
    assert decision.bound_datetime_clear.preserve_current_turn_time is False
    assert decision.slots_adjusted is True
    assert decision.lifecycle_evidence is not None
    assert decision.lifecycle_evidence.action == "supersede"
    assert decision.consume_evidence is not None
    assert decision.consume_evidence.consume is True
    assert get_confirmation_state(payload) == "pending"
    assert payload.get("slots", {}).get("time") == "10:00"
    assert payload.get("resolved_datetime_range") is not None

    apply_confirmation_planning_mutations(
        working_turn, decision, session_state=session
    )

    assert get_confirmation_state(payload) is None
    # Live request-scoped session mirrors consume + bound clear.
    assert get_confirmation_state(session) is None
    assert session.get("slots", {}).get("time") in (None, "")
    assert session.get("resolved_datetime_range") is None
    assert session.get("_bound_datetime_cleared") is True

    effective_slots = payload.get("_effective_collected_slots") or payload.get("slots") or {}
    assert payload.get("resolved_datetime_range") is None
    assert effective_slots.get("time") in (None, "")
    assert effective_slots.get("service_id") == "premium"
    assert effective_slots.get("date") == "2026-07-10"
    assert working_turn.effective_collected_slots.get("time") in (None, "")
    assert working_turn.effective_collected_slots is payload["_effective_collected_slots"]
    assert payload["slots"] is payload["_effective_collected_slots"]

    facts = derive_business_facts(
        PlanningFactContext(
            intent_name="CREATE_APPOINTMENT",
            organization_id=1,
            slots=effective_slots,
            session_state=None,
            luma_response=payload,
            confirmation_state=decision.confirmation_state,
        )
    )
    assert facts.user_confirmation_required is False
    assert facts.time_selection_ready is False


def test_stage06_availability_flag_alone_does_not_preserve_time_proposal():
    """Stale has_time flag without uttered time must not preserve time_proposal."""
    from core.planning.planning_mutations import apply_confirmation_planning_mutations

    payload = _commit_ready_payload()
    payload["confirmation_state"] = "pending"
    payload["_current_turn_has_time"] = True
    payload.pop("_current_turn_time", None)
    payload["time_proposal"] = {"mode": "exact", "value": "10:00"}
    session = {
        "intent_name": "CREATE_APPOINTMENT",
        "confirmation_state": "pending",
        "slots": dict(payload["slots"]),
        "resolved_datetime_range": dict(payload["resolved_datetime_range"]),
    }
    working_turn = WorkingTurn(
        payload=payload,
        effective_collected_slots=dict(payload["_effective_collected_slots"]),
    )
    decision = resolve_confirmation(
        attached_request=AttachedRequest(
            planning_intent="CREATE_APPOINTMENT",
            turn_operation="AVAILABILITY",
            session_reset_occurred=False,
            gate_action=ConfirmationGateTurn.ANOTHER_REQUEST,
        ),
        slot_state=SlotTurnState(
            intent_name="CREATE_APPOINTMENT",
            missing_slots=[],
            effective_collected_slots=dict(payload["_effective_collected_slots"]),
            base_status="AWAITING_CONFIRMATION",
        ),
        working_turn=working_turn,
        availability=AvailabilityDecision(availability_ready=True),
        session_state=session,
        gate_booking_intent="CREATE_APPOINTMENT",
        user_id="test",
    )
    assert decision.bound_datetime_clear is not None
    assert decision.bound_datetime_clear.preserve_current_turn_time is False
    apply_confirmation_planning_mutations(
        working_turn, decision, session_state=session
    )
    assert payload.get("time_proposal") in (None, {})
    assert "time_proposal" not in payload or not payload.get("time_proposal")
    assert payload.get("slots", {}).get("time") in (None, "")
    assert payload.get("_bound_datetime_cleared") is True


def test_stage06_availability_preserves_current_turn_time_flag():
    """Availability with current-turn time emits preserve_current_turn_time=True."""
    from core.planning.planning_mutations import apply_confirmation_planning_mutations

    payload = _commit_ready_payload()
    payload["confirmation_state"] = "pending"
    payload["_current_turn_has_time"] = True
    payload["_current_turn_time"] = "11:00"
    payload["time_proposal"] = {"mode": "exact", "value": "11:00"}
    session = {
        "intent_name": "CREATE_APPOINTMENT",
        "confirmation_state": "pending",
        "slots": dict(payload["slots"]),
        "resolved_datetime_range": dict(payload["resolved_datetime_range"]),
    }
    working_turn = WorkingTurn(
        payload=payload,
        effective_collected_slots=dict(payload["_effective_collected_slots"]),
    )
    decision = resolve_confirmation(
        attached_request=AttachedRequest(
            planning_intent="CREATE_APPOINTMENT",
            turn_operation="AVAILABILITY",
            session_reset_occurred=False,
            gate_action=ConfirmationGateTurn.ANOTHER_REQUEST,
        ),
        slot_state=SlotTurnState(
            intent_name="CREATE_APPOINTMENT",
            missing_slots=[],
            effective_collected_slots=dict(payload["_effective_collected_slots"]),
            base_status="AWAITING_CONFIRMATION",
        ),
        working_turn=working_turn,
        availability=AvailabilityDecision(availability_ready=True),
        session_state=session,
        gate_booking_intent="CREATE_APPOINTMENT",
        user_id="test",
    )
    assert decision.bound_datetime_clear is not None
    assert decision.bound_datetime_clear.preserve_current_turn_time is True
    apply_confirmation_planning_mutations(
        working_turn, decision, session_state=session
    )
    assert payload.get("time_proposal") == {"mode": "exact", "value": "11:00"}
    assert payload.get("slots", {}).get("time") in (None, "")
    assert payload.get("resolved_datetime_range") is None
    assert get_confirmation_state(payload) is None


def test_business_facts_skip_confirmation_when_booking_id_exists():
    facts = derive_business_facts(
        PlanningFactContext(
            intent_name="CREATE_APPOINTMENT",
            organization_id=1,
            slots={
                "service_id": "premium",
                "date": "2026-07-10",
                "time": "10:00",
                "booking_id": "bk-1",
            },
            session_state={
                "availability_fingerprint": "fp",
                "resolved_datetime_range": {
                    "start": "2026-07-10T10:00:00Z",
                    "end": "2026-07-10T10:30:00Z",
                },
            },
        )
    )
    assert facts.user_confirmation_required is False


def test_gate_closed_after_commit():
    session = {
        "intent_name": "CREATE_APPOINTMENT",
        "status": "AWAITING_CONFIRMATION",
        "confirmation_state": "pending",
        "slots": {
            "service_id": "premium",
            "date": "2026-07-10",
            "time": "10:00",
            "booking_id": "bk-1",
        },
        "resolved_datetime_range": {
            "start": "2026-07-10T10:00:00Z",
            "end": "2026-07-10T10:30:00Z",
        },
    }
    consume_create_appointment_confirmation(session)
    assert is_confirmation_gate_open(session) is False
