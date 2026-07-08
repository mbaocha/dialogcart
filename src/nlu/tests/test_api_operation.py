"""API contract tests for structured operation emission."""

import sys
from unittest.mock import MagicMock, patch

import pytest

sys.modules.setdefault("anthropic", MagicMock())
sys.modules.setdefault("dotenv", MagicMock())

from nlu.api import app
from nlu.pipeline import PipelineResult


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as test_client:
        yield test_client


@patch("nlu.api._pipeline.run")
def test_resolve_emits_operation(mock_run, client):
    mock_run.return_value = PipelineResult(
        intent={"name": "AVAILABILITY", "confidence": 0.95},
        facts={
            "dates": [],
            "times": [],
            "date_time_pairs": [],
            "service_id": "premium haircut",
            "booking_id": None,
        },
        operation="browse_next",
    )

    response = client.post(
        "/resolve",
        json={
            "text": "are there other slots",
            "tenant_context": {"aliases": {}, "booking_mode": "service"},
        },
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["intent"]["name"] == "AVAILABILITY"
    assert body["operation"] == "browse_next"


@patch("nlu.api._pipeline.run")
def test_resolve_omits_operation_when_none(mock_run, client):
    mock_run.return_value = PipelineResult(
        intent={"name": "CREATE_APPOINTMENT", "confidence": 0.95},
        facts={
            "dates": ["2026-07-08"],
            "times": [],
            "date_time_pairs": [],
            "service_id": "haircut",
            "booking_id": None,
        },
    )

    response = client.post(
        "/resolve",
        json={
            "text": "book haircut tomorrow",
            "tenant_context": {"aliases": {}, "booking_mode": "service"},
        },
    )

    assert response.status_code == 200
    body = response.get_json()
    assert "operation" not in body
