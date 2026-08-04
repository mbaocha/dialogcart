"""Parameterized runner for OFF_TOPIC E2E scenarios (RecordingLumaClient)."""

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
from core.tests.e2e.scenarios.off_topic import SCENARIOS


@pytest.fixture(autouse=True)
def _deterministic_off_topic_llm(monkeypatch):
    """Avoid ANTHROPIC_API_KEY for OFF_TOPIC / booking continuation text."""

    def _history_mentions_show_more(request) -> bool:
        history = getattr(request, "conversation_history", None) or []
        for msg in history:
            if not isinstance(msg, dict):
                continue
            content = str(msg.get("content") or msg.get("text") or "").lower()
            if "show more" in content:
                return True
        return False

    def _availability_wants_show_more(availability: dict) -> bool:
        more_count = availability.get("more_count") or 0
        try:
            more_count = int(more_count)
        except (TypeError, ValueError):
            more_count = 0
        browse_hints = availability.get("browse_hints")
        suggested = (
            browse_hints.get("suggested_next")
            if isinstance(browse_hints, dict)
            else None
        )
        return more_count > 0 or suggested == "show more" or bool(
            isinstance(browse_hints, dict) and browse_hints.get("has_more_any")
        )

    def _fake_render(request):
        facts = getattr(request, "facts", None) or {}
        if not isinstance(facts, dict):
            facts = {}

        recovery = facts.get("recovery") if isinstance(facts.get("recovery"), dict) else None
        if recovery:
            # Mid-booking unrecognized: acknowledge, then resume pending step.
            parts = ["Sorry, I didn't understand that."]
            if recovery.get("presented_availability") or recovery.get("missing_slots"):
                parts.append("Returning to your booking...")
                parts.append("Which of these times works best?")
                if _history_mentions_show_more(request):
                    parts.append('You can also say "show more" to see additional times.')
            elif recovery.get("awaiting") == "USER_CONFIRMATION":
                parts.append("Please confirm your appointment to continue.")
            else:
                parts.append("Could you rephrase, or tell me how I can help?")
            return "\n\n".join(parts)

        resume = facts.get("resume_instruction")
        if facts.get("off_topic_query") or facts.get("answer") is not None:
            answer = facts.get("answer") or "I can only help with bookings."
            text = str(answer)
            # Deterministic stand-in for LLM following the Resume section.
            if isinstance(resume, str) and resume.strip():
                lowered = resume.lower()
                show_more = _history_mentions_show_more(request)
                show_more_clause = (
                    ' You can also say "show more" to see additional times.'
                    if show_more
                    else ""
                )
                if "already-presented times" in lowered or (
                    "choose a time" in lowered and "already offered" in lowered
                ):
                    text += (
                        "\n\nWhich time works best for your appointment?"
                        + show_more_clause
                    )
                elif "confirm" in lowered:
                    text += "\n\nPlease confirm your appointment to continue."
                elif "service" in lowered:
                    text += "\n\nWhich service would you like to book?"
                elif "date" in lowered:
                    text += "\n\nWhich date would you like?"
                elif "invite" in lowered:
                    text += (
                        "\n\nI can help you book a service or appointment "
                        "with this business."
                    )
                else:
                    text += "\n\nShall we continue with your booking?"
            return text
        availability = facts.get("availability")
        if isinstance(availability, dict) and availability.get("times"):
            times = availability.get("times") or []
            lines = "\n".join(f"- {t}" for t in times[:5])
            text = f"Here are the available times:\n{lines}\nWhich would you like?"
            if _availability_wants_show_more(availability):
                text += ' You can also say "show more" to see additional times.'
            return text
        return (
            "I can help you continue your booking. "
            "What time works for your appointment?"
        )

    monkeypatch.setattr("core.rendering.llm_renderer.render_llm", _fake_render)
    monkeypatch.setattr("core.rendering.response_renderer.render_llm", _fake_render)
    monkeypatch.setattr("core.rendering.off_topic_renderer.render_llm", _fake_render)
    monkeypatch.setattr("core.rendering.recovery_renderer.render_llm", _fake_render)


@pytest.mark.parametrize(
    "scenario",
    [
        pytest.param(s, id=s.pytest_id(), marks=[live_luma])
        for s in SCENARIOS
    ],
)
def test_off_topic_scenario(scenario, api_client, monkeypatch):
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
