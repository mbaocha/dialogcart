"""Diagnostic tracing for SEARCH_AVAILABILITY HTTP calls to the availability service."""

from __future__ import annotations

import json
import logging
from contextvars import ContextVar
from typing import Any, Dict, Mapping, Optional

from core.tracing.decision_trace import emit_evidence

logger = logging.getLogger(__name__)

AVAILABILITY_REQUEST_ID = "availability.request"
AVAILABILITY_RESPONSE_ID = "availability.response"

_pending_availability_trace: ContextVar[Optional[Dict[str, Any]]] = ContextVar(
    "dialogcart_availability_trace_pending", default=None
)


def count_raw_availability_slots(response: Any) -> int:
    """Count slot rows in a raw availability API response body."""
    if not isinstance(response, dict):
        return 0
    payload = response
    if isinstance(response.get("data"), dict):
        payload = response["data"]
    raw_slots = payload.get("slots") or payload.get("available_slots") or []
    if not isinstance(raw_slots, list):
        return 0
    return len(raw_slots)


def _log_json(label: str, payload: Mapping[str, Any]) -> None:
    try:
        text = json.dumps(payload, ensure_ascii=True, default=str)
    except (TypeError, ValueError):
        text = str(payload)
    logger.info("%s %s", label, text)


def begin_availability_request(
    *,
    endpoint: str,
    method: str,
    organization_id: int,
    params: Mapping[str, Any],
    service_id: Optional[Any] = None,
    date: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    field_provenance: Optional[Mapping[str, Any]] = None,
) -> None:
    """Emit ``availability.request`` and log outbound availability HTTP parameters."""
    facts: Dict[str, Any] = {
        "endpoint": endpoint,
        "method": method,
        "organization_id": organization_id,
        "time_constraint": None,
    }
    if service_id is not None:
        facts["service_id"] = service_id
    if date is not None:
        facts["date"] = date
    if start_date is not None:
        facts["start_date"] = start_date
    if end_date is not None:
        facts["end_date"] = end_date

    provenance: Dict[str, Any] = dict(field_provenance) if field_provenance else {}
    if service_id is not None and "service_id" not in provenance:
        provenance["service_id"] = {
            "value": service_id,
            "source": "execution.search_slots.service_id",
            "consumer": "AvailabilityClient",
        }
    if date is not None and "date" not in provenance:
        provenance["date"] = {
            "value": date,
            "source": "date_proposal or slots.date",
            "consumer": "AvailabilityClient",
            "reason": "temporal constraint for availability search",
        }
    if "time" not in provenance:
        provenance["time"] = {
            "omitted": True,
            "reason": "not sent to availability endpoint; applied after search",
        }
    if provenance:
        facts["field_provenance"] = provenance

    # Forensic-only detail (omitted from reasoning projection).
    facts["_forensic"] = {
        "query_params": dict(params),
    }

    request_id = emit_evidence(
        "AVAILABILITY_REQUEST",
        subsystem="execution",
        facts=facts,
        node_id=AVAILABILITY_REQUEST_ID,
        source="AvailabilityClient",
        observed_at_stage="execution",
    )

    _pending_availability_trace.set(
        {
            "request_id": request_id,
            "endpoint": endpoint,
            "method": method,
            "organization_id": organization_id,
            "params": dict(params),
        }
    )
    _log_json("[availability.request]", facts)


def record_availability_http(
    *,
    http_status: int,
    raw_body: str,
) -> None:
    """Stash HTTP metadata between the client response and dispatcher normalization."""
    pending = dict(_pending_availability_trace.get() or {})
    pending["http_status"] = http_status
    pending["raw_body"] = raw_body
    _pending_availability_trace.set(pending)


def finalize_availability_response(
    *,
    raw_response: Mapping[str, Any],
    normalized: Mapping[str, Any],
) -> None:
    """Emit ``availability.response`` after Core normalizes the availability payload."""
    pending = _pending_availability_trace.get()
    if not pending:
        return
    normalized_slots = normalized.get("slots") or []
    normalized_count = (
        len(normalized_slots) if isinstance(normalized_slots, list) else 0
    )
    facts: Dict[str, Any] = {
        "endpoint": pending.get("endpoint"),
        "method": pending.get("method"),
        "organization_id": pending.get("organization_id"),
        "http_status": pending.get("http_status"),
        "available_slot_count": count_raw_availability_slots(raw_response),
        "normalized_slot_count": normalized_count,
        "normalized_status": normalized.get("status"),
        "normalized_type": normalized.get("type"),
        "field_provenance": {
            "slots": {
                "value": f"{normalized_count} offers",
                "source": "availability API",
                "consumer": "last_execution_result, presented_availability",
                "reason": f"http {pending.get('http_status')}",
            }
        },
        "_forensic": {
            "query_params": pending.get("params"),
            "raw_response_body": pending.get("raw_body"),
            "raw_response": dict(raw_response) if isinstance(raw_response, dict) else raw_response,
        },
    }

    emit_evidence(
        "AVAILABILITY_RESPONSE",
        subsystem="execution",
        facts=facts,
        node_id=AVAILABILITY_RESPONSE_ID,
        source="dispatcher._normalize_availability_response",
        observed_at_stage="execution",
        parent_id=pending.get("request_id"),
    )
    _log_json("[availability.response]", facts)
    _pending_availability_trace.set(None)


def finalize_availability_http_error(
    *,
    http_status: int,
    raw_body: str,
) -> None:
    """Emit ``availability.response`` when the availability HTTP call fails."""
    pending = _pending_availability_trace.get() or {}
    facts: Dict[str, Any] = {
        "endpoint": pending.get("endpoint"),
        "method": pending.get("method"),
        "organization_id": pending.get("organization_id"),
        "http_status": http_status,
        "available_slot_count": 0,
        "normalized_slot_count": 0,
        "error": True,
        "field_provenance": {
            "slots": {
                "omitted": True,
                "reason": f"availability HTTP error {http_status}",
            }
        },
        "_forensic": {
            "query_params": pending.get("params"),
            "raw_response_body": raw_body,
        },
    }
    emit_evidence(
        "AVAILABILITY_RESPONSE",
        subsystem="execution",
        facts=facts,
        node_id=AVAILABILITY_RESPONSE_ID,
        source="AvailabilityClient",
        observed_at_stage="execution",
        parent_id=pending.get("request_id"),
    )
    _log_json("[availability.response]", facts)
    _pending_availability_trace.set(None)


def clear_availability_trace() -> None:
    """Reset pending trace state (e.g. after errors)."""
    _pending_availability_trace.set(None)
