"""E2E regression: explicit date must survive service clarification."""

from __future__ import annotations

import pytest

from core.session.session_manager import clear_session
from core.tests.e2e.framework.conversation import ORG_ID
from core.tests.e2e.framework.fixtures import (
    SCRIPTED_FIXTURE_PARAMS,
    build_scripted_bundle,
)
from core.tests.e2e.framework.runner import run_bundle
from core.tests.e2e.scenarios.date_survives_service_clarification import (
    JULY_20,
    JULY_21,
    SCENARIOS,
    date_survives_service_clarification_scripts,
)


@pytest.fixture(autouse=True)
def _deterministic_availability_llm(monkeypatch):
    """Avoid ANTHROPIC_API_KEY; surface search_date in rendered text for asserts."""

    def _fake_render(request):
        facts = getattr(request, "facts", None) or {}
        if not isinstance(facts, dict):
            facts = {}
        availability = facts.get("availability")
        if not isinstance(availability, dict):
            availability = {}
        date_label = str(availability.get("date") or "").strip()
        if date_label == JULY_21:
            date_phrase = "July 21"
        elif date_label == JULY_20:
            date_phrase = "July 20"
        else:
            date_phrase = date_label
        times = availability.get("times") or []
        if times:
            lines = "\n".join(f"- {t}" for t in times[:5])
            if date_phrase:
                return (
                    f"Here are the available times for {date_phrase}:\n"
                    f"{lines}\nWhich would you like?"
                )
            return f"Here are the available times:\n{lines}\nWhich would you like?"
        if date_phrase:
            return (
                f"Here are the available appointment times for {date_phrase}. "
                "Which would you like?"
            )
        return "Here are the available appointment times. Which would you like?"

    monkeypatch.setattr("core.rendering.llm_renderer.render_llm", _fake_render)
    monkeypatch.setattr(
        "core.rendering.response_renderer.render_llm",
        _fake_render,
    )


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s.pytest_id() for s in SCENARIOS])
def test_date_survives_service_clarification_scenario(
    scenario, api_client, monkeypatch
):
    params = dict(SCRIPTED_FIXTURE_PARAMS.get(scenario.fixture) or {})
    conv, booking, availability, user_id = build_scripted_bundle(
        api_client,
        monkeypatch,
        extra_scripts=date_survives_service_clarification_scripts(),
        **params,
    )
    try:
        run_bundle(scenario, (conv, booking, availability))
    finally:
        clear_session(ORG_ID, user_id)
