"""Parameterized runner for HANDLER_DELEGATED digression E2E scenarios."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from core.session.session_manager import clear_session
from core.tests.e2e.framework.conversation import ORG_ID
from core.tests.e2e.framework.fixtures import (
    E2E_FIXTURE_PARAMS,
    build_recorded_bundle,
    live_luma,
)
from core.tests.e2e.framework.runner import run_bundle
from core.tests.e2e.booking import HANDLER_DIGRESSION_IDS, scenarios_with_ids

SCENARIOS = scenarios_with_ids(HANDLER_DIGRESSION_IDS)

_FAQ_DATA = {
    "chunks": [
        {
            "id": 7,
            "source_type": "document",
            "source_id": 12,
            "content": "Haircuts start at $25 and include a wash.",
            "score": 0.84,
        }
    ],
    "structured_context": {
        "business_name": "Glamour Studio",
        "business_phone": "+1 555 000 1234",
        "services": [
            {
                "name": "Haircut",
                "type": "service",
                "config": {"price": 25, "duration": 30},
            }
        ],
        "hours": {"mon": "9am-6pm"},
        "cancellation_policy": {"notice_hours": 24, "fee": "50%"},
        "rescheduling_policy": None,
        "reservations": [],
    },
    "no_hit": False,
}


@pytest.fixture(autouse=True)
def _deterministic_handler_llm(monkeypatch):
    """Deterministic FAQ + confirmation rendering without live LLM."""

    def _fake_render(request):
        facts = getattr(request, "facts", None) or {}
        if not isinstance(facts, dict):
            facts = {}
        if facts.get("confirmation") or facts.get("booking_summary"):
            return "Would you like me to go ahead and book this appointment?"
        resume = facts.get("resume_instruction")
        chunks = facts.get("chunks") or []
        if chunks or facts.get("structured_context") is not None:
            text = "Haircuts start at $25 and include a wash."
            if isinstance(resume, str) and resume.strip():
                if "confirm" in resume.lower():
                    text += "\n\nPlease confirm your appointment to continue."
                else:
                    text += "\n\nShall we continue with your booking?"
            return text
        availability = facts.get("availability")
        if isinstance(availability, dict) and availability.get("times"):
            times = availability.get("times") or []
            lines = "\n".join(f"- {t}" for t in times[:5])
            return f"Here are the available times:\n{lines}\nWhich would you like?"
        return "I can help you continue your booking."

    monkeypatch.setattr("core.rendering.llm_renderer.render_llm", _fake_render)
    monkeypatch.setattr("core.rendering.response_renderer.render_llm", _fake_render)
    monkeypatch.setattr("core.rendering.recovery_renderer.render_llm", _fake_render)


@pytest.mark.parametrize(
    "scenario",
    [pytest.param(s, id=s.pytest_id(), marks=[live_luma]) for s in SCENARIOS],
)
def test_handler_digression_scenario(scenario, api_client, monkeypatch):
    params = dict(E2E_FIXTURE_PARAMS.get(scenario.fixture) or {})
    conv, booking, availability, user_id = build_recorded_bundle(
        api_client,
        monkeypatch,
        **params,
    )
    try:
        with patch("extensions.handlers.adapters.rag.FaqClient") as mock_faq:
            mock_faq.return_value.retrieve.return_value = _FAQ_DATA
            run_bundle(scenario, (conv, booking, availability))
    finally:
        clear_session(ORG_ID, user_id)
