from unittest.mock import MagicMock

from core.engine.conversation_engine import ConversationEngine
from core.engine.execution_coordinator import ExecutionGateResult
from core.rendering import response_renderer


def test_skipped_execution_renders_required_slot_clarification(monkeypatch):
    monkeypatch.setattr(
        response_renderer,
        "render_llm",
        lambda _request: "I couldn't identify that registration number. What is it?",
    )
    decision = {
        "intent_name": "CREATE_APPOINTMENT",
        "ask_next": "registration_number",
        "missing_slots": ["registration_number"],
        "facts": {
            "ask_next": "registration_number",
            "missing_slots": ["registration_number"],
        },
        "plan": {
            "status": "NEEDS_CLARIFICATION",
            "ask_next": "registration_number",
            "missing_slots": ["registration_number"],
        },
    }
    response = {
        "success": True,
        "outcome": {
            "status": "NEEDS_CLARIFICATION",
            "missing_slots": ["registration_number"],
        },
    }
    plan = {
        "status": "NEEDS_CLARIFICATION",
        "action": None,
        "_decision": decision,
    }
    gate = ExecutionGateResult(
        path="skipped",
        response=response,
        plan=plan,
        plan_status="NEEDS_CLARIFICATION",
        plan_action=None,
    )
    stages = MagicMock()
    stages.finish.side_effect = lambda value, **_kwargs: value

    result = ConversationEngine()._finish_gate(
        stages,
        gate,
        session_state={},
        availability_client=MagicMock(),
        user_text="aa1239",
    )

    assert result["text"] == (
        "I couldn't identify that registration number. What is it?"
    )
