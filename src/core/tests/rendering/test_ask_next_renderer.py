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


def test_renderer_acknowledges_current_time_not_previous_engine(monkeypatch):
    captured = {}

    def fake_render_llm(request):
        captured["instruction"] = request.render_instruction
        return "9:45 AM noted. What's your registration number?"

    monkeypatch.setattr(rr, "render_llm", fake_render_llm)
    result = {
        "status": "NEEDS_CLARIFICATION",
        "_merged_luma_response": {
            "_current_turn_has_time": True,
            "_current_turn_time": "09:45",
        },
    }
    decision = {
        "intent_name": "CREATE_APPOINTMENT",
        "ask_next": "registration_number",
        "missing_slots": ["registration_number"],
        "facts": {"missing_slots": ["registration_number"]},
    }
    session = {
        "last_filled_slot": "engine_type",
        "planning": {"slots": {"engine_type": "petrol"}},
    }

    rr._inject_rendering_text_impl(result, decision, session_state=session)

    instruction = captured["instruction"]
    assert "time=09:45" in instruction
    assert "engine_type" not in instruction
    assert "petrol" not in instruction


def test_customer_name_prompt_acknowledges_current_registration(monkeypatch):
    def fail_render_llm(_request):
        raise AssertionError("customer contact name must use deterministic rendering")

    monkeypatch.setattr(rr, "render_llm", fail_render_llm)
    result = {
        "status": "NEEDS_CLARIFICATION",
        "_merged_luma_response": {
            "facts": {"registration_number": "AZ213PP"},
            "_entity_schema": {
                "version": 1,
                "fields": [
                    {
                        "name": "registration_number",
                        "type": "free_text",
                        "required": True,
                    }
                ],
            },
        },
    }
    decision = {
        "intent_name": "CREATE_APPOINTMENT",
        "awaiting": "CUSTOMER_CONTACT_NAME",
        "facts": {"missing_slots": []},
    }

    rr._inject_rendering_text_impl(result, decision, session_state={})

    assert result["text"].startswith("Thanks—registration AZ213PP noted.")


    assert result["text"].endswith("Before we confirm, may I have your name?")
    assert "for the contact details" not in result["text"]


def test_confirmation_acknowledges_customer_name_correction():
    result = {
        "outcome": {
            "slots": {
                "service_id": "premium full service",
                "date": "2026-08-17",
                "time": "09:45",
            }
        }
    }

    rr.ResponseRenderer().render_confirmation(
        result,
        decision=None,
        customer_name_change={"from": "Godi Nnam", "to": "Godin Nnem"},
    )

    assert result["text"].startswith(
        "No problem—I’ve updated the contact name to Godin Nnem."
    ) or result["text"].startswith(
        "No problem—I've updated the contact name to Godin Nnem."
    )


def test_confirmation_acknowledges_name_correction_on_existing_text():
    result = {
        "text": (
            "You're about to book an Executive Oil Change on July 3 at 10:00 AM. "
            "Would you like me to go ahead?"
        )
    }

    rr.ResponseRenderer().render_confirmation(
        result,
        decision=None,
        customer_name_change={"from": "Godswill Mbaocha", "to": "Godin Nnem"},
    )

    assert "updated the contact name to Godin Nnem" in result["text"]
    assert "Would you like me to go ahead?" in result["text"]


def test_renderer_customer_contact_name_without_booking_slots(monkeypatch):
    """Confirmation prerequisite survives empty missing_slots and promptable_slots."""

    def fail_render_llm(_request):
        raise AssertionError("customer contact name must not use LLM clarification")

    monkeypatch.setattr(rr, "render_llm", fail_render_llm)

    result = {"status": "NEEDS_CLARIFICATION"}
    decision = {
        "intent_name": "CREATE_APPOINTMENT",
        "status": "NEEDS_CLARIFICATION",
        "awaiting": "CUSTOMER_CONTACT_NAME",
        "ask_next": "customer_contact_name",
        "missing_slots": [],
        "plan": {
            "status": "NEEDS_CLARIFICATION",
            "stage": "CONFIRM",
            "awaiting": "CUSTOMER_CONTACT_NAME",
            "ask_next": "customer_contact_name",
            "missing_slots": [],
            "promptable_slots": [],
        },
        "facts": {
            "missing_slots": [],
            "promptable_slots": [],
            "ask_next": "customer_contact_name",
        },
    }

    rr._inject_rendering_text_impl(result, decision, session_state={})

    text = result.get("text") or ""
    assert text == "Before we confirm, may I have your name?"
    assert "for the contact details" not in text


def test_renderer_presents_configured_enum_values_for_engine_type(monkeypatch):
    captured = {}

    def fake_render_llm(request):
        captured["instruction"] = request.render_instruction
        return "Is your vehicle petrol, diesel, hybrid, or EV?"

    monkeypatch.setattr(rr, "render_llm", fake_render_llm)
    result = {"status": "NEEDS_CLARIFICATION"}
    decision = {
        "intent_name": "CREATE_APPOINTMENT",
        "ask_next": "engine_type",
        "missing_slots": ["engine_type", "registration_number"],
        "facts": {
            "missing_slots": ["engine_type", "registration_number"],
            "_entity_schema": {
                "fields": [
                    {
                        "name": "engine_type",
                        "type": "enum",
                        "description": "Vehicle engine type.",
                        "values": ["petrol", "diesel", "hybrid", "ev"],
                        "required": True,
                    }
                ]
            },
        },
    }

    rr._inject_rendering_text_impl(result, decision, session_state={})

    instruction = captured["instruction"]
    assert (
        'Present these options for them to choose from: "petrol", "diesel", '
        '"hybrid", "ev".' in instruction
    )
    assert "registration_number" not in instruction


def test_renderer_hardens_required_field_retry_against_invented_substitutes(
    monkeypatch,
):
    captured = {}

    def fake_render_llm(request):
        captured["instruction"] = request.render_instruction
        return "I need your vehicle registration number to continue."

    monkeypatch.setattr(rr, "render_llm", fake_render_llm)
    result = {"status": "NEEDS_CLARIFICATION"}
    decision = {
        "intent_name": "CREATE_APPOINTMENT",
        "ask_next": "registration_number",
        "missing_slots": ["registration_number"],
        "slot_attempts": {"registration_number": 1},
        "facts": {
            "missing_slots": ["registration_number"],
            "ask_next": "registration_number",
            "_entity_schema": {
                "fields": [
                    {
                        "name": "registration_number",
                        "type": "text",
                        "required": True,
                        "description": "Vehicle registration number.",
                    }
                ]
            },
        },
    }

    rr._inject_rendering_text_impl(result, decision, session_state={})

    instruction = captured["instruction"]
    assert "This field is required" in instruction
    assert "cannot proceed without it" in instruction
    assert "Do not suggest substitute fields" in instruction
    assert "alternative requirements, workarounds, or inferred values" in instruction
    assert "restate the same request briefly without changing its meaning" in instruction
    assert "rephrase naturally" not in instruction
