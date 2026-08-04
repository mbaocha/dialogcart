"""E2E regression: browse exhaustion must not poison subsequent SEARCH_AVAILABILITY."""

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
from core.tests.e2e.scenarios.browse_exhaustion_search import SCENARIOS


@pytest.fixture(autouse=True)
def _deterministic_availability_llm(monkeypatch):
    """Avoid ANTHROPIC_API_KEY; render only the current search date + times."""

    def _fake_render(request):
        facts = getattr(request, "facts", None) or {}
        if not isinstance(facts, dict):
            facts = {}
        availability = facts.get("availability")
        if not isinstance(availability, dict):
            availability = {}
        browse_status = str(
            facts.get("browse_status")
            or availability.get("browse_status")
            or ""
        ).lower()
        if browse_status in {"exhausted"} or "no more" in browse_status:
            return "There are no more available times to show from your last search."
        date_label = str(availability.get("date") or "").strip()
        if date_label == "2026-07-21":
            date_phrase = "July 21"
        elif date_label == "2026-07-20":
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
def test_browse_exhaustion_search_scenario(scenario, api_client, monkeypatch):
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
