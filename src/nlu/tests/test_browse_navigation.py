import sys
from unittest.mock import MagicMock, patch

import pytest

sys.modules.setdefault("anthropic", MagicMock())
sys.modules.setdefault("dotenv", MagicMock())

from nlu.stages.stage2.browse_operation import (
    apply_deterministic_browse_operation,
    conversation_presented_availability,
    match_browse_direction,
    utterance_has_date_language,
)
from nlu.pipeline import NLUPipeline, PipelineResult


BROWSE_NEXT_PHRASES = (
    "next",
    "show more",
    "more",
    "show more times",
    "show me additional times",
)
BROWSE_PREVIOUS_PHRASES = (
    "previous",
    "show previous",
    "back",
)
TEMPORAL_PHRASES = (
    "next Tuesday",
    "next week",
    "the next available Tuesday",
    "go back to Tuesday",
    "show more tomorrow",
)

PUNCTUATION_CASES = (
    ("next.", "browse_next"),
    ("Show more!", "browse_next"),
    ("show more times?", "browse_next"),
    ("please show me additional times", "browse_next"),
    ("back.", "browse_previous"),
    ("Previous!", "browse_previous"),
)


def _presented_ctx(*, exhausted: bool = False) -> dict:
    if exhausted:
        assistant = (
            "There are no more times to show for 2026-07-09. "
            "You can try another date, or say previous to go back."
        )
    else:
        assistant = (
            "Here are the available times for 2026-07-09:\n"
            "- 9:00 AM\n"
            "- 10:00 AM\n"
            "- 11:00 AM\n"
            "Which time works for you? You can also say \"show more\"."
        )
    return {
        "last_intent": "CREATE_APPOINTMENT",
        "last_date_proposal": {"mode": "single_day", "start": "2026-07-09"},
        "missing_slots": ["time"],
        "resolved_service_id": "premium haircut",
        "temporal_context_version": 1,
        "presented_options": {
            "reference": "avp1_test",
            "options": [
                {"index": 1, "starts_at": "2026-07-09T09:00:00Z", "label": "9:00 AM"},
                {"index": 2, "starts_at": "2026-07-09T10:00:00Z", "label": "10:00 AM"},
            ],
        },
        "messages": [
            {"role": "user", "text": "actually July 9"},
            {"role": "assistant", "text": assistant},
        ],
        "turns": [
            {"user": "book haircut", "intent": "CREATE_APPOINTMENT", "search_query": None},
            {"user": "premium", "intent": "CREATE_APPOINTMENT", "search_query": None},
            {
                "user": "actually July 9",
                "intent": "CREATE_APPOINTMENT",
                "search_query": None,
                "assistant": assistant,
            },
        ],
    }


def _empty_slm(*, intent="CREATE_APPOINTMENT", operation=None, dates=None, times=None):
    slm = {
        "intent": intent,
        "confidence": 0.85,
        "facts": {
            "dates": list(dates or []),
            "times": list(times or []),
            "date_time_pairs": [],
            "service_id": None,
            "booking_id": None,
        },
        "service_term": None,
        "time_constraint": None,
        "search_query": None,
        "service_candidates": [],
        "temporal": {
            "expression": None,
            "start_date_expression": None,
            "start_time_expression": None,
            "end_date_expression": None,
            "end_time_expression": None,
            "start_date": (dates or [None])[0] if dates else None,
            "start_time": (times or [None])[0] if times else None,
            "end_date": None,
            "end_time": None,
            "mode": "none",
            "confidence": 0.85,
        },
    }
    if operation is not None:
        slm["operation"] = operation
    return slm


class TestPhraseMatching:
    @pytest.mark.parametrize("text", BROWSE_NEXT_PHRASES)
    def test_browse_next_phrases(self, text):
        assert match_browse_direction(text) == "browse_next"

    @pytest.mark.parametrize("text", BROWSE_PREVIOUS_PHRASES)
    def test_browse_previous_phrases(self, text):
        assert match_browse_direction(text) == "browse_previous"

    @pytest.mark.parametrize("text,expected", PUNCTUATION_CASES)
    def test_punctuation_and_fillers(self, text, expected):
        assert match_browse_direction(text) == expected

    @pytest.mark.parametrize("text", TEMPORAL_PHRASES)
    def test_temporal_is_not_browse_phrase(self, text):
        assert match_browse_direction(text) is None
        assert utterance_has_date_language(text) is True


class TestPresentedAvailability:
    def test_detects_listed_times(self):
        assert conversation_presented_availability(_presented_ctx()) is True

    def test_detects_exhaustion_without_clocks(self):
        assert conversation_presented_availability(_presented_ctx(exhausted=True)) is True

    def test_cold_context_is_not_presented(self):
        assert conversation_presented_availability(None) is False
        assert conversation_presented_availability({}) is False
        assert conversation_presented_availability(
            {"last_intent": "CREATE_APPOINTMENT", "missing_slots": ["time"]}
        ) is False

    def test_last_availability_intent_without_presentation_does_not_count(self):
        assert conversation_presented_availability(
            {"last_intent": "AVAILABILITY"}
        ) is False


class TestApplyAfterPresentation:
    @pytest.mark.parametrize(
        "text",
        (
            "Are there more times?",
            "Are there more times for July 9?",
            "Show me the next times",
        ),
    )
    def test_recovers_natural_browse_next_language(self, text):
        out = apply_deterministic_browse_operation(
            text, _empty_slm(), _presented_ctx()
        )
        assert out["operation"] == "browse_next"
        assert out["intent"] == "AVAILABILITY"

    @pytest.mark.parametrize("text", ("Previous times", "Go back"))
    def test_recovers_natural_browse_previous_language(self, text):
        out = apply_deterministic_browse_operation(
            text, _empty_slm(), _presented_ctx()
        )
        assert out["operation"] == "browse_previous"

    def test_structured_presented_options_are_sufficient_context(self):
        context = {
            "presented_options": _presented_ctx()["presented_options"],
            "last_date_proposal": {"start": "2026-07-09"},
        }
        out = apply_deterministic_browse_operation(
            "Are there more times for July 9?", _empty_slm(), context
        )
        assert out["operation"] == "browse_next"

    def test_exact_july_20_regression(self):
        context = {
            "presented_options": {
                "reference": "avp1_a13283ddc3f6640fd2a840f1",
                "options": [
                    {"index": 1, "label": "10:00 AM"},
                    {"index": 2, "label": "11:00 AM"},
                ],
            },
            "last_date_proposal": {
                "mode": "single_day",
                "start": "2026-07-20",
            },
        }
        out = apply_deterministic_browse_operation(
            "Are there more times for July 20?", _empty_slm(), context
        )
        assert out["operation"] == "browse_next"
        assert out["intent"] == "AVAILABILITY"

    def test_revised_date_is_not_browse(self):
        slm = _empty_slm(dates=["2026-07-21"])
        out = apply_deterministic_browse_operation(
            "Are there times for July 21?", slm, _presented_ctx()
        )
        assert out.get("operation") is None
        assert out["facts"]["dates"] == ["2026-07-21"]

    @pytest.mark.parametrize(
        "text",
        (
            "I need more information about the premium haircut.",
            "Go back to changing the service.",
        ),
    )
    def test_unrelated_navigation_words_are_not_browse(self, text):
        out = apply_deterministic_browse_operation(
            text, _empty_slm(), _presented_ctx()
        )
        assert out.get("operation") is None

    @pytest.mark.parametrize("text", BROWSE_NEXT_PHRASES)
    def test_recovers_browse_next(self, text):
        out = apply_deterministic_browse_operation(
            text, _empty_slm(), _presented_ctx()
        )
        assert out["operation"] == "browse_next"
        assert out["intent"] == "AVAILABILITY"
        assert out["facts"]["dates"] == []
        assert out["facts"]["times"] == []

    @pytest.mark.parametrize("text", BROWSE_PREVIOUS_PHRASES)
    def test_recovers_browse_previous(self, text):
        out = apply_deterministic_browse_operation(
            text, _empty_slm(), _presented_ctx()
        )
        assert out["operation"] == "browse_previous"
        assert out["intent"] == "AVAILABILITY"

    def test_recovers_when_model_omits_operation(self):
        out = apply_deterministic_browse_operation(
            "show more", _empty_slm(), _presented_ctx()
        )
        assert out["operation"] == "browse_next"

    def test_clears_invented_temporal_on_browse(self):
        slm = _empty_slm(dates=["2026-07-14"])
        out = apply_deterministic_browse_operation(
            "next", slm, _presented_ctx()
        )
        assert out["operation"] == "browse_next"
        assert out["facts"]["dates"] == []
        assert not (out.get("temporal") or {}).get("start_date")

    @pytest.mark.parametrize("text,expected", PUNCTUATION_CASES)
    def test_recovers_punctuation_and_polite_variations(self, text, expected):
        out = apply_deterministic_browse_operation(
            text, _empty_slm(), _presented_ctx()
        )
        assert out["operation"] == expected


class TestApplyWithoutPresentation:
    @pytest.mark.parametrize("text", BROWSE_NEXT_PHRASES + BROWSE_PREVIOUS_PHRASES)
    def test_does_not_invent_cold_browse(self, text):
        out = apply_deterministic_browse_operation(text, _empty_slm(), None)
        assert "operation" not in out or out.get("operation") is None
        assert out["intent"] == "CREATE_APPOINTMENT"

    def test_strips_model_browse_when_cold(self):
        slm = _empty_slm(operation="browse_next")
        out = apply_deterministic_browse_operation("more", slm, None)
        assert out.get("operation") is None

    @pytest.mark.parametrize("text,expected", PUNCTUATION_CASES)
    def test_suppresses_punctuation_and_polite_variations_when_cold(
        self, text, expected
    ):
        out = apply_deterministic_browse_operation(text, _empty_slm(), None)
        assert out.get("operation") is None

    @pytest.mark.parametrize("operation", ("browse_next", "browse_previous"))
    def test_strips_unknown_model_browse_when_cold(self, operation):
        slm = _empty_slm(operation=operation)
        out = apply_deterministic_browse_operation(
            "continue exploring those results", slm, None
        )
        assert out.get("operation") is None

    @pytest.mark.parametrize("operation", ("browse_next", "browse_previous"))
    def test_retains_unknown_model_browse_after_presentation(self, operation):
        slm = _empty_slm(operation=operation)
        out = apply_deterministic_browse_operation(
            "continue exploring those results", slm, _presented_ctx()
        )
        assert out["operation"] == operation
        assert out["intent"] == "AVAILABILITY"


class TestApplyTemporalLanguage:
    @pytest.mark.parametrize("text", TEMPORAL_PHRASES)
    def test_strips_browse_and_keeps_temporal(self, text):
        slm = _empty_slm(operation="browse_next", dates=["2026-07-14"])
        out = apply_deterministic_browse_operation(
            text, slm, _presented_ctx()
        )
        assert out.get("operation") is None
        assert out["facts"]["dates"] == ["2026-07-14"]
        assert out["intent"] == "CREATE_APPOINTMENT"


def _tenant():
    return {"aliases": {"premium haircut": "premium haircut"}, "booking_mode": "service"}


class TestPipelineRun:
    @pytest.mark.parametrize("text", BROWSE_NEXT_PHRASES)
    def test_run_emits_browse_next_after_presentation(self, text):
        pipeline = NLUPipeline()
        with patch.object(pipeline, "_slm_extract", return_value=_empty_slm()):
            result = pipeline.run(
                text,
                _tenant(),
                now="2026-07-07T10:00:00Z",
                conversation_context=_presented_ctx(),
            )
        assert isinstance(result, PipelineResult)
        assert result.operation == "browse_next"
        assert result.intent["name"] == "AVAILABILITY"
        assert result.understanding == "UNDERSTOOD"

    @pytest.mark.parametrize("text", ("more", "back", "next"))
    def test_run_omits_operation_when_cold(self, text):
        pipeline = NLUPipeline()
        with patch.object(
            pipeline, "_slm_extract", return_value=_empty_slm(operation="browse_next")
        ):
            result = pipeline.run(
                text,
                _tenant(),
                now="2026-07-07T10:00:00Z",
                conversation_context=None,
            )
        assert result.operation is None

    @pytest.mark.parametrize("text", TEMPORAL_PHRASES)
    def test_run_does_not_emit_browse_for_temporal(self, text):
        pipeline = NLUPipeline()
        slm = _empty_slm(operation="browse_next", dates=["2026-07-14"])
        with patch.object(pipeline, "_slm_extract", return_value=slm):
            result = pipeline.run(
                text,
                _tenant(),
                now="2026-07-07T10:00:00Z",
                conversation_context=_presented_ctx(),
            )
        assert result.operation is None

    @pytest.mark.parametrize("operation", ("browse_next", "browse_previous"))
    @pytest.mark.parametrize("presented", (False, True))
    def test_run_context_gates_unknown_model_operation(self, operation, presented):
        pipeline = NLUPipeline()
        slm = _empty_slm(operation=operation)
        with patch.object(pipeline, "_slm_extract", return_value=slm):
            result = pipeline.run(
                "continue exploring those results",
                _tenant(),
                now="2026-07-07T10:00:00Z",
                conversation_context=_presented_ctx() if presented else None,
            )
        assert result.operation == (operation if presented else None)


class TestResolveContract:
    @pytest.fixture
    def client(self):
        from nlu.api import app

        app.config["TESTING"] = True
        with app.test_client() as test_client:
            yield test_client

    @pytest.mark.parametrize(
        "text,operation",
        [(p, "browse_next") for p in BROWSE_NEXT_PHRASES]
        + [(p, "browse_previous") for p in BROWSE_PREVIOUS_PHRASES],
    )
    def test_resolve_includes_top_level_operation(self, client, text, operation):
        from nlu import api as nlu_api

        with patch.object(nlu_api._pipeline, "_slm_extract", return_value=_empty_slm()):
            response = client.post(
                "/resolve",
                json={
                    "text": text,
                    "tenant_context": _tenant(),
                    "conversation_context": _presented_ctx(),
                    "test_now": "2026-07-07T10:00:00Z",
                },
            )
        assert response.status_code == 200
        body = response.get_json()
        assert body["operation"] == operation
        assert body["intent"]["name"] == "AVAILABILITY"
        assert "operation" in body

    @pytest.mark.parametrize("text", TEMPORAL_PHRASES)
    def test_resolve_temporal_omits_operation_and_preserves_date(self, client, text):
        from nlu import api as nlu_api

        slm = _empty_slm(operation="browse_next", dates=["2026-07-14"])
        with patch.object(nlu_api._pipeline, "_slm_extract", return_value=slm):
            response = client.post(
                "/resolve",
                json={
                    "text": text,
                    "tenant_context": _tenant(),
                    "conversation_context": _presented_ctx(),
                    "test_now": "2026-07-07T10:00:00Z",
                },
            )
        assert response.status_code == 200
        body = response.get_json()
        assert "operation" not in body
        assert body["facts"]["dates"] == ["2026-07-14"]

    @pytest.mark.parametrize(
        "text",
        BROWSE_NEXT_PHRASES + BROWSE_PREVIOUS_PHRASES,
    )
    def test_resolve_suppresses_every_browse_phrase_without_presentation(
        self, client, text
    ):
        from nlu import api as nlu_api

        with patch.object(
            nlu_api._pipeline,
            "_slm_extract",
            return_value=_empty_slm(operation="browse_next"),
        ):
            response = client.post(
                "/resolve",
                json={
                    "text": text,
                    "tenant_context": _tenant(),
                    "test_now": "2026-07-07T10:00:00Z",
                },
            )
        assert response.status_code == 200
        assert "operation" not in response.get_json()
