"""E2E regression tests for confirmation interruption (ANOTHER_REQUEST)."""

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
from core.tests.e2e.booking import (
    CONFIRMATION_INTERRUPTION_IDS,
    scenarios_with_ids,
)

SCENARIOS = scenarios_with_ids(CONFIRMATION_INTERRUPTION_IDS)


@pytest.fixture(autouse=True)
def _deterministic_availability_llm(monkeypatch):
    """Avoid ANTHROPIC_API_KEY dependency for availability rendering asserts."""

    def _fake_render(request):
        facts = getattr(request, "facts", None) or {}
        availability = facts.get("availability") if isinstance(facts, dict) else {}
        if not isinstance(availability, dict):
            availability = {}
        date_label = str(availability.get("date") or "").strip()
        date_phrases = {
            "2026-07-02": "July 2",
            "2026-07-03": "July 3",
            "2026-07-20": "July 20",
            "2026-07-21": "July 21",
            "2026-07-22": "July 22",
            "2026-07-23": "July 23",
            "2026-07-24": "July 24",
        }
        date_phrase = date_phrases.get(date_label, date_label)
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

    monkeypatch.setattr(
        "core.rendering.llm_renderer.render_llm",
        _fake_render,
    )
    monkeypatch.setattr(
        "core.rendering.response_renderer.render_llm",
        _fake_render,
    )
    monkeypatch.setattr(
        "core.workflows.availability.pagination.render_llm",
        _fake_render,
    )


@pytest.mark.parametrize(
    "scenario",
    [
        pytest.param(s, id=s.pytest_id(), marks=[live_luma])
        for s in SCENARIOS
    ],
)
def test_confirmation_interruption_scenario(scenario, api_client, monkeypatch):
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
