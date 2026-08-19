"""Interactive chat models WhatsApp identity without claiming a customer ID."""

import chat


def test_chat_ignores_preconfigured_customer_id_and_name(monkeypatch):
    monkeypatch.setenv("CUSTOMER_ID", "2")
    monkeypatch.setenv("CUSTOMER_NAME", "Preconfigured Name")

    assert chat._get_customer_id() is None
    assert chat._get_customer_name() is None


def test_chat_always_has_a_simulated_whatsapp_phone(monkeypatch):
    monkeypatch.delenv("CUSTOMER_PHONE", raising=False)
    monkeypatch.delenv("TEST_CUSTOMER_PHONE", raising=False)

    assert chat._get_customer_phone() == "+15550000002"


def test_chat_phone_can_be_overridden_without_customer_id(monkeypatch):
    monkeypatch.setenv("CUSTOMER_PHONE", "+15551234567")
    monkeypatch.setenv("CUSTOMER_ID", "99")

    payload = chat.build_message_payload(
        user_id="chat_user",
        text="hello",
        timezone="UTC",
        org_id=2,
        booking_domain="service",
        customer_id=chat._get_customer_id(),
        customer_phone=chat._get_customer_phone(),
        customer_name=chat._get_customer_name(),
    )

    assert "customer_id" not in payload
    assert "customer_name" not in payload
    assert payload["customer_phone"] == "+15551234567"
