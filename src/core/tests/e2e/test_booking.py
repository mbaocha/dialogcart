"""Parameterized runner for all declarative booking conversation scenarios."""

from __future__ import annotations

import copy
from typing import Any, Dict
from unittest.mock import patch

import pytest

from core.session.session_manager import clear_session
from core.tests.e2e.booking import SCENARIOS
from core.tests.e2e.framework.conversation import ORG_ID, Expect, Scenario, Turn, coerce_turn
from core.tests.e2e.framework.fixtures import (
    DEFAULT_BUSINESS_CATEGORY,
    E2E_FIXTURE_PARAMS,
    build_recorded_bundle,
    live_luma,
)
from core.tests.e2e.framework.runner import run_bundle

_DATE_PHRASES = {
    # Keep TARGET_DATE (2026-07-03) as raw ISO so recorded Luma keys for
    # dotted-time selection stay stable (legacy booking runner behaviour).
    "2026-07-20": "July 20",
    "2026-07-21": "July 21",
    "2026-07-22": "July 22",
    "2026-07-23": "July 23",
    "2026-07-24": "July 24",
}

_FAQ_DATA = {
    "chunks": [
        {
            "id": 7,
            "source_type": "document",
            "source_id": 12,
            "content": "Haircuts start at $25 and include a wash.",
            "score": 0.84,
        }
    ],
    "structured_context": {
        "business_name": "Glamour Studio",
        "business_phone": "+1 555 000 1234",
        "services": [
            {
                "name": "Haircut",
                "type": "service",
                "config": {"price": 25, "duration": 30},
            }
        ],
        "hours": {"mon": "9am-6pm"},
        "cancellation_policy": {"notice_hours": 24, "fee": "50%"},
        "rescheduling_policy": None,
        "reservations": [],
    },
    "no_hit": False,
}


_CURRENCY_SYMBOLS = {
    "GBP": "£",
    "USD": "$",
    "EUR": "€",
}


def _render_structured_service_discovery(
    structured_context: Any,
) -> str | None:
    """Project structured service facts into deterministic discovery text."""
    if not isinstance(structured_context, dict):
        return None

    services = structured_context.get("services")
    if not isinstance(services, list):
        return None

    lines = []
    for service in services:
        if not isinstance(service, dict) or not service.get("name"):
            continue

        label = str(service["name"])
        details = []
        config = service.get("config")
        if isinstance(config, dict):
            price = config.get("price")
            if price is not None:
                currency = str(config.get("currency") or "").strip().upper()
                symbol = _CURRENCY_SYMBOLS.get(currency)
                if symbol:
                    details.append(f"{symbol}{price}")
                elif currency:
                    details.append(f"{currency} {price}")
                else:
                    details.append(str(price))

            duration = config.get("duration")
            if duration is None:
                duration = config.get("durationMinutes")
            if duration is not None:
                details.append(f"{duration} minutes")

        if details:
            label += f" — {details[0]}"
            if len(details) > 1:
                label += f" ({', '.join(details[1:])})"
        lines.append(label)

    if not lines:
        return None

    prompt = (
        "Which one would you like to book?"
        if len(lines) > 1
        else "Would you like to book this service?"
    )
    return "\n\n".join([*lines, prompt])


@pytest.fixture(autouse=True)
def _deterministic_booking_llm(monkeypatch):
    """Deterministic rendering for all booking conversation-state suites."""

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

        # OFF_TOPIC interruption: answer + resume guidance back into booking.
        resume = facts.get("resume_instruction")
        if facts.get("off_topic_query") or facts.get("answer") is not None:
            answer = facts.get("answer") or "I can only help with bookings."
            text = str(answer)
            if isinstance(resume, str) and resume.strip():
                lowered = resume.lower()
                history = getattr(request, "conversation_history", None) or []
                show_more = False
                for msg in history:
                    if not isinstance(msg, dict):
                        continue
                    content = str(msg.get("content") or msg.get("text") or "").lower()
                    if "show more" in content:
                        show_more = True
                        break
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

        browse_status = str(
            facts.get("browse_status") or availability.get("browse_status") or ""
        ).lower()
        if browse_status in {"exhausted"} or "no more" in browse_status:
            return "There are no more available times to show from your last search."

        missing = facts.get("missing_slots") or getattr(request, "missing_slots", None) or []
        if isinstance(missing, list) and "service_id" in missing:
            return (
                "Which service would you like me to check availability for?\n"
                "- Premium haircut\n"
                "- Flexi haircut + pruning"
            )

        # HANDLER_DELEGATED FAQ/discovery — structured business facts are the
        # deterministic source of truth; chunks remain a compatibility fallback.
        chunks = facts.get("chunks") or []
        structured_response = _render_structured_service_discovery(
            facts.get("structured_context")
        )
        if structured_response:
            return structured_response
        if isinstance(chunks, list) and chunks:
            resume = facts.get("resume_instruction")
            text = "Haircuts start at $25 and include a wash."
            if isinstance(resume, str) and resume.strip():
                if "confirm" in resume.lower():
                    text += "\n\nPlease confirm your appointment to continue."
                else:
                    text += "\n\nShall we continue with your booking?"
            return text

        instruction = str(getattr(request, "render_instruction", "") or "").lower()
        if "missing" in instruction and "time" in instruction:
            return (
                "I didn't quite catch which time you meant. "
                "Could you choose one of the available times, such as 9:00 AM or 9:30 AM?"
            )

        date_label = str(availability.get("date") or "").strip()
        date_phrase = _DATE_PHRASES.get(date_label, date_label)
        times = availability.get("times") or []
        if times:
            lines = "\n".join(f"- {t}" for t in times[:5])
            if date_phrase:
                text = (
                    f"Here are the available times for {date_phrase}:\n"
                    f"{lines}\nWhich would you like?"
                )
            else:
                text = f"Here are the available times:\n{lines}\nWhich would you like?"
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
            if more_count > 0 or suggested == "show more" or (
                isinstance(browse_hints, dict) and browse_hints.get("has_more_any")
            ):
                text += ' You can also say "show more" to see additional times.'
            return text
        if date_phrase:
            return (
                f"Here are the available appointment times for {date_phrase}. "
                "Which would you like?"
            )
        return (
            "Which service would you like me to check availability for?\n"
            "- Premium haircut\n"
            "- Flexi haircut + pruning"
        )

    monkeypatch.setattr("core.rendering.llm_renderer.render_llm", _fake_render)
    monkeypatch.setattr("core.rendering.response_renderer.render_llm", _fake_render)
    monkeypatch.setattr(
        "core.workflows.availability.pagination.render_llm",
        _fake_render,
    )
    monkeypatch.setattr("core.rendering.recovery_renderer.render_llm", _fake_render)
    monkeypatch.setattr("core.rendering.off_topic_renderer.render_llm", _fake_render)


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


def _run_with_category_faq_mock(scenario, bundle) -> None:
    """Run with category-owned FAQ evidence available to every scenario."""
    conv = bundle[0]
    chunks = conv.faq_chunks
    if not chunks and conv.structured_business_context == _FAQ_DATA["structured_context"]:
        # Preserve the established beauty-salon FAQ evidence exactly.
        chunks = copy.deepcopy(_FAQ_DATA["chunks"])
    faq_data = {
        "chunks": chunks,
        "structured_context": conv.structured_business_context,
        "no_hit": False,
    }
    with patch("extensions.handlers.adapters.rag.FaqClient") as mock_faq:
        mock_faq.return_value.retrieve.return_value = faq_data
        run_bundle(scenario, bundle)


@pytest.mark.parametrize("scenario", _booking_scenario_params())
def test_booking_scenario(scenario, api_client, monkeypatch):
    # Availability layout from scenario.fixture; vertical from owning module.
    if scenario.fixture == "booking":
        # Match historic booking_conversation multi-slot layout (10, 11).
        params: Dict[str, Any] = {"start_hours": (10, 11)}
    else:
        params = dict(E2E_FIXTURE_PARAMS.get(scenario.fixture) or {})

    business_category = getattr(
        scenario, "business_category", None
    ) or DEFAULT_BUSINESS_CATEGORY

    conv, booking, availability, user_id = build_recorded_bundle(
        api_client,
        monkeypatch,
        business_category=business_category,
        **params,
    )
    try:
        _run_with_category_faq_mock(scenario, (conv, booking, availability))
    finally:
        clear_session(ORG_ID, user_id)
