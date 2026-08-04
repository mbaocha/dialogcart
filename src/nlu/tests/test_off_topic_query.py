"""Stage 2 FAQ OFF_TOPIC: canonical off_topic_query + answer evidence; search_query null."""

import sys
from unittest.mock import MagicMock

sys.modules.setdefault("anthropic", MagicMock())

from nlu.stages.stage2.groups import faq


def test_off_topic_merge_returns_canonical_query_and_null_search_query():
    result = faq._merge(
        {
            "validated_intent": "OFF_TOPIC",
            "confidence": 0.95,
            "search_query": "should be stripped",
            "off_topic_query": "Who is the president of Nigeria?",
            "answerable": True,
            "answer": "Bola Ahmed Tinubu is the president of Nigeria.",
        },
        "OFF_TOPIC",
    )
    assert result["intent"] == "OFF_TOPIC"
    assert result["search_query"] is None
    assert result["off_topic_query"] == "Who is the president of Nigeria?"
    assert result["answerable"] is True
    assert result["answer"] == "Bola Ahmed Tinubu is the president of Nigeria."


def test_discovery_merge_nulls_off_topic_query():
    result = faq._merge(
        {
            "validated_intent": "DISCOVERY",
            "confidence": 0.9,
            "search_query": "available services",
            "off_topic_query": "Who is the president of Nigeria?",
            "answerable": True,
            "answer": "should be stripped",
        },
        "DISCOVERY",
    )
    assert result["intent"] == "DISCOVERY"
    assert result["search_query"] == "available services"
    assert result["off_topic_query"] is None
    assert result["answerable"] is None
    assert result["answer"] is None


def test_off_topic_blank_query_becomes_null():
    result = faq._merge(
        {
            "validated_intent": "OFF_TOPIC",
            "confidence": 0.9,
            "search_query": None,
            "off_topic_query": "   ",
            "answerable": False,
            "answer": None,
        },
        "OFF_TOPIC",
    )
    assert result["search_query"] is None
    assert result["off_topic_query"] is None
    assert result["answerable"] is False
    assert result["answer"] is None


def test_off_topic_answerable_without_answer_becomes_unanswerable():
    result = faq._merge(
        {
            "validated_intent": "OFF_TOPIC",
            "confidence": 0.9,
            "off_topic_query": "Which phone should I buy?",
            "answerable": True,
            "answer": "  ",
        },
        "OFF_TOPIC",
    )
    assert result["answerable"] is False
    assert result["answer"] is None
