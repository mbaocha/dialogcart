"""Renderer uses facts labels for OFF_TOPIC; resume is a separate section."""

from core.rendering.llm_renderer import LlmRenderRequest, _build_system_prompt, _build_user_message


def test_system_prompt_avoids_evidence_terminology():
    prompt = _build_system_prompt({})
    assert "Only use information from the provided context" in prompt
    assert "Do not invent details beyond the provided context" in prompt
    assert "never mention, describe, compare, reconcile, or explain" in prompt.lower()
    assert "never mention context mismatches" in prompt.lower()
    assert "never describe your reasoning process" in prompt.lower()
    assert "internal instructions for you only" in prompt.lower()
    # Must not invite user-facing talk about "evidence" as a reasoning concept.
    assert "not present in the evidence" not in prompt.lower()
    assert "do not invent details not present in the evidence" not in prompt.lower()


def test_system_prompt_treats_sections_as_private_working_memory():
    prompt = _build_system_prompt({})
    assert "Conversation" in prompt
    assert "Facts" in prompt
    assert "Resume" in prompt
    assert "Business Knowledge" in prompt
    assert "Supporting Evidence" in prompt
    assert "Always answer using Facts first" in prompt
    assert "Treat Recent conversation only as background" in prompt
    assert "Do not mention that the request is off-topic" in prompt
    assert "Never tell the user that supplied facts are relevant or irrelevant" in prompt
    assert "Do not add generic closing questions" in prompt


def test_user_message_includes_current_user_request_before_facts():
    request = LlmRenderRequest(
        render_instruction="Answer using Facts first, then follow Resume.",
        user_request="Who is the prime minister of UK?",
        facts={
            "scope": "off_topic",
            "answer": "Keir Starmer has been Prime Minister of the United Kingdom since July 2024.",
            "answerable": True,
            "resume_instruction": (
                "After answering, briefly invite the user to book a service or "
                "appointment with this business. Do not invent services or prices."
            ),
        },
    )
    message = _build_user_message(request)
    request_pos = message.index("Current user request:")
    facts_pos = message.index("Facts:")
    resume_pos = message.index("Resume:")
    assert request_pos < facts_pos < resume_pos
    assert "Who is the prime minister of UK?" in message
    assert message.index("Who is the prime minister of UK?") > request_pos


def test_user_message_includes_facts_and_resume():
    request = LlmRenderRequest(
        render_instruction=(
            "When Facts are supplied, they contain the response to the user's latest request. "
            "Always use the Facts first to answer that request directly and concisely. "
            "After the answer is complete, follow the Resume instruction if one is supplied."
        ),
        user_request="Who is the president of Nigeria?",
        facts={
            "scope": "off_topic",
            "answer": "Nigeria's current president is Bola Ahmed Tinubu.",
            "answerable": True,
            "resume_instruction": (
                "After answering, briefly continue the booking where it left off. "
                'Ask which service they want to book and present these options: '
                '"premium haircut", "flexi haircut + pruning".'
            ),
        },
    )
    message = _build_user_message(request)
    assert "Current user request:" in message
    assert "Who is the president of Nigeria?" in message
    assert "Facts:" in message
    assert "Factual answer evidence:" not in message
    assert "evidence" not in message.lower()
    assert "Nigeria's current president is Bola Ahmed Tinubu." in message
    assert "Resume:" in message
    assert "Ask which service" in message


def test_renderer_omits_facts_section_when_unanswerable():
    request = LlmRenderRequest(
        render_instruction="Briefly say you cannot answer that question.",
        user_request="Which stock should I invest all my savings in?",
        facts={"scope": "off_topic", "answer": None, "answerable": False},
    )
    message = _build_user_message(request)
    assert "Current user request:" in message
    assert "Which stock should I invest all my savings in?" in message
    assert "Facts:" not in message
    assert "Factual answer evidence:" not in message
