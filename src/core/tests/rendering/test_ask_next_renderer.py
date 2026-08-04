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
