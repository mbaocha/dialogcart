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
def test_resolve_emits_temporal(mock_run, client):
    mock_run.return_value = PipelineResult(
        intent={"name": "CREATE_APPOINTMENT", "confidence": 0.95},
        facts={
            "dates": ["2026-07-19"],
            "times": ["09:00"],
            "date_time_pairs": [],
            "service_id": "haircut",
            "booking_id": None,
        },
        temporal={
            "expression": "2026-07-19 at 09:00",
            "start_date_expression": None,
            "start_time_expression": None,
            "end_date_expression": None,
            "end_time_expression": None,
            "start_date": "2026-07-19",
            "start_time": "09:00",
            "end_date": None,
            "end_time": None,
            "confidence": 0.95,
        },
    )

    response = client.post(
        "/resolve",
        json={
            "text": "book haircut tomorrow at 9am",
            "tenant_context": {"aliases": {}, "booking_mode": "service"},
        },
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["facts"]["dates"] == ["2026-07-19"]
    assert body["temporal"]["start_date"] == "2026-07-19"
    assert body["temporal"]["start_time"] == "09:00"


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
