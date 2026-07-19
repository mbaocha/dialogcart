"""E2E regression: browse exhaustion must not poison subsequent SEARCH_AVAILABILITY."""

from __future__ import annotations

import pytest

from core.session.session_manager import clear_session
from core.tests.e2e.framework.conversation import ORG_ID
from core.tests.e2e.framework.fixtures import (
    SCRIPTED_FIXTURE_PARAMS,
    build_scripted_bundle,
)
from core.tests.e2e.framework.runner import run_bundle
from core.tests.e2e.scenarios.browse_exhaustion_search import (
    SCENARIOS,
    browse_exhaustion_search_scripts,
)


@pytest.fixture(autouse=True)
def _deterministic_availability_llm(monkeypatch):
    """Avoid ANTHROPIC_API_KEY dependency for availability rendering asserts."""

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
        if browse_status in {"exhausted", "no_more_times_for_date"} or "no more" in browse_status:
            return "There are no more available times to show from your last search."
        times = availability.get("times") or []
        if times:
            lines = "\n".join(f"- {t}" for t in times[:5])
            return f"Here are the available times:\n{lines}\nWhich would you like?"
        return "Here are the available appointment times. Which would you like?"

    monkeypatch.setattr("core.rendering.llm_renderer.render_llm", _fake_render)
    monkeypatch.setattr(
        "core.workflows.availability.pagination.render_llm",
        _fake_render,
    )


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s.pytest_id() for s in SCENARIOS])
def test_browse_exhaustion_search_scenario(scenario, api_client, monkeypatch):
    params = dict(SCRIPTED_FIXTURE_PARAMS.get(scenario.fixture) or {})
    conv, booking, availability, user_id = build_scripted_bundle(
        api_client,
        monkeypatch,
        extra_scripts=browse_exhaustion_search_scripts(),
        **params,
    )
    try:
        run_bundle(scenario, (conv, booking, availability))
    finally:
        clear_session(ORG_ID, user_id)
