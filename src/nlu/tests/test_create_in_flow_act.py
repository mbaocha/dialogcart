"""Tests for Stage 2 in-flow booking act validation."""

from nlu.stages.stage2.groups.create import _merge
from nlu.stages.shared.in_flow_act import promote_in_flow_booking_intent


class TestPromoteInFlowBookingIntent:
    def test_promotes_gibberish_with_active_booking(self):
        ctx = {"last_intent": "CREATE_APPOINTMENT"}
        assert promote_in_flow_booking_intent("UNKNOWN", "aaaa", ctx) == "CREATE_APPOINTMENT"

    def test_no_promotion_without_context(self):
        assert promote_in_flow_booking_intent("UNKNOWN", "aaaa", None) == "UNKNOWN"

    def test_no_promotion_with_booking_verb(self):
        ctx = {"last_intent": "CREATE_APPOINTMENT"}
        assert (
            promote_in_flow_booking_intent("UNKNOWN", "book for tomorrow", ctx)
            == "UNKNOWN"
        )

    def test_preserves_non_unknown_intent(self):
        ctx = {"last_intent": "CREATE_APPOINTMENT"}
        assert (
            promote_in_flow_booking_intent("DISCOVERY", "aaaa", ctx) == "DISCOVERY"
        )


class TestCreateMergeInFlowValidation:
    def test_merge_promotes_unknown_gibberish_with_empty_facts(self):
        ctx = {"last_intent": "CREATE_APPOINTMENT"}
        raw = {
            "validated_intent": None,
            "confidence": 0.8,
            "facts": {"service_term": None},
            "temporal": {
                "expression": None,
                "start_date_expression": None,
                "start_time_expression": None,
                "end_date_expression": None,
                "end_time_expression": None,
                "start_date": None,
                "start_time": None,
                "end_date": None,
                "end_time": None,
                "mode": "none",
                "confidence": 0.0,
            },
        }
        result = _merge(
            raw,
            "UNKNOWN",
            text="aaaa",
            conversation_context=ctx,
        )
        assert result["intent"] == "CREATE_APPOINTMENT"
        assert result["facts"]["dates"] == []
        assert result["facts"]["times"] == []
        assert result["service_term"] is None

    def test_merge_preserves_extracted_service_for_premium(self):
        ctx = {"last_intent": "CREATE_APPOINTMENT"}
        raw = {
            "validated_intent": "CREATE_APPOINTMENT",
            "confidence": 0.9,
            "facts": {"service_term": "premium"},
            "temporal": {
                "expression": None,
                "start_date_expression": None,
                "start_time_expression": None,
                "end_date_expression": None,
                "end_time_expression": None,
                "start_date": None,
                "start_time": None,
                "end_date": None,
                "end_time": None,
                "mode": "none",
                "confidence": 0.0,
            },
        }
        result = _merge(
            raw,
            "CREATE_APPOINTMENT",
            text="premium",
            conversation_context=ctx,
        )
        assert result["intent"] == "CREATE_APPOINTMENT"
        assert result["service_term"] == "premium"
