"""E2E tests for OFF_TOPIC intent routing and session preservation."""

from __future__ import annotations

import uuid

import pytest

from core.adapters.cache.catalog_cache import catalog_cache
from core.api import message as message_api
from core.api.compat import handle_message as real_handle_message
from core.planning.policy.handler_router import reload_handlers
from core.session.session_manager import clear_session
from core.tests.e2e.framework.conversation import (
    FROZEN_TIME,
    HAIRCUT_CATALOG,
    ORG_ID,
    BookingConversation,
    create_slot_availability_client,
)
from core.tests.e2e.framework.runner import run_bundle
from core.tests.e2e.framework.turn_understanding import (
    UnderstandingAwareScriptedLumaClient,
)
from core.tests.e2e.scenarios.off_topic import SCENARIOS, off_topic_scripts
from core.tests.harness.clients import TestCatalogClient
from core.tests.harness.mock_clients import (
    create_mock_booking_client,
    create_mock_organization_client,
)
from core.tests.harness.org_setup import setup_test_org_domain
from core.tests.mocks import reset_booking_counter
from extensions.handlers.bootstrap import register_default_handlers


@pytest.fixture(autouse=True)
def _deterministic_llm_and_handlers(monkeypatch):
    """Deterministic scope-decline text + ensure off_topic handler is registered."""
    reload_handlers()
    register_default_handlers()

    def _fake_render(request):
        facts = getattr(request, "facts", None) or {}
        if not isinstance(facts, dict):
            facts = {}

        if facts.get("scope") == "off_topic":
            answer = facts.get("answer")
            resume = facts.get("resume_instruction") or ""
            parts = []
            if isinstance(answer, str) and answer.strip():
                parts.append(answer.strip())
            elif facts.get("answerable") is False:
                parts.append("I can't answer that right now.")
            # Deterministic resume wording from workflow-owned instruction signals.
            lowered_resume = resume.lower()
            if "premium haircut" in lowered_resume or "which service" in lowered_resume:
                parts.append(
                    "Now let's continue your booking.\n"
                    "Which service would you like to book?\n"
                    "- Premium haircut\n"
                    "- Flexi haircut + pruning"
                )
            elif "confirm" in lowered_resume:
                parts.append("Shall I go ahead and confirm that appointment?")
            elif "choose a time" in lowered_resume or "which time" in lowered_resume:
                parts.append("Which time would you like?")
            elif "invite" in lowered_resume or "book a service" in lowered_resume:
                parts.append(
                    "I'm here to help with this business's services and appointments. "
                    "How can I help you today?"
                )
            elif facts.get("booking_active"):
                parts.append("Let's continue your booking.")
            else:
                parts.append(
                    "I'm here to help with this business's services and appointments. "
                    "How can I help you today?"
                )
            return "\n\n".join(parts)

        recovery = facts.get("recovery")
        if isinstance(recovery, dict):
            return "Sorry, I didn't understand that.\n\nHow can I help you today?"

        availability = facts.get("availability")
        if isinstance(availability, dict) and availability.get("times"):
            times = availability.get("times") or []
            lines = "\n".join(f"- {t}" for t in times[:5])
            return f"Here are the available times:\n{lines}\nWhich would you like?"

        return "How can I help you with your booking today?"

    monkeypatch.setattr("core.rendering.llm_renderer.render_llm", _fake_render)
    monkeypatch.setattr("core.rendering.response_renderer.render_llm", _fake_render)
    monkeypatch.setattr("core.api.message.render_llm", _fake_render)
    monkeypatch.setattr("core.rendering.recovery_renderer.render_llm", _fake_render)


def _build_off_topic_bundle(api_client, monkeypatch):
    user_id = f"e2e-off-topic-{uuid.uuid4().hex[:10]}"
    clear_session(ORG_ID, user_id)
    reset_booking_counter()
    setup_test_org_domain("service")
    catalog_cache._mem_cache.pop((ORG_ID, "service"), None)

    luma_client = UnderstandingAwareScriptedLumaClient(off_topic_scripts())
    catalog_client = TestCatalogClient(test_aliases=HAIRCUT_CATALOG, domain="service")
    org_client = create_mock_organization_client(business_category_id=1)
    booking_client = create_mock_booking_client()
    availability_client = create_slot_availability_client(
        start_hours=(9, 10, 11, 12, 13, 14, 15, 16)
    )

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
def test_off_topic_scenario(scenario, api_client, monkeypatch):
    conv, booking, availability, user_id = _build_off_topic_bundle(
        api_client, monkeypatch
    )
    try:
        run_bundle(scenario, (conv, booking, availability))
    finally:
        clear_session(ORG_ID, user_id)
