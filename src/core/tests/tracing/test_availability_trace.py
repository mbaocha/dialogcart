"""Tests for SEARCH_AVAILABILITY availability HTTP diagnostic tracing."""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest

from core.adapters.errors import AvailabilityRejectedError, UpstreamError
from core.execution.clients.availability_client import AvailabilityClient
from core.execution.dispatcher import (
    _finalize_availability_search,
    _normalize_availability_response,
)
from core.tracing.availability import (
    AVAILABILITY_REQUEST_ID,
    AVAILABILITY_RESPONSE_ID,
    count_raw_availability_slots,
)
from core.tracing.decision_trace import (
    TRACE_ENV_VAR,
    TurnTrace,
    finalize_turn_trace,
    reset_decision_trace_state,
    trace_to_dict,
)


@pytest.fixture(autouse=True)
def _enable_trace(monkeypatch):
    monkeypatch.setenv(TRACE_ENV_VAR, "1")
    reset_decision_trace_state()
    yield
    reset_decision_trace_state()


def test_count_raw_availability_slots_supports_internal_api_shape():
    response = {
        "success": True,
        "data": {
            "available_slots": [
                {"start_time": "2026-07-09T09:00:00.000Z", "end_time": "2026-07-09T09:30:00.000Z"},
                {"start_time": "2026-07-09T10:00:00.000Z", "end_time": "2026-07-09T10:30:00.000Z"},
            ]
        },
    }
    assert count_raw_availability_slots(response) == 2


def test_finalize_availability_search_emits_request_and_response():
    TurnTrace.begin(user_id="u1", text="premium")
    client = AvailabilityClient(base_url="http://availability.test")

    mock_response = httpx.Response(
        200,
        json={
            "success": True,
            "data": {
                "available_slots": [
                    {
                        "start_time": "2026-07-09T09:00:00.000Z",
                        "end_time": "2026-07-09T09:30:00.000Z",
                    }
                ]
            },
        },
        request=httpx.Request(
            "GET",
            "http://availability.test/api/internal/availability/services",
        ),
    )

    with patch.object(client._client, "request", return_value=mock_response):
        raw = client.get_service_availability(
            organization_id=1,
            service_id=18,
            date="2026-07-09",
        )

    normalized = _finalize_availability_search(raw)
    assert normalized["type"] == "availability"
    assert len(normalized["slots"]) == 1

    trace = trace_to_dict(finalize_turn_trace() or {})
    record_ids = {record["id"] for record in trace["records"]}
    assert AVAILABILITY_REQUEST_ID in record_ids
    assert AVAILABILITY_RESPONSE_ID in record_ids

    request = next(r for r in trace["records"] if r["id"] == AVAILABILITY_REQUEST_ID)
    response = next(r for r in trace["records"] if r["id"] == AVAILABILITY_RESPONSE_ID)

    assert request["facts"]["organization_id"] == 1
    assert request["facts"]["service_id"] == 18
    assert request["facts"]["date"] == "2026-07-09"
    assert request["facts"]["time_constraint"] is None
    assert request["facts"]["field_provenance"]["time"]["omitted"] is True
    assert request["facts"]["field_provenance"]["service_id"]["value"] == 18
    assert request["facts"]["_forensic"]["query_params"]["service_id"] == 18

    assert response["facts"]["http_status"] == 200
    assert response["facts"]["available_slot_count"] == 1
    assert response["facts"]["normalized_slot_count"] == 1
    assert response["facts"]["_forensic"]["raw_response_body"]

    reset_decision_trace_state()


def test_availability_client_emits_error_response_trace():
    TurnTrace.begin(user_id="u1", text="premium")
    client = AvailabilityClient(base_url="http://availability.test")

    mock_response = httpx.Response(
        502,
        text='{"message":"upstream down"}',
        request=httpx.Request(
            "GET",
            "http://availability.test/api/internal/availability/services",
        ),
    )

    with patch.object(client._client, "request", return_value=mock_response):
        with pytest.raises(Exception):
            client.get_service_availability(
                organization_id=1,
                service_id=18,
                date="2026-07-09",
            )

    trace = trace_to_dict(finalize_turn_trace() or {})
    record_ids = {record["id"] for record in trace["records"]}
    assert AVAILABILITY_REQUEST_ID in record_ids
    assert AVAILABILITY_RESPONSE_ID in record_ids
    response = next(r for r in trace["records"] if r["id"] == AVAILABILITY_RESPONSE_ID)
    assert response["facts"]["http_status"] == 502
    assert response["facts"]["error"] is True
    assert response["facts"]["normalized_slot_count"] == 0

    reset_decision_trace_state()


def test_availability_client_normalizes_top_level_message_422_as_rejection():
    client = AvailabilityClient(base_url="http://availability.test")
    mock_response = httpx.Response(
        422,
        json={
            "success": False,
            "data": None,
            "message": "Business is closed on the selected date.",
        },
        request=httpx.Request(
            "GET",
            "http://availability.test/api/internal/availability/services",
        ),
    )

    with patch.object(client._client, "request", return_value=mock_response):
        with pytest.raises(AvailabilityRejectedError):
            client.get_service_availability(
                organization_id=1,
                service_id=18,
                date="2026-08-22",
            )


def test_availability_client_preserves_structured_business_closed_reason():
    client = AvailabilityClient(base_url="http://availability.test")
    mock_response = httpx.Response(
        422,
        json={"detail": {"code": "BUSINESS_CLOSED"}},
        request=httpx.Request(
            "GET",
            "http://availability.test/api/internal/availability/services",
        ),
    )

    with patch.object(client._client, "request", return_value=mock_response):
        with pytest.raises(AvailabilityRejectedError) as captured:
            client.get_service_availability(
                organization_id=1,
                service_id=18,
                date="2026-08-30",
            )

    assert captured.value.reason == "business_closed"


def test_availability_client_keeps_unstructured_422_as_upstream_error():
    client = AvailabilityClient(base_url="http://availability.test")
    mock_response = httpx.Response(
        422,
        content=b"",
        request=httpx.Request(
            "GET",
            "http://availability.test/api/internal/availability/services",
        ),
    )

    with patch.object(client._client, "request", return_value=mock_response):
        with pytest.raises(UpstreamError):
            client.get_service_availability(
                organization_id=1,
                service_id=18,
                date="2026-08-22",
            )


def test_normalize_only_does_not_emit_response_without_client():
    TurnTrace.begin(user_id="u1", text="x")
    raw = {"data": {"available_slots": []}}
    _normalize_availability_response(raw)
    trace = trace_to_dict(finalize_turn_trace() or {})
    record_ids = {record["id"] for record in trace["records"]}
    assert AVAILABILITY_REQUEST_ID not in record_ids
    assert AVAILABILITY_RESPONSE_ID not in record_ids
    reset_decision_trace_state()
