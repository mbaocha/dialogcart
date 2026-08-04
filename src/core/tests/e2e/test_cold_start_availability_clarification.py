"""E2E regression: cold-start dated AVAILABILITY must clarify service."""

from __future__ import annotations

import pytest

from core.session.session_manager import clear_session
from core.tests.e2e.framework.conversation import ORG_ID
from core.tests.e2e.framework.fixtures import (
    E2E_FIXTURE_PARAMS,
    build_recorded_bundle,
    live_luma,
)
from core.tests.e2e.framework.runner import run_bundle
from core.tests.e2e.scenarios.cold_start_availability_clarification import SCENARIOS


@pytest.fixture(autouse=True)
def _deterministic_clarification_llm(monkeypatch):
    """Avoid ANTHROPIC_API_KEY for clarification / availability text."""

    def _fake_render(request):
        facts = getattr(request, "facts", None) or {}
        if not isinstance(facts, dict):
            facts = {}
        missing = facts.get("missing_slots") or getattr(request, "missing_slots", None) or []
        if isinstance(missing, list) and "service_id" in missing:
            return (
                "Which service would you like me to check availability for?\n"
                "- Premium haircut\n"
                "- Flexi haircut + pruning"
            )
        availability = facts.get("availability")
        if isinstance(availability, dict) and (availability.get("times") or []):
            times = availability.get("times") or []
            lines = "\n".join(f"- {t}" for t in times[:5])
            return f"Here are the available times:\n{lines}\nWhich would you like?"
        return "Which service would you like me to check availability for?"

    monkeypatch.setattr("core.rendering.llm_renderer.render_llm", _fake_render)
    monkeypatch.setattr(
        "core.rendering.response_renderer.render_llm",
        _fake_render,
    )


@pytest.mark.parametrize(
    "scenario",
    [
        pytest.param(s, id=s.pytest_id(), marks=[live_luma])
        for s in SCENARIOS
    ],
)
def test_cold_start_availability_clarification_scenario(
    scenario, api_client, monkeypatch
):
    params = dict(E2E_FIXTURE_PARAMS.get(scenario.fixture) or {})
    conv, booking, availability, user_id = build_recorded_bundle(
        api_client,
        monkeypatch,
        **params,
    )
    try:
        run_bundle(scenario, (conv, booking, availability))
    finally:
        clear_session(ORG_ID, user_id)
