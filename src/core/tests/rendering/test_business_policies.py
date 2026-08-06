"""Deterministic cancellation/rescheduling policy normalization for Business Knowledge."""

from __future__ import annotations

from core.rendering.business_hours import prepare_structured_context_for_render
from core.rendering.business_policies import (
    normalize_cancellation_policy,
    normalize_rescheduling_policy,
)
from core.rendering.llm_renderer import LlmRenderRequest, _build_user_message


def test_free_cancellation_summary():
    summary = normalize_cancellation_policy(
        {"refundType": "free", "refundPercent": 50, "cancelBeforeHours": 24}
    )
    assert summary == (
        "Free cancellation if cancelled at least 24 hours before your appointment."
    )


def test_partial_refund_cancellation_summary():
    summary = normalize_cancellation_policy(
        {"refundType": "partial", "refundPercent": 50, "cancelBeforeHours": 24}
    )
    assert summary == (
        "50% refund if cancelled at least 24 hours before your appointment."
    )


def test_no_refund_cancellation_summary():
    summary = normalize_cancellation_policy(
        {"refundType": "none", "cancelBeforeHours": 24}
    )
    assert summary == "Appointments cancelled within 24 hours are non-refundable."


def test_always_reschedule_summary():
    summary = normalize_rescheduling_policy({"type": "always"})
    assert summary == "Appointments may be rescheduled at any time."


def test_reschedule_until_hours_summary():
    summary = normalize_rescheduling_policy({"type": "until", "hours": 24})
    assert summary == (
        "Appointments may be rescheduled up to 24 hours before the appointment."
    )


def test_no_reschedule_within_hours_summary():
    summary = normalize_rescheduling_policy({"type": "within", "hours": 12})
    assert summary == (
        "Appointments cannot be rescheduled within 12 hours of the appointment."
    )


def test_prepare_injects_policy_summaries_and_strips_raw_enums():
    original = {
        "business_name": "Any Garage",
        "cancellation_policy": {
            "refundType": "free",
            "refundPercent": 50,
            "cancelBeforeHours": 24,
        },
        "rescheduling_policy": {"type": "always"},
    }
    prepared = prepare_structured_context_for_render(original)

    assert prepared["cancellation_summary"] == (
        "Free cancellation if cancelled at least 24 hours before your appointment."
    )
    assert prepared["rescheduling_summary"] == (
        "Appointments may be rescheduled at any time."
    )
    assert "cancellation_policy" not in prepared
    assert "rescheduling_policy" not in prepared
    assert "refundType" not in str(prepared)

    # Original facts untouched for booking / other consumers.
    assert "cancellation_policy" in original
    assert original["cancellation_policy"]["refundType"] == "free"
    assert original["rescheduling_policy"]["type"] == "always"


def test_prepare_accepts_cancellation_rules_alias():
    prepared = prepare_structured_context_for_render(
        {
            "cancellation_rules": {
                "refundType": "partial",
                "refundPercent": 50,
                "cancelBeforeHours": 24,
            },
            "rescheduling_policy": {"type": "until", "hours": 24},
        }
    )
    assert prepared["cancellation_summary"].startswith("50% refund")
    assert "up to 24 hours" in prepared["rescheduling_summary"]
    assert "cancellation_rules" not in prepared


def test_renderer_prompt_contains_normalized_policy_summaries():
    request = LlmRenderRequest(
        render_instruction="Answer the user.",
        user_request="What is your cancellation policy?",
        facts={
            "structured_context": {
                "business_name": "Any Garage",
                "cancellation_policy": {
                    "refundType": "free",
                    "refundPercent": 50,
                    "cancelBeforeHours": 24,
                },
                "rescheduling_policy": {"type": "always"},
            },
            "chunks": [],
        },
    )
    message = _build_user_message(request)

    assert "cancellation_summary" in message
    assert (
        "Free cancellation if cancelled at least 24 hours before your appointment."
        in message
    )
    assert "rescheduling_summary" in message
    assert "Appointments may be rescheduled at any time." in message
    knowledge = message.split("Business Knowledge (Authoritative):", 1)[1]
    knowledge = knowledge.split("When answering from Business Knowledge", 1)[0]
    assert "refundType" not in knowledge
    assert '"type": "always"' not in knowledge and '"type":"always"' not in knowledge
    assert "do not reinterpret" in message.lower()

    # Raw facts still available outside the prompt builder.
    assert (
        request.facts["structured_context"]["cancellation_policy"]["refundType"]
        == "free"
    )


def test_renderer_prompt_covers_partial_and_within_policies():
    request = LlmRenderRequest(
        render_instruction="Answer.",
        facts={
            "structured_context": {
                "cancellation_policy": {
                    "refundType": "none",
                    "cancelBeforeHours": 24,
                },
                "rescheduling_policy": {"type": "within", "hours": 12},
            }
        },
    )
    message = _build_user_message(request)
    assert "Appointments cancelled within 24 hours are non-refundable." in message
    assert (
        "Appointments cannot be rescheduled within 12 hours of the appointment."
        in message
    )
