"""Parameterized runner for turn.understanding E2E scenarios (RecordingLumaClient)."""

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
from core.tests.e2e.scenarios.turn_understanding import SCENARIOS


@pytest.fixture(autouse=True)
def _deterministic_availability_llm(monkeypatch):
    """Avoid ANTHROPIC_API_KEY dependency for availability / recovery text."""

    def _fake_render(request):
        facts = getattr(request, "facts", None) or {}
        if not isinstance(facts, dict):
            facts = {}
        availability = facts.get("availability")
        if not isinstance(availability, dict):
            availability = {}
        # Recovery before availability: acknowledgement owns the reply; times are support.
        recovery = facts.get("recovery") if isinstance(facts.get("recovery"), dict) else {}
        if recovery:
            # Mirror recovery_renderer._has_active_workflow_evidence: cold-start
            # UNKNOWN must stay intent-neutral (no booking time/date prompts).
            presented = recovery.get("presented_availability")
            times: list = []
            if isinstance(presented, dict):
                raw_times = presented.get("times") or []
                if isinstance(raw_times, list):
                    times = [str(t) for t in raw_times if t][:5]
            if not times:
                raw_times = availability.get("times") or []
                if isinstance(raw_times, list):
                    times = [str(t) for t in raw_times if t][:5]
            has_workflow = bool(
                recovery.get("awaiting")
                or recovery.get("awaiting_slot")
                or recovery.get("missing_slots")
                or recovery.get("selected_service")
                or recovery.get("selected_date")
                or recovery.get("selected_time")
                or presented
            )
            if has_workflow:
                if times:
                    lines = "\n".join(f"- {t}" for t in times)
                    return (
                        "Sorry, I didn't understand that. "
                        "Please choose one of these available times:\n"
                        f"{lines}"
                    )
                return (
                    "Sorry, I didn't understand that. "
                    "Could you tell me what time you'd like?"
                )
            return (
                "Sorry, I didn't understand that. "
                "Could you rephrase, or tell me how I can help?"
            )
        date_label = str(availability.get("date") or "").strip()
        times = availability.get("times") or []
        if times:
            lines = "\n".join(f"- {t}" for t in times[:5])
            if date_label:
                return (
                    f"Here are the available times for {date_label}:\n"
                    f"{lines}\nWhich would you like?"
                )
            return f"Here are the available times:\n{lines}\nWhich would you like?"
        if date_label:
            return (
                f"Here are the available appointment times for {date_label}. "
                "Which would you like?"
            )
        return "Here are the available appointment times. Which would you like?"

    monkeypatch.setattr("core.rendering.llm_renderer.render_llm", _fake_render)
    monkeypatch.setattr("core.rendering.response_renderer.render_llm", _fake_render)
    monkeypatch.setattr(
        "core.workflows.availability.pagination.render_llm",
        _fake_render,
    )
    monkeypatch.setattr("core.rendering.recovery_renderer.render_llm", _fake_render)


@pytest.mark.parametrize(
    "scenario",
    [
        pytest.param(s, id=s.pytest_id(), marks=[live_luma])
        for s in SCENARIOS
    ],
)
def test_turn_understanding_scenario(scenario, api_client, monkeypatch):
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
