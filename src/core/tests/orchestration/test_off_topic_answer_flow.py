"""OFF_TOPIC render-request unit coverage (no HTTP / api_client)."""

from core.rendering.off_topic_renderer import build_off_topic_render_request


def test_build_off_topic_render_request_attaches_resume():
    req = build_off_topic_render_request(
        {
            "off_topic_query": "Who is the president of Nigeria?",
            "answerable": True,
            "answer": "Nigeria's current president is Bola Ahmed Tinubu.",
        },
        session_state={},
        user_input="Who is the president of Nigeria?",
    )
    assert req.facts.get("answer") == (
        "Nigeria's current president is Bola Ahmed Tinubu."
    )
    assert req.user_request == "Who is the president of Nigeria?"
    assert "facts first" in req.render_instruction.lower()
    assert "evidence" not in req.render_instruction.lower()
    assert isinstance(req.facts.get("resume_instruction"), str)
    assert req.facts["resume_instruction"].strip()
