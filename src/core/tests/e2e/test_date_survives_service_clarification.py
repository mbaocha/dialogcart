"""E2E regression: explicit date must survive service clarification."""

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
from core.tests.e2e.booking import DATE_SURVIVES_IDS, scenarios_with_ids
from core.tests.e2e.booking.service_selection import JULY_20, JULY_21, JULY_23

SCENARIOS = scenarios_with_ids(DATE_SURVIVES_IDS)

_DATE_PHRASES = {
    JULY_20: "July 20",
    JULY_21: "July 21",
    JULY_23: "July 23",
}


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
        date_phrase = _DATE_PHRASES.get(date_label, date_label)
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


@pytest.mark.parametrize(
    "scenario",
    [
        pytest.param(s, id=s.pytest_id(), marks=[live_luma])
        for s in SCENARIOS
    ],
)
def test_date_survives_service_clarification_scenario(
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
