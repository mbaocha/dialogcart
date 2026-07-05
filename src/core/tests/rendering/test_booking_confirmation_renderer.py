"""Tests for booking confirmation prompt rendering."""

from core.rendering.booking_confirmation_renderer import (
    prefix_with_revision_acknowledgement,
    render_booking_confirmation_prompt,
    render_revision_acknowledgement,
)


def test_render_booking_confirmation_prompt_formats_slots():
    text = render_booking_confirmation_prompt(
        {
            "service_id": "premium haircut",
            "date": "2026-07-06",
            "time": "09:00",
        }
    )
    assert "Premium Haircut" in text
    assert "July 6" in text
    assert "9:00 AM" in text
    assert "Would you like me to go ahead?" in text


def test_render_revision_acknowledgement_time():
    text = render_revision_acknowledgement(
        {"changes": [{"field": "time", "from": "09:00", "to": "11:00"}]}
    )
    assert "11:00 AM" in text
    assert text.startswith("Sure")


def test_render_revision_acknowledgement_service():
    text = render_revision_acknowledgement(
        {
            "changes": [
                {
                    "field": "service",
                    "from": "premium haircut",
                    "to": "flexi haircut + pruning",
                }
            ]
        }
    )
    assert "Flexi Haircut + Pruning" in text


def test_render_revision_acknowledgement_date():
    text = render_revision_acknowledgement(
        {"changes": [{"field": "date", "from": "2026-07-06", "to": "2026-07-11"}]}
    )
    assert "July 11" in text


def test_render_revision_acknowledgement_empty():
    assert render_revision_acknowledgement(None) == ""
    assert render_revision_acknowledgement({"changes": []}) == ""


def test_prefix_with_revision_acknowledgement():
    body = render_booking_confirmation_prompt(
        {
            "service_id": "premium haircut",
            "date": "2026-07-06",
            "time": "11:00",
        }
    )
    text = prefix_with_revision_acknowledgement(
        body,
        {"changes": [{"field": "time", "from": "09:00", "to": "11:00"}]},
    )
    assert text.startswith("Sure")
    assert "\n\n" in text
    assert "Would you like me to go ahead?" in text
