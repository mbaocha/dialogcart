"""Customer-profile evidence authorization and persistence boundary tests."""

from core.engine.execution_coordinator import ExecutionCoordinator
from core.customer_identification import customer_channel_fingerprint
from core.session.confirmation_gate import get_confirmation_state


class _CustomerSpy:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.update_calls = []

    def upsert(self, **kwargs):
        self.calls.append(kwargs)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def update_name_by_id(self, **kwargs):
        self.update_calls.append(kwargs)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _session():
    return {
        "planning": {"pending_profile_request": "CUSTOMER_CONTACT_NAME"},
        "confirmation_state": None,
    }


def _plan(value="Submitted Name"):
    merged = {
        "_entity_schema": {
            "version": 1,
            "fields": [{"name": "customer_contact_name", "type": "text"}],
        },
        "_entity_resolution_evidence": {
            "customer_contact_name": {
                "resolution": "RESOLVED",
                "value": value,
            },
        },
    }
    return {
        "status": "NEEDS_CLARIFICATION",
        "stage": "CONFIRM",
        "awaiting": "CUSTOMER_CONTACT_NAME",
        "_otherwise_confirmation_ready": True,
        "_customer_name_prerequisite": {
            "satisfied": False,
            "required_input": "CUSTOMER_CONTACT_NAME",
        },
        "_merged_luma_response": merged,
    }


def _persist(plan, session, client, *, customer_id=None, phone="+15550001111"):
    return ExecutionCoordinator().persist_customer_contact_evidence(
        plan=plan,
        session_state=session,
        organization_id=2,
        kwargs={
            "customer_client": client,
            "customer_id": customer_id,
            "customer_phone": phone,
            "customer_email": None,
        },
    )


def test_known_customer_without_channel_updates_name_by_id():
    session = _session()
    session["customer_id"] = 91
    plan = _plan("Submitted Name")
    plan["text"] = "Before we confirm, may I have your name?"
    plan["ask_next"] = "customer_contact_name"
    plan["action_branch"] = "customer_contact_name_required"
    plan["plan"] = {
        "ask_next": "customer_contact_name",
        "action_branch": "customer_contact_name_required",
    }
    plan["_decision"] = {
        "awaiting": "CUSTOMER_CONTACT_NAME",
        "ask_next": "customer_contact_name",
        "plan": {
            "ask_next": "customer_contact_name",
            "action_branch": "customer_contact_name_required",
        },
        "facts": {
            "awaiting": "CUSTOMER_CONTACT_NAME",
            "ask_next": "customer_contact_name",
        },
    }
    client = _CustomerSpy([{"id": 91, "organizationId": 2, "name": "Submitted Name"}])

    assert _persist(plan, session, client, customer_id=91, phone=None) is True
    assert client.update_calls == [
        {"organization_id": 2, "customer_id": 91, "name": "Submitted Name"}
    ]
    assert client.calls == []
    assert session["customer_contact"]["authoritative_name"] == "Submitted Name"
    assert plan["status"] == "AWAITING_CONFIRMATION"
    assert "text" not in plan
    assert plan["awaiting"] == "USER_CONFIRMATION"
    assert plan["ask_next"] is None
    assert "action_branch" not in plan
    assert plan["plan"]["ask_next"] is None
    assert "action_branch" not in plan["plan"]
    assert plan["_decision"]["ask_next"] is None
    assert plan["_decision"]["awaiting"] == "USER_CONFIRMATION"
    assert plan["_decision"]["plan"]["ask_next"] is None
    assert "action_branch" not in plan["_decision"]["plan"]
    assert plan["_decision"]["facts"]["ask_next"] is None
    assert plan["_decision"]["facts"]["awaiting"] == "USER_CONFIRMATION"


def test_known_customer_prefers_id_update_even_with_channel():
    session = _session()
    client = _CustomerSpy([{"id": 91, "organizationId": 2, "name": "Submitted Name"}])
    assert _persist(_plan(), session, client, customer_id=91) is True
    assert len(client.update_calls) == 1
    assert client.calls == []


def test_known_customer_update_failure_never_falls_back_to_upsert():
    session = _session()
    plan = _plan()
    client = _CustomerSpy([RuntimeError("temporary")])
    assert _persist(plan, session, client, customer_id=91) is False
    assert len(client.update_calls) == 1
    assert client.calls == []
    assert session["planning"]["pending_profile_request"] == "CUSTOMER_CONTACT_NAME"
    assert get_confirmation_state(plan) is None


def test_known_customer_rejects_mismatched_returned_authority():
    for response in (
        {"id": 92, "organizationId": 2, "name": "Returned Name"},
        {"id": 91, "organizationId": 3, "name": "Returned Name"},
        {"id": 91, "organizationId": 2, "name": "Guest"},
    ):
        session = _session()
        plan = _plan()
        client = _CustomerSpy([response])
        assert _persist(plan, session, client, customer_id=91, phone=None) is False
        assert client.calls == []
        assert session.get("customer_contact") is None
        assert session["planning"]["pending_profile_request"] == "CUSTOMER_CONTACT_NAME"
        assert get_confirmation_state(plan) is None


def test_unknown_customer_name_conflict_is_not_projected_or_disclosed():
    session = _session()
    plan = _plan("Mma Helen")
    client = _CustomerSpy([
        {"id": 231, "organizationId": 2, "name": "Different Customer"}
    ])
    assert _persist(plan, session, client) is False
    assert session.get("customer_contact") is None
    assert session["planning"]["pending_profile_request"] == "CUSTOMER_CONTACT_NAME"
    assert plan["_customer_contact_identity_conflict"] == {
        "detected": True,
        "reason_code": "CUSTOMER_CONTACT_NAME_AUTHORITY_CONFLICT",
    }
    assert "Different Customer" not in str(plan)


def test_unknown_customer_without_channel_preserves_pending_request():
    session = _session()
    plan = _plan()
    client = _CustomerSpy([])
    assert _persist(plan, session, client, phone=None) is False
    assert client.update_calls == []
    assert client.calls == []
    assert session["planning"]["pending_profile_request"] == "CUSTOMER_CONTACT_NAME"


def test_resolved_name_without_active_request_does_not_call_commerce():
    session = _session()
    session["planning"]["pending_profile_request"] = None
    client = _CustomerSpy([])
    assert _persist(_plan(), session, client) is True
    assert client.calls == []


def test_resolved_name_correction_during_pending_confirmation_updates_customer():
    session = {
        "customer_id": 91,
        "customer_contact": {
            "customer_id": 91,
            "authoritative_name": "Godswill Mbaocha",
            "name_status": "authoritative",
        },
        "planning": {"pending_profile_request": None},
        "confirmation_state": "pending",
    }
    client = _CustomerSpy([
        {"id": 91, "organizationId": 2, "name": "Godin Nnem"},
    ])

    assert _persist(_plan("Godin Nnem"), session, client, customer_id=91) is True
    assert client.update_calls == [{
        "organization_id": 2,
        "customer_id": 91,
        "name": "Godin Nnem",
    }]
    assert session["customer_contact"]["authoritative_name"] == "Godin Nnem"


def test_missing_request_schema_authorization_does_not_call_commerce():
    plan = _plan()
    plan["_merged_luma_response"]["_entity_schema"]["fields"] = []
    client = _CustomerSpy([])
    assert _persist(plan, _session(), client) is True
    assert client.calls == []


def test_empty_and_placeholder_names_do_not_call_commerce():
    for value in (None, "", "   ", "Guest", "gUeSt", "anonymous"):
        client = _CustomerSpy([])
        assert _persist(_plan(value), _session(), client) is True
        assert client.calls == []


def test_matching_commerce_returned_name_is_retained_as_authoritative():
    session = _session()
    plan = _plan("Submitted Name")
    client = _CustomerSpy([
        {"id": 91, "organizationId": 2, "name": "Submitted Name"}
    ])
    assert _persist(plan, session, client) is True
    assert session["customer_contact"] == {
        "customer_id": 91,
        "authoritative_name": "Submitted Name",
        "name_status": "authoritative",
        "channel_fingerprint": customer_channel_fingerprint(
            phone="+15550001111"
        ),
    }
    assert session["planning"]["pending_profile_request"] is None
    assert plan["status"] == "AWAITING_CONFIRMATION"
    assert get_confirmation_state(plan) == "pending"


def test_malformed_commerce_projection_preserves_pending_and_blocks_confirmation():
    for response in ({"id": 91}, {"name": "Returned Name"}, {"id": 91, "name": "Guest"}):
        session = _session()
        plan = _plan()
        client = _CustomerSpy([response])
        assert _persist(plan, session, client) is False
        assert session["planning"]["pending_profile_request"] == "CUSTOMER_CONTACT_NAME"
        assert session.get("customer_contact") is None
        assert get_confirmation_state(plan) is None


def test_failure_then_success_is_retry_safe_and_reuses_ready_plan():
    session = _session()
    plan = _plan()
    client = _CustomerSpy([
        RuntimeError("temporary"),
        {"id": 91, "organizationId": 2, "name": "Submitted Name"},
    ])
    assert _persist(plan, session, client) is False
    assert session["planning"]["pending_profile_request"] == "CUSTOMER_CONTACT_NAME"
    assert get_confirmation_state(plan) is None
    assert _persist(plan, session, client) is True
    assert session["planning"]["pending_profile_request"] is None
    assert plan["status"] == "AWAITING_CONFIRMATION"
    assert get_confirmation_state(plan) == "pending"
