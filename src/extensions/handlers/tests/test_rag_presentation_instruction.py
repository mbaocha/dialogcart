"""Focused tests: RAG handler presentation instruction."""

from unittest.mock import patch

from extensions.handlers.adapters.rag import RagAdapter


_FAQ = {
    "chunks": [],
    "structured_context": {
        "business_phone": "+1 555 000 1234",
        "services": [
            {"name": "Haircut", "config": {"price": 25, "durationMinutes": 30}}
        ],
        "hours": {"mon": "9am-6pm"},
    },
    "no_hit": True,
}


def test_rag_instruction_covers_service_presentation_and_booking_cta():
    with patch("extensions.handlers.adapters.rag.FaqClient") as MockFaq:
        MockFaq.return_value.retrieve.return_value = _FAQ
        response = RagAdapter().handle(
            {
                "organization_id": 1,
                "search_query": "what services do you offer",
                "user_text": "What services do you offer?",
            }
        )
    instruction = response.render_instruction.lower()
    assert "front-desk" in instruction
    assert "compare" in instruction or "decision" in instruction
    assert "contact" in instruction
    assert "calling" in instruction
    assert "next step" in instruction or "continue" in instruction
    # No formatting prescriptions
    assert "duration:" not in instruction
    assert "brackets" not in instruction
    # Retrieval payload unchanged
    assert response.facts["structured_context"] == _FAQ["structured_context"]
