"""Tests for LLM-derived availability operation from Stage 2 availability extractor."""

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.modules.setdefault("anthropic", MagicMock())

from nlu.stages.stage2.groups import availability as availability_group


def _tool_input(
    *,
    operation=None,
    validated_intent="AVAILABILITY",
    dates=None,
    times=None,
    service_term=None,
):
    dates = list(dates or [])
    times = list(times or [])
    start_date = dates[0] if dates else None
    end_date = dates[1] if len(dates) > 1 else None
    start_time = times[0] if times else None
    return {
        "validated_intent": validated_intent,
        "confidence": 0.92,
        "operation": operation,
        "temporal": {
            "expression": None,
            "start_date_expression": None,
            "start_time_expression": None,
            "end_date_expression": None,
            "end_time_expression": None,
            "start_date": start_date,
            "start_time": start_time,
            "end_date": end_date,
            "end_time": None,
            "mode": None,
            "confidence": 0.92,
        },
        "facts": {
            "service_term": service_term,
        },
        "time_constraint": None,
        "service_candidates": [],
    }


def _mock_llm_response(tool_input: dict):
    block = SimpleNamespace(
        type="tool_use",
        name="extract_availability_slots",
        input=tool_input,
    )
    return SimpleNamespace(content=[block])


class TestAvailabilityMerge:
    def test_merge_browse_next(self):
        result = availability_group._merge(
            _tool_input(operation="browse_next"),
            "AVAILABILITY",
        )
        assert result["intent"] == "AVAILABILITY"
        assert result["operation"] == "browse_next"
        assert result["facts"]["dates"] == []

    def test_merge_browse_previous(self):
        result = availability_group._merge(
            _tool_input(operation="browse_previous"),
            "AVAILABILITY",
        )
        assert result["operation"] == "browse_previous"

    def test_merge_null_operation_omitted(self):
        result = availability_group._merge(
            _tool_input(operation=None, dates=["2026-07-08"]),
            "AVAILABILITY",
        )
        assert "operation" not in result
        assert result["facts"]["dates"] == ["2026-07-08"]

    def test_merge_rejects_unknown_operation(self):
        result = availability_group._merge(
            _tool_input(operation="search"),
            "AVAILABILITY",
        )
        assert "operation" not in result


class TestAvailabilityToolSchema:
    def test_tool_schema_includes_operation(self):
        schema = availability_group._TOOL["input_schema"]
        assert "operation" in schema["properties"]
        assert schema["properties"]["operation"]["enum"] == [
            "browse_next",
            "browse_previous",
            None,
        ]
        assert "operation" in schema["required"]


class TestAvailabilityExtractorOperation:
    """Verify operation is returned from mocked LLM tool output."""

    @pytest.mark.parametrize(
        "text,operation",
        [
            ("next", "browse_next"),
            ("show more", "browse_next"),
            ("more", "browse_next"),
            ("previous", "browse_previous"),
            ("show previous", "browse_previous"),
            ("back", "browse_previous"),
        ],
    )
    def test_extractor_returns_operation_from_llm(self, text, operation):
        extractor = availability_group.AvailabilityGroupExtractor()
        tool_input = _tool_input(operation=operation)
        mock_response = _mock_llm_response(tool_input)

        with patch.object(
            extractor._client.messages,
            "create",
            return_value=mock_response,
        ) as mock_create:
            result = extractor.extract(
                text=text,
                now="2026-07-07T10:00:00Z",
                tenant_context={"aliases": {}},
                candidate_intent="AVAILABILITY",
            )

        mock_create.assert_called_once()
        assert result["operation"] == operation
        assert result["intent"] == "AVAILABILITY"

    def test_extractor_search_leaves_operation_unset(self):
        extractor = availability_group.AvailabilityGroupExtractor()
        tool_input = _tool_input(
            operation=None, dates=["2026-07-08"], service_term="haircut"
        )
        mock_response = _mock_llm_response(tool_input)

        with patch.object(
            extractor._client.messages,
            "create",
            return_value=mock_response,
        ):
            result = extractor.extract(
                text="availability for July 8",
                now="2026-07-07T10:00:00Z",
                tenant_context={"aliases": {}},
                candidate_intent="AVAILABILITY",
            )

        assert "operation" not in result
        assert result["facts"]["dates"] == ["2026-07-08"]
        assert result["facts"]["service_id"] is None
        assert result["service_term"] == "haircut"


class TestPipelineOperationPropagation:
    def test_bind_calendar_passes_operation_to_pipeline_result(self):
        from nlu.pipeline import NLUPipeline

        pipeline = NLUPipeline()
        slm = {
            "intent": "AVAILABILITY",
            "confidence": 0.9,
            "operation": "browse_next",
            "facts": {
                "dates": [],
                "times": [],
                "date_time_pairs": [],
                "service_id": "premium haircut",
                "booking_id": None,
            },
            "time_constraint": None,
            "search_query": None,
            "service_candidates": [],
        }
        result = pipeline._bind_calendar(
            slm,
            {"booking_mode": "service", "aliases": {}},
            "2026-07-07T10:00:00Z",
        )
        assert result.operation == "browse_next"
        assert result.intent["name"] == "AVAILABILITY"
