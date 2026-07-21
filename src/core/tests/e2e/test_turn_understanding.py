"""E2E regression tests for turn.understanding (utterance vs session stickiness)."""

from __future__ import annotations

import uuid

import pytest

from core.adapters.cache.catalog_cache import catalog_cache
from core.api import message as message_api
from core.api.compat import handle_message as real_handle_message
from core.session.session_manager import clear_session
from core.tests.e2e.framework.conversation import (
    FROZEN_TIME,
    HAIRCUT_CATALOG,
    ORG_ID,
    BookingConversation,
    create_slot_availability_client,
)
from core.tests.e2e.framework.fixtures import SCRIPTED_FIXTURE_PARAMS
from core.tests.e2e.framework.runner import run_bundle
from core.tests.e2e.framework.turn_understanding import (
    UnderstandingAwareScriptedLumaClient,
)
from core.tests.e2e.scenarios.turn_understanding import (
    SCENARIOS,
    turn_understanding_scripts,
)
from core.tests.harness.clients import TestCatalogClient
from core.tests.harness.mock_clients import (
    create_mock_booking_client,
    create_mock_organization_client,
)
from core.tests.harness.org_setup import setup_test_org_domain
from core.tests.mocks import reset_booking_counter


@pytest.fixture(autouse=True)
def _deterministic_llm(monkeypatch):
    """Deterministic availability + recovery wording for assertable responses."""

    def _fake_render(request):
        facts = getattr(request, "facts", None) or {}
        if not isinstance(facts, dict):
            facts = {}

        recovery = facts.get("recovery")
        if isinstance(recovery, dict):
            missing = recovery.get("missing_slots") or []
            awaiting = str(recovery.get("awaiting") or "").upper()
            if awaiting == "USER_CONFIRMATION" or recovery.get("conversation_stage") == "CONFIRM":
                return (
                    "I didn't understand that. Please say yes to confirm the booking, "
                    "or no if you'd like to cancel."
                )
            if "time" in missing or awaiting == "TIME_SELECTION":
                return (
                    "I didn't understand that. Please choose a time from the "
                    "available options."
                )
            if missing or recovery.get("selected_service") or recovery.get(
                "selected_date"
            ):
                return (
                    "I didn't understand that. Please try again with a date, time, "
                    "or service."
                )
            # Cold-start / no active workflow: general recovery prompt.
            return (
                "Sorry, I didn't understand that.\n\n"
                "How can I help you today?"
            )

        availability = facts.get("availability")
        if not isinstance(availability, dict):
            availability = {}
        date_label = str(availability.get("date") or "").strip()
        date_phrases = {
            "2026-07-21": "July 21",
            "2026-07-22": "July 22",
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

    monkeypatch.setattr("core.rendering.llm_renderer.render_llm", _fake_render)
    monkeypatch.setattr("core.rendering.response_renderer.render_llm", _fake_render)
    monkeypatch.setattr(
        "core.workflows.availability.pagination.render_llm",
        _fake_render,
    )
    monkeypatch.setattr("core.rendering.recovery_renderer.render_llm", _fake_render)


def _build_understanding_bundle(api_client, monkeypatch, *, start_hours):
    user_id = f"e2e-understanding-{uuid.uuid4().hex[:10]}"
    clear_session(ORG_ID, user_id)
    reset_booking_counter()
    setup_test_org_domain("service")
    catalog_cache._mem_cache.pop((ORG_ID, "service"), None)

    luma_client = UnderstandingAwareScriptedLumaClient(turn_understanding_scripts())
    catalog_client = TestCatalogClient(test_aliases=HAIRCUT_CATALOG, domain="service")
    org_client = create_mock_organization_client(business_category_id=1)
    booking_client = create_mock_booking_client()
    availability_client = create_slot_availability_client(start_hours=start_hours)

    monkeypatch.setattr(message_api, "_booking_client", booking_client)
    monkeypatch.setattr(message_api, "_availability_client", availability_client)

    def handle_message_with_test_deps(**kwargs):
        kwargs.setdefault("luma_client", luma_client)
        kwargs.setdefault("organization_client", org_client)
        kwargs.setdefault("catalog_client", catalog_client)
        kwargs.setdefault("frozen_time", FROZEN_TIME)
        return real_handle_message(**kwargs)

    monkeypatch.setattr(
        message_api._engine, "process_turn", handle_message_with_test_deps
    )
    conv = BookingConversation(api_client, user_id)
    conv.luma_client = luma_client
    return conv, booking_client, availability_client, user_id


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s.pytest_id() for s in SCENARIOS])
def test_turn_understanding_scenario(scenario, api_client, monkeypatch):
    params = dict(SCRIPTED_FIXTURE_PARAMS.get(scenario.fixture) or {})
    start_hours = params.get("start_hours") or (9, 10, 11, 12, 13, 14, 15, 16)
    conv, booking, availability, user_id = _build_understanding_bundle(
        api_client,
        monkeypatch,
        start_hours=start_hours,
    )
    try:
        run_bundle(scenario, (conv, booking, availability))
    finally:
        clear_session(ORG_ID, user_id)
