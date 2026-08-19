"""Booking E2Es for the customer/contact identification gate."""

from core.rendering.booking_confirmation_renderer import (
    render_booking_confirmation_prompt,
)
from core.session.session_manager import get_session
from core.tests.e2e.framework.conversation import Expect, ORG_ID, Scenario, Turn


def _anonymous_contact_channel(conv) -> None:
    conv.customer_phone = "+15551234002"
    conv.customer_email = "unnamed.customer@dialogcart.test"
    conv.customer_name = None
    conv.customer_id = None


def _known_customer_without_name_or_channel(conv) -> None:
    from core.api import message as message_api

    customer_id = 92002
    customer_client = message_api._customer_client
    customer_client._org_ids[customer_id] = ORG_ID
    customer_client.update_name_by_id_calls.clear()
    conv.customer_id = customer_id
    conv.customer_phone = None
    conv.customer_email = None
    conv.customer_name = None


def _assert_known_customer_name_updated_by_id(conv, *_deps) -> None:
    from core.api import message as message_api

    session = get_session(ORG_ID, conv.user_id) or {}
    customer_client = message_api._customer_client
    assert customer_client.update_name_by_id_calls == [
        {
            "organization_id": ORG_ID,
            "customer_id": 92002,
            "name": "Godswill Mbaocha",
        }
    ]
    assert session.get("customer_contact") == {
        "customer_id": 92002,
        "authoritative_name": "Godswill Mbaocha",
        "name_status": "authoritative",
    }
    _assert_name_persisted_and_confirmation_rendered(conv, *_deps)


def _assert_contact_not_booking_slot(conv, *_deps) -> None:
    session = get_session(ORG_ID, conv.user_id) or {}
    assert "customer_contact_name" not in (session.get("slots") or {})
    assert "customer_contact_name" not in (
        (session.get("planning") or {}).get("slots") or {}
    )


def _assert_name_persisted_and_confirmation_rendered(conv, *_deps) -> None:
    session = get_session(ORG_ID, conv.user_id) or {}
    planning = session.get("planning") or {}
    contact = session.get("customer_contact") or {}
    text = conv.last_body.get("text")

    assert planning.get("status") == "AWAITING_CONFIRMATION"
    assert planning.get("pending_profile_request") is None
    assert planning.get("ask_next") is None
    assert session.get("confirmation_state") == "pending"
    assert contact.get("name_status") == "authoritative"
    assert contact.get("authoritative_name") == "Godswill Mbaocha"
    assert isinstance(text, str) and text.strip()
    assert text.endswith(render_booking_confirmation_prompt(planning.get("slots")))
    assert "Godswill Mbaocha" in text
    assert "may i have your name" not in text.casefold()
    assert "couldn't save your contact name" not in text.casefold()
    _assert_contact_not_booking_slot(conv, *_deps)


def _assert_booking_completed_after_collected_name(conv, booking, *_deps) -> None:
    session = get_session(ORG_ID, conv.user_id) or {}
    booking_state = session.get("booking") or {}
    assert booking_state.get("booking_id")
    assert session.get("confirmation_state") is None
    assert (session.get("planning") or {}).get("pending_profile_request") is None
    assert booking.create_booking.called, (
        "expected booking execution after explicit confirmation"
    )
    kwargs = booking.create_booking.call_args.kwargs
    assert kwargs.get("start_time"), "service booking must send selected start_time"
    assert "end_time" not in kwargs, "Commerce derives service booking end_time"


def _assert_customer_name_gate(conv, *_deps) -> None:
    session = get_session(ORG_ID, conv.user_id) or {}
    planning = session.get("planning") or {}
    assert planning.get("pending_profile_request") == "CUSTOMER_CONTACT_NAME"
    assert session.get("confirmation_state") is None
    assert "customer_contact_name" not in (planning.get("slots") or {})
    text = conv.last_body.get("text")
    assert isinstance(text, str) and "name" in text.casefold()


SCENARIOS = [
    Scenario(
        "Known customer name goes directly to confirmation",
        Turn("book haircut tomorrow at 10am", Expect(response_status="NEEDS_CLARIFICATION")),
        Turn(
            "premium",
            Expect(planner="AWAITING_CONFIRMATION", awaiting="USER_CONFIRMATION"),
            after=_assert_contact_not_booking_slot,
        ),
        fixture="scripted",
        requires_customer_identity=True,
        id="known-customer-name-skips-identification",
    ),
    Scenario(
        "Unnamed customer supplies contact name before confirmation",
        Turn("book haircut tomorrow at 10am", Expect(response_status="NEEDS_CLARIFICATION")),
        Turn(
            "premium",
            Expect(
                response_status="succeeded",
                planner="NEEDS_CLARIFICATION",
                stage="CONFIRM",
                action=None,
                awaiting="CUSTOMER_CONTACT_NAME",
                time_match="TIME_MATCH_EXACT",
                confirmation=None,
                response_text_present=True,
            ),
            after=_assert_customer_name_gate,
        ),
        Turn(
            "Godswill Mbaocha",
            Expect(planner="AWAITING_CONFIRMATION", awaiting="USER_CONFIRMATION"),
            after=_assert_name_persisted_and_confirmation_rendered,
        ),
        Turn(
            "yes",
            Expect(
                planner="READY",
                stage="CONFIRM",
                action="CONFIRM_APPOINTMENT",
                confirmation=None,
                missing_slots=[],
            ),
            after=_assert_booking_completed_after_collected_name,
        ),
        fixture="scripted",
        before=_anonymous_contact_channel,
        id="collect-customer-contact-name-before-confirmation",
    ),
    Scenario(
        "Known unnamed customer updates contact name by id before confirmation",
        Turn("book haircut tomorrow at 10am", Expect(response_status="NEEDS_CLARIFICATION")),
        Turn(
            "premium",
            Expect(
                planner="NEEDS_CLARIFICATION",
                stage="CONFIRM",
                action=None,
                awaiting="CUSTOMER_CONTACT_NAME",
                confirmation=None,
                response_text_present=True,
            ),
            after=_assert_customer_name_gate,
        ),
        Turn(
            "Godswill Mbaocha",
            Expect(planner="AWAITING_CONFIRMATION", awaiting="USER_CONFIRMATION"),
            after=_assert_known_customer_name_updated_by_id,
        ),
        fixture="scripted",
        before=_known_customer_without_name_or_channel,
        id="known-customer-name-update-by-id-before-confirmation",
    ),
]
