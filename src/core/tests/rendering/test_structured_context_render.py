"""Focused tests: structured_context as authoritative Business Knowledge."""

from __future__ import annotations

from core.rendering.llm_renderer import (
    LlmRenderRequest,
    _BUSINESS_KNOWLEDGE_PRESENTATION,
    _build_system_prompt,
    _build_user_message,
)


def test_empty_chunks_still_exposes_structured_context():
    request = LlmRenderRequest(
        render_instruction="Answer the user.",
        user_request="What services do you offer?",
        facts={
            "chunks": [],
            "structured_context": {
                "business_name": "CarOne",
                "services": [
                    {"name": "Executive Oil Change", "config": {"price": 95}},
                ],
            },
            "no_hit": True,
        },
    )
    message = _build_user_message(request)
    assert "Business Knowledge (Authoritative):" in message
    assert "Executive Oil Change" in message
    assert '"price": 95' in message or '"price":95' in message
    assert "Supporting Evidence:" not in message


def test_chunks_and_structured_context_are_separate_sections():
    request = LlmRenderRequest(
        render_instruction="Answer the user.",
        facts={
            "chunks": [
                {"id": 1, "content": "Oil changes take about 30 minutes."},
            ],
            "structured_context": {
                "services": [{"name": "Executive Oil Change"}],
            },
        },
    )
    message = _build_user_message(request)
    knowledge_pos = message.index("Business Knowledge (Authoritative):")
    evidence_pos = message.index("Supporting Evidence:")
    assert knowledge_pos < evidence_pos
    assert "Executive Oil Change" in message
    assert "Oil changes take about 30 minutes." in message


def test_arbitrary_structured_context_keys_rendered():
    request = LlmRenderRequest(
        render_instruction="Answer.",
        facts={
            "structured_context": {
                "promotions": [{"code": "SPRING10", "percent": 10}],
                "locations": [{"name": "Main Garage", "city": "London"}],
                "staff": ["Sarah", "James"],
            },
        },
    )
    message = _build_user_message(request)
    assert "promotions" in message
    assert "SPRING10" in message
    assert "locations" in message
    assert "Main Garage" in message
    assert "staff" in message
    assert "Sarah" in message


def test_services_rendered_without_special_casing():
    """Services appear only because they are keys in structured_context JSON."""
    request = LlmRenderRequest(
        render_instruction="Answer.",
        facts={
            "structured_context": {
                "services": [
                    {
                        "name": "Premium Full Service",
                        "type": "service",
                        "config": {"price": 85, "durationMinutes": 45},
                    }
                ],
            },
        },
    )
    message = _build_user_message(request)
    assert '"services"' in message
    assert "Premium Full Service" in message
    assert "85" in message


def test_future_unknown_structured_context_key_rendered():
    request = LlmRenderRequest(
        render_instruction="Answer.",
        facts={
            "structured_context": {
                "loyalty_tiers": {"gold": {"discount": 15}},
                "weird_future_field_xyz": {"enabled": True},
            },
        },
    )
    message = _build_user_message(request)
    assert "loyalty_tiers" in message
    assert "weird_future_field_xyz" in message
    assert "true" in message.lower()


def test_precedence_instruction_present_in_system_prompt():
    prompt = _build_system_prompt({})
    assert "Business Knowledge is authoritative" in prompt
    assert "Supporting Evidence provides additional" in prompt
    assert "Business Knowledge wins" in prompt
    assert "Do not invent business facts absent from Business Knowledge" in prompt


def test_empty_structured_context_omits_business_knowledge_section():
    request = LlmRenderRequest(
        render_instruction="Answer.",
        facts={"structured_context": {}, "chunks": [{"content": "Only a chunk."}]},
    )
    message = _build_user_message(request)
    assert "Business Knowledge" not in message
    assert "Supporting Evidence:" in message
    assert "Only a chunk." in message
    assert "front-desk" not in message.lower()


def test_service_list_presentation_rules_in_user_message():
    request = LlmRenderRequest(
        render_instruction="Answer the user.",
        user_request="What services do you offer?",
        facts={
            "structured_context": {
                "business_name": "Glamour Studio",
                "business_phone": "+1 555 000 1234",
                "services": [
                    {
                        "name": "Haircut",
                        "description": "Wash and cut.",
                        "config": {"price": 25, "durationMinutes": 30},
                    }
                ],
                "hours": {"mon-sat": "9:00 AM to 5:00 PM"},
            },
        },
    )
    message = _build_user_message(request)
    assert "front-desk" in message.lower() or "experienced" in message.lower()
    assert "compare" in message.lower() or "decision" in message.lower()
    assert "contact" in message.lower()
    assert "explicitly asks" in message.lower() or "explicitly ask" in message.lower()
    assert "calling" in message.lower() or "contacting" in message.lower()
    assert "fixed template" in message.lower()
    # structured_context still present unchanged
    assert "+1 555 000 1234" in message
    assert '"durationMinutes": 30' in message or '"durationMinutes":30' in message


def test_presentation_rules_omit_call_cta():
    guidance = _BUSINESS_KNOWLEDGE_PRESENTATION.lower()
    assert "never encourage calling" in guidance or "never encourage" in guidance
    assert "next" in guidance and ("step" in guidance or "conversation" in guidance)
    # No formatting prescriptions
    assert "duration:" not in guidance
    assert "bullet" not in guidance
    assert "markdown" not in guidance
    assert "which of these services would you like to book" not in guidance


def test_availability_render_without_structured_context_skips_presentation_rules():
    request = LlmRenderRequest(
        render_instruction="List available times as a bullet list.",
        facts={
            "availability": {
                "service_name": "Haircut",
                "date": "2026-07-02",
                "times": ["09:00", "10:00"],
            }
        },
    )
    message = _build_user_message(request)
    assert "front-desk" not in message.lower()
    assert "Available times:" in message
