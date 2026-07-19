"""E2E regression tests for confirmation interruption (ANOTHER_REQUEST)."""

from __future__ import annotations

import pytest

from core.session.session_manager import clear_session
from core.tests.e2e.framework.conversation import ORG_ID
from core.tests.e2e.framework.fixtures import (
    SCRIPTED_FIXTURE_PARAMS,
    build_scripted_bundle,
)
from core.tests.e2e.framework.runner import run_bundle
from core.tests.e2e.scenarios.confirmation_interruption import (
    SCENARIOS,
    confirmation_interruption_scripts,
)


@pytest.fixture(autouse=True)
def _deterministic_availability_llm(monkeypatch):
    """Avoid ANTHROPIC_API_KEY dependency for availability rendering asserts."""

    def _fake_render(request):
        facts = getattr(request, "facts", None) or {}
        availability = facts.get("availability") if isinstance(facts, dict) else {}
        if not isinstance(availability, dict):
            availability = {}
        times = availability.get("times") or []
        if times:
            lines = "\n".join(f"- {t}" for t in times[:5])
            return f"Here are the available times:\n{lines}\nWhich would you like?"
        return "Here are the available appointment times. Which would you like?"

    monkeypatch.setattr(
        "core.rendering.response_renderer.render_llm",
        _fake_render,
    )


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s.pytest_id() for s in SCENARIOS])
def test_confirmation_interruption_scenario(scenario, api_client, monkeypatch):
    params = dict(SCRIPTED_FIXTURE_PARAMS.get(scenario.fixture) or {})
    conv, booking, availability, user_id = build_scripted_bundle(
        api_client,
        monkeypatch,
        extra_scripts=confirmation_interruption_scripts(),
        **params,
    )
    try:
        run_bundle(scenario, (conv, booking, availability))
    finally:
        clear_session(ORG_ID, user_id)
