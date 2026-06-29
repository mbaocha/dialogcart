"""Tests for FaqClient — mocks httpx, asserts POST body and response unwrapping."""

import os
from unittest.mock import patch

import pytest

os.environ.setdefault("CORE_EXECUTION_MODE", "test")

from extensions.handlers.clients.faq_client import FaqClient
from core.orchestration.errors import UpstreamError


def _wrapped_response(data: dict) -> dict:
    return {"success": True, "data": data, "message": None}


def _raw_data() -> dict:
    return {
        "chunks": [
            {
                "id": 7,
                "source_type": "document",
                "source_id": 12,
                "content": "Haircuts start at $25.",
                "score": 0.84,
            }
        ],
        "structured_context": {
            "business_name": "Glamour Studio",
            "business_phone": "+1 555 000 1234",
            "services": [
                {"name": "Haircut", "type": "service", "config": {"price": 25, "duration": 30}}
            ],
            "hours": {"mon": "9am-6pm"},
            "cancellation_policy": {"notice_hours": 24, "fee": "50%"},
            "rescheduling_policy": None,
            "reservations": [],
        },
        "no_hit": False,
    }


class TestFaqClientRetrieve:
    def test_post_body_uses_query_field(self):
        """POST body must use 'query', not 'search_query'."""
        client = FaqClient(base_url="http://test")
        with patch.object(client, "_request", return_value=_wrapped_response(_raw_data())) as mock_req:
            client.retrieve(organization_id=1, query="haircut price")
            mock_req.assert_called_once_with(
                "POST",
                "/api/internal/faq/retrieve",
                json={"organization_id": 1, "query": "haircut price"},
            )

    def test_unwraps_success_envelope(self):
        """Unwraps { success, data } and returns chunks/structured_context/no_hit."""
        client = FaqClient(base_url="http://test")
        with patch.object(client, "_request", return_value=_wrapped_response(_raw_data())):
            result = client.retrieve(organization_id=1, query="haircut price")
        assert isinstance(result["chunks"], list)
        assert len(result["chunks"]) == 1
        assert result["chunks"][0]["id"] == 7
        assert result["structured_context"]["business_name"] == "Glamour Studio"
        assert result["no_hit"] is False

    def test_accepts_already_unwrapped_data(self):
        """Handles direct data object (no success envelope) defensively."""
        client = FaqClient(base_url="http://test")
        with patch.object(client, "_request", return_value=_raw_data()):
            result = client.retrieve(organization_id=1, query="haircut price")
        assert result["no_hit"] is False
        assert len(result["chunks"]) == 1

    def test_no_hit_true_returned(self):
        """no_hit=True is preserved in return value."""
        data = _raw_data()
        data["no_hit"] = True
        data["chunks"] = []
        client = FaqClient(base_url="http://test")
        with patch.object(client, "_request", return_value=_wrapped_response(data)):
            result = client.retrieve(organization_id=1, query="unknown topic")
        assert result["no_hit"] is True
        assert result["chunks"] == []

    def test_raises_upstream_error_on_bad_shape(self):
        """Unexpected response shape raises UpstreamError."""
        client = FaqClient(base_url="http://test")
        with patch.object(client, "_request", return_value={"error": "something"}):
            with pytest.raises(UpstreamError):
                client.retrieve(organization_id=1, query="test")

    def test_upstream_error_propagates(self):
        """UpstreamError from _request propagates to caller."""
        client = FaqClient(base_url="http://test")
        with patch.object(client, "_request", side_effect=UpstreamError("timeout")):
            with pytest.raises(UpstreamError):
                client.retrieve(organization_id=1, query="test")

    def test_empty_chunks_normalized_to_list(self):
        """chunks=None in response normalizes to []."""
        data = _raw_data()
        data["chunks"] = None
        client = FaqClient(base_url="http://test")
        with patch.object(client, "_request", return_value=_wrapped_response(data)):
            result = client.retrieve(organization_id=1, query="test")
        assert result["chunks"] == []
