"""Tests for handler_router.resolve_handler."""

import pytest

from core.planning.policy.handler_router import reload_handlers, resolve_handler


reload_handlers()

RAG_INTENTS = [
    "DISCOVERY",
    "DETAILS",
    "QUOTE",
    "RECOMMENDATION",
    "GENERAL_INQUIRY",
    "PAYMENT",
    "PAYMENT_STATUS",
]


@pytest.mark.parametrize("intent", RAG_INTENTS)
def test_rag_intents_resolve_to_rag(intent):
    assert resolve_handler(intent) == "rag"


def test_off_topic_is_core_digression_not_extension_handler():
    assert resolve_handler("OFF_TOPIC") is None


@pytest.mark.parametrize(
    "intent",
    ["CREATE_APPOINTMENT", "CREATE_RESERVATION", "MODIFY_BOOKING", "CANCEL_BOOKING"],
)
def test_core_booking_intents_return_none(intent):
    assert resolve_handler(intent) is None


def test_unknown_returns_none():
    assert resolve_handler("UNKNOWN") is None


def test_empty_string_returns_none():
    assert resolve_handler("") is None
