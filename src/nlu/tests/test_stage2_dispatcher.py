"""Unit tests for Stage 2 dispatcher routing and Stage 3 re-dispatch."""

import sys
from unittest.mock import MagicMock, patch

import pytest

sys.modules.setdefault("anthropic", MagicMock())

from nlu.stages.stage2 import dispatcher


def _create_result(intent: str, *, service_term: str = "haircut") -> dict:
    return {
        "intent": intent,
        "confidence": 0.9,
        "facts": {
            "dates": [],
            "times": [],
            "date_time_pairs": [],
            "service_id": None,
            "booking_id": None,
        },
        "service_term": service_term,
        "time_constraint": None,
        "search_query": None,
        "service_candidates": [],
    }


def _faq_result(intent: str = "DISCOVERY", search_query: str = "services") -> dict:
    return {
        "intent": intent,
        "confidence": 0.9,
        "facts": {
            "dates": [],
            "times": [],
            "date_time_pairs": [],
            "service_id": None,
            "booking_id": None,
        },
        "time_constraint": None,
        "search_query": search_query,
    }


@pytest.fixture(autouse=True)
def _clear_extractor_cache():
    dispatcher._instances.clear()
    yield
    dispatcher._instances.clear()


def _mock_extractors(create_return, faq_return=None):
    mock_create = MagicMock()
    mock_create.extract.return_value = create_return
    mocks = {"create": mock_create}
    if faq_return is not None:
        mock_faq = MagicMock()
        mock_faq.extract.return_value = faq_return
        mocks["faq"] = mock_faq

    def _get_extractor(group: str):
        if group not in mocks:
            raise AssertionError(f"Unexpected extractor group: {group}")
        return mocks[group]

    return mocks, _get_extractor


class TestUnknownRoutesToCreate:
    def test_unknown_runs_create_extractor(self):
        create_out = _create_result("CREATE_APPOINTMENT", service_term="flexi")
        mocks, get_ext = _mock_extractors(create_out)

        with patch.object(dispatcher, "_get_extractor", side_effect=get_ext):
            result = dispatcher.extract_slots(
                intent="UNKNOWN",
                text="flexi",
                now="2026-01-13T10:00:00Z",
                tenant_context={"aliases": {}},
                conversation_context={"last_intent": "CREATE_APPOINTMENT"},
            )

        mocks["create"].extract.assert_called_once()
        assert result["intent"] == "CREATE_APPOINTMENT"
        assert result["service_term"] == "flexi"

    def test_confirm_action_still_skips_extraction(self):
        with patch.object(dispatcher, "_get_extractor") as get_ext:
            result = dispatcher.extract_slots(
                intent="CONFIRM_ACTION",
                text="yes",
                now="2026-01-13T10:00:00Z",
                tenant_context={},
            )
        get_ext.assert_not_called()
        assert result["intent"] == "CONFIRM_ACTION"
        assert result["confidence"] == 0.0


class TestStage3Redispatch:
    def test_redispatch_when_validated_intent_changes_group(self):
        create_out = _create_result("DISCOVERY", service_term="haircut")
        faq_out = _faq_result(search_query="haircut services")
        mocks, get_ext = _mock_extractors(create_out, faq_out)

        with patch.object(dispatcher, "_get_extractor", side_effect=get_ext):
            result = dispatcher.extract_slots(
                intent="CREATE_APPOINTMENT",
                text="what haircuts do you offer",
                now="2026-01-13T10:00:00Z",
                tenant_context={"aliases": {}},
            )

        mocks["create"].extract.assert_called_once()
        mocks["faq"].extract.assert_called_once()
        faq_call = mocks["faq"].extract.call_args
        assert faq_call.kwargs["candidate_intent"] == "DISCOVERY"
        assert result["intent"] == "DISCOVERY"
        assert result["search_query"] == "haircut services"
        assert "service_term" not in result or result.get("service_term") is None

    def test_no_redispatch_when_validated_intent_same_group(self):
        create_out = _create_result("CREATE_RESERVATION", service_term="room")
        mocks, get_ext = _mock_extractors(create_out)

        with patch.object(dispatcher, "_get_extractor", side_effect=get_ext):
            result = dispatcher.extract_slots(
                intent="CREATE_APPOINTMENT",
                text="book room march 5-10",
                now="2026-01-13T10:00:00Z",
                tenant_context={"booking_mode": "reservation", "aliases": {}},
            )

        mocks["create"].extract.assert_called_once()
        assert result["intent"] == "CREATE_RESERVATION"

    def test_unknown_to_create_then_faq_on_discovery(self):
        create_out = _create_result("DISCOVERY")
        faq_out = _faq_result(search_query="spa treatments")
        mocks, get_ext = _mock_extractors(create_out, faq_out)

        with patch.object(dispatcher, "_get_extractor", side_effect=get_ext):
            result = dispatcher.extract_slots(
                intent="UNKNOWN",
                text="what spa treatments do you have",
                now="2026-01-13T10:00:00Z",
                tenant_context={"aliases": {}},
            )

        mocks["create"].extract.assert_called_once()
        mocks["faq"].extract.assert_called_once()
        assert result["intent"] == "DISCOVERY"
        assert result["search_query"] == "spa treatments"
