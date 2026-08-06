"""Renderer asks only planning ask_next; no service-first heuristic."""

from core.rendering import response_renderer as rr


def test_renderer_asks_only_ask_next_not_full_missing_list(monkeypatch):
    captured = {}

    def fake_render_llm(request):
        captured["instruction"] = request.render_instruction
        return "Which date works for you?"

    monkeypatch.setattr(rr, "render_llm", fake_render_llm)

    result = {"status": "NEEDS_CLARIFICATION"}
    decision = {
        "intent_name": "CREATE_APPOINTMENT",
        "ask_next": "date",
        "facts": {
            "missing_slots": ["date", "time"],
            "ask_next": "date",
            "slots": {"service_id": "premium haircut"},
        },
    }
    rr._inject_rendering_text_impl(result, decision, session_state={})

    instruction = captured.get("instruction") or ""
    assert "Ask ONLY for these specific missing fields (nothing else): date." in instruction


def test_renderer_service_first_heuristic_removed(monkeypatch):
    """When ask_next is date, do not override to service_id even if service is missing."""
    captured = {}

    def fake_render_llm(request):
        captured["instruction"] = request.render_instruction
        return "ok"

    monkeypatch.setattr(rr, "render_llm", fake_render_llm)

    result = {"status": "NEEDS_CLARIFICATION"}
    decision = {
        "intent_name": "CREATE_APPOINTMENT",
        "ask_next": "date",
        "facts": {
            "missing_slots": ["service_id", "date", "time"],
            "ask_next": "date",
        },
    }
    rr._inject_rendering_text_impl(result, decision, session_state={})

    instruction = captured.get("instruction") or ""
    assert "Ask ONLY for these specific missing fields (nothing else): date." in instruction


def test_renderer_falls_back_to_first_missing_when_ask_next_absent(monkeypatch):
    captured = {}

    def fake_render_llm(request):
        captured["instruction"] = request.render_instruction
        return "ok"

    monkeypatch.setattr(rr, "render_llm", fake_render_llm)

    result = {"status": "NEEDS_CLARIFICATION"}
    decision = {
        "intent_name": "CREATE_APPOINTMENT",
        "facts": {"missing_slots": ["service_id", "date", "time"]},
    }
    rr._inject_rendering_text_impl(result, decision, session_state={})

    instruction = captured.get("instruction") or ""
    assert (
        "Ask ONLY for these specific missing fields (nothing else): service_id."
        in instruction
    )


def test_renderer_reconciles_stale_ask_next_with_final_missing_slots(monkeypatch):
    captured = {}

    def fake_render_llm(request):
        captured["request"] = request
        return "What is your vehicle registration number?"

    monkeypatch.setattr(rr, "render_llm", fake_render_llm)
    result = {"status": "NEEDS_CLARIFICATION"}
    decision = {
        "intent_name": "CREATE_APPOINTMENT",
        "ask_next": "engine_type",
        "missing_slots": ["registration_number"],
        "plan": {
            "ask_next": "engine_type",
            "missing_slots": ["registration_number"],
        },
        "facts": {
            "ask_next": "engine_type",
            "missing_slots": ["registration_number"],
        },
    }

    rr._inject_rendering_text_impl(result, decision, session_state={})

    request = captured["request"]
    assert request.facts == {
        "structured_context": {
            "business_name": None,
            "business_about": None,
            "business_phone": None,
        },
        "rendering_purpose": "clarification",
        "ask_next": "registration_number",
        "missing_slots": ["registration_number"],
        "promptable_slots": [],
    }
    assert "registration_number" in request.render_instruction
    assert "engine_type" not in request.render_instruction


def test_renderer_preserves_valid_ask_next_ordering(monkeypatch):
    captured = {}

    def fake_render_llm(request):
        captured["request"] = request
        return "Which date works?"

    monkeypatch.setattr(rr, "render_llm", fake_render_llm)
    result = {"status": "NEEDS_CLARIFICATION"}
    decision = {
        "intent_name": "CREATE_APPOINTMENT",
        "ask_next": "date",
        "missing_slots": ["service_id", "date", "time"],
        "facts": {"missing_slots": ["service_id", "date", "time"]},
    }

    rr._inject_rendering_text_impl(result, decision, session_state={})

    assert captured["request"].facts["ask_next"] == "date"
