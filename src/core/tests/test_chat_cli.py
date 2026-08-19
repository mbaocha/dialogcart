"""Focused sanity tests for multi-category chat.py developer UX."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import chat as chat_mod


def test_default_category_is_beauty_salon():
    assert chat_mod.DEFAULT_BUSINESS_CATEGORY == "beauty_salon"
    assert chat_mod.resolve_org_id("beauty_salon") == 1


def test_startup_org_mapping_for_each_category():
    assert chat_mod.resolve_org_id("beauty_salon") == 1
    assert chat_mod.resolve_org_id("hotel") == 2
    assert chat_mod.resolve_org_id("car_service") == 3


def test_unknown_category_raises():
    try:
        chat_mod.resolve_org_id("clinic")
        assert False, "expected ValueError"
    except ValueError as e:
        assert "clinic" in str(e)


def test_startup_banner_shows_category_domain_org():
    text = chat_mod.format_startup_banner(
        business_category="car_service",
        booking_domain="service",
        org_id=3,
    )
    assert "Business Category : car_service" in text
    assert "Booking Domain    : service" in text
    assert "Organization ID   : 3" in text


def test_cli_defaults_and_category_choices():
    parser = chat_mod.build_arg_parser()
    args = parser.parse_args([])
    assert args.business_category == "beauty_salon"

    for category in ("beauty_salon", "car_service", "hotel"):
        args = parser.parse_args(["--business-category", category])
        assert args.business_category == category
        assert chat_mod.resolve_org_id(args.business_category) == (
            chat_mod.BUSINESS_CATEGORY_TO_ORG[category]
        )


def test_switch_command_updates_category_and_org():
    category, org_id = chat_mod.switch_business_category(
        "beauty_salon", "car_service"
    )
    assert category == "car_service"
    assert org_id == 3

    category, org_id = chat_mod.switch_business_category("car_service", "hotel")
    assert category == "hotel"
    assert org_id == 2


def test_switch_rejects_unknown_category():
    try:
        chat_mod.switch_business_category("beauty_salon", "not_a_vertical")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_switch_command_parser_does_not_capture_booking_correction():
    assert chat_mod.parse_switch_command("switch car_service") == "car_service"
    assert chat_mod.parse_switch_command("switch time to 10am") is None
    assert chat_mod.parse_switch_command("switch not_a_vertical") is None


def test_catalog_beauty_salon_collections():
    data = {
        "services": [
            {"name": "Premium Haircut", "is_active": True},
            {"name": "Flexi Haircut", "is_active": True},
        ],
        "staff": [
            {"name": "Sarah", "is_active": True},
            {"name": "James", "is_active": True},
        ],
    }
    text = chat_mod.format_catalog_display(
        "beauty_salon", data, ["services"]
    )
    assert "Beauty Salon" in text
    assert "Services" in text
    assert "Premium Haircut" in text
    assert "Flexi Haircut" in text
    assert "Staff" not in text  # schema for salon only references services
    assert "beauty_and_wellness" not in text


def test_catalog_car_service_collections():
    data = {
        "services": [
            {"name": "Oil Change", "is_active": True},
            {"name": "Brake Inspection", "is_active": True},
        ],
        "staff": [
            {"name": "John", "is_active": True},
            {"name": "Mike", "is_active": True},
        ],
    }
    text = chat_mod.format_catalog_display(
        "car_service", data, ["services", "staff"]
    )
    assert "Car Service" in text
    assert "Oil Change" in text
    assert "Brake Inspection" in text
    assert "Staff" in text
    assert "John" in text
    assert "Mike" in text


def test_catalog_hotel_collections():
    data = {
        "room_types": [
            {"name": "Standard", "is_active": True},
            {"name": "Deluxe", "is_active": True},
            {"name": "Suite", "is_active": True},
        ]
    }
    text = chat_mod.format_catalog_display("hotel", data, ["room_types"])
    assert "Hotel" in text
    assert "Room Types" in text
    assert "Standard" in text
    assert "Deluxe" in text
    assert "Suite" in text
    assert "Services" not in text


def test_message_payload_sends_organization_id_for_category():
    for category, org_id in chat_mod.BUSINESS_CATEGORY_TO_ORG.items():
        payload = chat_mod.build_message_payload(
            user_id="chat_user",
            text="hello",
            timezone="UTC",
            org_id=org_id,
            booking_domain="service" if category != "hotel" else "reservation",
        )
        assert payload["organization_id"] == org_id
        assert payload["user_id"] == "chat_user"
        assert payload["text"] == "hello"
        assert "domain" in payload


def test_post_message_reaches_core_with_org_id():
    mock_response = MagicMock()
    mock_response.is_success = True
    mock_response.json.return_value = {
        "success": True,
        "text": "Hi",
        "outcome": {"status": "READY"},
    }
    mock_client = MagicMock()
    mock_client.post.return_value = mock_response

    result = chat_mod._post_message(
        mock_client,
        core_url="http://localhost:8000",
        user_id="chat_user",
        text="book me an oil change",
        domain="service",
        timezone="UTC",
        org_id=3,
    )
    assert result["success"] is True
    mock_client.post.assert_called_once()
    args, kwargs = mock_client.post.call_args
    assert args[0] == "http://localhost:8000/api/message"
    body = kwargs["json"]
    assert body["organization_id"] == 3
    assert body["text"] == "book me an oil change"


def test_help_lists_business_categories():
    parser = chat_mod.build_arg_parser()
    help_text = parser.format_help()
    assert "beauty_salon" in help_text
    assert "car_service" in help_text
    assert "hotel" in help_text


def test_resolve_booking_domain_uses_org_resolution_not_cli():
    """Banner domain comes from org resolve path."""
    mock_cache = MagicMock()
    mock_cache.resolve.return_value = ("hotel", "reservation", 2)

    with patch(
        "core.adapters.cache.org_domain_cache.org_domain_cache",
        mock_cache,
    ), patch(
        "core.adapters.clients.organization_client.OrganizationClient",
        MagicMock,
    ):
        domain = chat_mod.resolve_booking_domain_for_org(2)
    assert domain == "reservation"
    mock_cache.resolve.assert_called()
