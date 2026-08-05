"""Parameterized runner for all declarative booking conversation scenarios."""

from __future__ import annotations

import pytest

from core.tests.e2e.framework.conversation import ORG_ID, Expect, Scenario, Turn, coerce_turn
from core.tests.e2e.framework.fixtures import (
    E2E_FIXTURE_PARAMS,
    build_recorded_bundle,
    live_luma,
)
from core.tests.e2e.framework.runner import run_bundle
from core.tests.e2e.booking import SCENARIOS
from core.session.session_manager import clear_session


@pytest.fixture(autouse=True)
def _deterministic_booking_llm(monkeypatch):
    """Avoid live LLM for availability / confirmation rendering in booking E2E."""

    def _fake_render(request):
        from core.rendering.availability_renderer import resolve_time_mismatch_text

        facts = getattr(request, "facts", None) or {}
        if not isinstance(facts, dict):
            facts = {}
        availability = facts.get("availability")
        if not isinstance(availability, dict):
            availability = {}
        time_resolution = facts.get("time_resolution")
        if isinstance(time_resolution, dict):
            outcome = time_resolution.get("outcome") or time_resolution.get("status")
            if outcome in ("TIME_MATCH_MISMATCH", "no_match"):
                times = availability.get("times") or []
                return resolve_time_mismatch_text(
                    requested_time=(
                        str(time_resolution["requested_time"])
                        if time_resolution.get("requested_time") is not None
                        else None
                    ),
                    times=list(times) if isinstance(times, list) else None,
                    alternatives=(
                        list(time_resolution.get("alternatives") or [])
                        if isinstance(time_resolution.get("alternatives"), list)
                        else None
                    ),
                    mismatch_location=(
                        str(time_resolution["mismatch_location"])
                        if time_resolution.get("mismatch_location") is not None
                        else None
                    ),
                    search_date=(
                        str(availability["date"])
                        if availability.get("date") is not None
                        else (
                            str(availability["search_date"])
                            if availability.get("search_date") is not None
                            else None
                        )
                    ),
                    browse_hints=(
                        availability.get("browse_hints")
                        if isinstance(availability.get("browse_hints"), dict)
                        else None
                    ),
                    recovery_actions=(
                        time_resolution.get("recovery_actions")
                        if isinstance(time_resolution.get("recovery_actions"), list)
                        else (
                            availability.get("recovery_actions")
                            if isinstance(availability.get("recovery_actions"), list)
                            else None
                        )
                    ),
                )
        # Recovery before availability: acknowledgement owns the reply; times are support.
        recovery = facts.get("recovery") if isinstance(facts.get("recovery"), dict) else {}
        if recovery:
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
        if facts.get("confirmation") or facts.get("booking_summary"):
            return "Would you like me to go ahead and book this appointment?"
        instruction = str(getattr(request, "render_instruction", "") or "").lower()
        if "missing" in instruction and "time" in instruction:
            return (
                "I didn't quite catch which time you meant. "
                "Could you choose one of the available times, such as 9:00 AM or 9:30 AM?"
            )
        date_label = str(availability.get("date") or "").strip()
        times = availability.get("times") or []
        if times:
            lines = "\n".join(f"- {t}" for t in times[:5])
            if date_label == "2026-07-23":
                date_phrase = "July 23"
            elif date_label == "2026-07-24":
                date_phrase = "July 24"
            else:
                date_phrase = date_label
            if date_phrase:
                return (
                    f"Here are the available times for {date_phrase}:\n"
                    f"{lines}\nWhich would you like?"
                )
            return f"Here are the available times:\n{lines}\nWhich would you like?"
        if date_label:
            if date_label == "2026-07-23":
                date_phrase = "July 23"
            elif date_label == "2026-07-24":
                date_phrase = "July 24"
            else:
                date_phrase = date_label
            return (
                f"Here are the available appointment times for {date_phrase}. "
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


def test_conversation_dsl_expect_aliases():
    checks = Expect(
        planner="READY",
        confirmation=None,
        execution="availability",
        time_match="EXACT",
    ).to_assert_turn_kwargs()
    assert checks["planner_status"] == "READY"
    assert checks["confirmation"] is None
    assert checks["execution_type"] == "availability"
    assert checks["time_match_outcome"] == "TIME_MATCH_EXACT"


def test_conversation_dsl_coerces_turn_shorthand():
    scenario = Scenario(
        "Demo",
        Turn("hi", Expect(planner="READY")),
        ("premium", Expect(action="SEARCH_AVAILABILITY")),
    )
    assert len(scenario.turns) == 2
    assert coerce_turn(("x", {"planner": "READY"})).expect.planner == "READY"


def _booking_scenario_params():
    # All booking E2E uses RecordingLumaClient (cache miss → live /resolve).
    return [
        pytest.param(scenario, id=scenario.pytest_id(), marks=[live_luma])
        for scenario in SCENARIOS
    ]


@pytest.mark.parametrize("scenario", _booking_scenario_params())
def test_booking_scenario(scenario, api_client, monkeypatch, request):
    if scenario.fixture == "booking":
        bundle = request.getfixturevalue("booking_conversation")
        run_bundle(scenario, bundle)
        return

    params = dict(E2E_FIXTURE_PARAMS.get(scenario.fixture) or {})
    conv, booking, availability, user_id = build_recorded_bundle(
        api_client, monkeypatch, **params
    )
    try:
        run_bundle(scenario, (conv, booking, availability))
    finally:
        clear_session(ORG_ID, user_id)
