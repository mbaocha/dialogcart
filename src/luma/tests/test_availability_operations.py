"""Tests for availability operation detection (Luma → Core contract)."""

import json
from datetime import datetime, timezone

import pytest

from luma.grouping.availability_operations import (
    BROWSE_NEXT,
    BROWSE_PREVIOUS,
    detect_availability_operation,
)
from luma.grouping.reservation_intent_resolver import (
    AVAILABILITY,
    ReservationIntentResolver,
    resolve_intent,
)
from luma.pipeline import LumaPipeline
from luma.response.builder import ResponseBuilder

# Contract utterances from AVAILABILITY_INTERACTION_CONTRACT / product requirements.
CONTRACT_BROWSE_NEXT_UTTERANCES = [
    "show more",
    "show more times",
    "show additional times",
    "next page",
    "more availability",
]

CONTRACT_BROWSE_PREVIOUS_UTTERANCES = [
    "previous page",
    "earlier times",
    "go back",
]


@pytest.mark.parametrize(
    "text,expected_operation",
    [
        ("show more", BROWSE_NEXT),
        ("show more times", BROWSE_NEXT),
        ("show additional times", BROWSE_NEXT),
        ("show me additional times", BROWSE_NEXT),
        ("next page", BROWSE_NEXT),
        ("more availability", BROWSE_NEXT),
        ("see more times please", BROWSE_NEXT),
        ("later times", BROWSE_NEXT),
        ("previous page", BROWSE_PREVIOUS),
        ("earlier times", BROWSE_PREVIOUS),
        ("go back", BROWSE_PREVIOUS),
        ("show earlier slots", BROWSE_PREVIOUS),
    ],
)
def test_detect_availability_operation_browse_phrases(text, expected_operation):
    assert detect_availability_operation(text) == expected_operation


@pytest.mark.parametrize("text", CONTRACT_BROWSE_NEXT_UTTERANCES)
def test_contract_browse_next_utterances(text):
    assert detect_availability_operation(text) == BROWSE_NEXT


@pytest.mark.parametrize("text", CONTRACT_BROWSE_PREVIOUS_UTTERANCES)
def test_contract_browse_previous_utterances(text):
    assert detect_availability_operation(text) == BROWSE_PREVIOUS


@pytest.mark.parametrize(
    "text",
    [
        "show availability",
        "availability for July 8",
        "do you have any slots?",
        "what times are available?",
        "book a haircut tomorrow",
    ],
)
def test_detect_availability_operation_not_browse(text):
    assert detect_availability_operation(text) is None


@pytest.mark.parametrize(
    "sentence,expected_operation",
    [
        ("show more", BROWSE_NEXT),
        ("show more times", BROWSE_NEXT),
        ("show additional times", BROWSE_NEXT),
        ("next page", BROWSE_NEXT),
        ("more availability", BROWSE_NEXT),
        ("previous page", BROWSE_PREVIOUS),
        ("earlier times", BROWSE_PREVIOUS),
        ("go back", BROWSE_PREVIOUS),
    ],
)
def test_resolve_intent_emits_availability_with_operation(sentence, expected_operation):
    intent, confidence, operation = resolve_intent(sentence, {})
    assert intent == AVAILABILITY
    assert confidence >= 0.9
    assert operation == expected_operation


@pytest.mark.parametrize("booking_mode", ["service", "reservation"])
@pytest.mark.parametrize(
    "sentence,expected_operation",
    [
        ("show more times", BROWSE_NEXT),
        ("go back", BROWSE_PREVIOUS),
    ],
)
def test_browse_preempts_locked_booking_mode(sentence, expected_operation, booking_mode):
    resolver = ReservationIntentResolver()
    intent, confidence, operation = resolver.resolve_intent(
        sentence, {}, booking_mode=booking_mode
    )
    assert intent == AVAILABILITY
    assert confidence >= 0.9
    assert operation == expected_operation


def test_resolve_intent_availability_search_has_no_operation():
    intent, confidence, operation = resolve_intent(
        "availability for July 8",
        {"dates": [{"text": "July 8"}]},
    )
    assert intent == AVAILABILITY
    assert operation is None
    assert confidence >= 0.8


@pytest.mark.parametrize(
    "sentence,expected_operation",
    [
        ("show more", BROWSE_NEXT),
        ("more availability", BROWSE_NEXT),
        ("previous page", BROWSE_PREVIOUS),
    ],
)
def test_pipeline_intent_stage_emits_structured_operation(sentence, expected_operation):
    pipeline = LumaPipeline()
    now = datetime(2026, 1, 13, 10, 0, tzinfo=timezone.utc)
    results = pipeline.run(
        sentence,
        now=now,
        tenant_context={"booking_mode": "service"},
    )
    intent_stage = results["stages"]["intent"]
    assert intent_stage["intent"] == AVAILABILITY
    assert intent_stage["operation"] == expected_operation


@pytest.mark.parametrize(
    "operation",
    [BROWSE_NEXT, BROWSE_PREVIOUS],
)
def test_response_builder_emits_operation_without_planner_fields(operation):
    body = ResponseBuilder().build_response_body(
        intent_payload={"name": AVAILABILITY, "confidence": 0.95},
        facts={},
        operation=operation,
    )
    assert body["intent"]["name"] == AVAILABILITY
    assert body["operation"] == operation
    serialized = json.dumps(body)
    assert "SEARCH_AVAILABILITY" not in serialized
    assert "action" not in body
    assert "plan" not in body


def test_more_availability_is_browse_not_search():
    """'more availability' must paginate, not trigger a fresh availability search."""
    intent, _, operation = resolve_intent("more availability", {})
    assert intent == AVAILABILITY
    assert operation == BROWSE_NEXT

    intent_with_date, _, operation_with_date = resolve_intent(
        "availability for July 8",
        {"dates": [{"text": "July 8"}]},
    )
    assert intent_with_date == AVAILABILITY
    assert operation_with_date is None
