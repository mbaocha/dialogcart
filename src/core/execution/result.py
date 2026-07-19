"""Canonical execution result shared by execution, workflows, and rendering."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, TypedDict, Union


ExecutionStatus = Literal["succeeded", "failed", "partial"]
ExecutionSubjectKind = Literal[
    "booking", "cancellation", "availability", "none"
]
Identifier = Union[int, str]
Amount = Union[int, float]


class ExecutionRefs(TypedDict):
    organization_id: int
    customer_id: Optional[int]
    booking_id: Optional[Identifier]
    booking_code: Optional[str]


class ExecutionSubject(TypedDict):
    kind: ExecutionSubjectKind
    booking: Optional[Dict[str, Any]]
    cancellation: Optional[Dict[str, Any]]
    service_name: Optional[str]
    starts_at: Optional[str]
    ends_at: Optional[str]
    total_amount: Optional[Amount]
    currency: Optional[str]


class ExecutionAvailability(TypedDict):
    slots: List[Any]
    time_resolution: Optional[Dict[str, Any]]


class ExecutionError(TypedDict):
    code: Optional[str]
    message: Optional[str]


class ExecutionResult(TypedDict):
    """The only action-execution artifact exposed beyond ``dispatcher``."""

    schema_version: Literal[1]
    action: str
    status: ExecutionStatus
    intent_name: Optional[str]
    refs: ExecutionRefs
    subject: ExecutionSubject
    availability: Optional[ExecutionAvailability]
    error: Optional[ExecutionError]


_FAILED_STATUSES = {"FAILED", "FAILURE", "ERROR", "failed", "failure", "error"}
_PARTIAL_STATUSES = {"PARTIAL", "partial"}


def _first(source: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = source.get(key)
        if value is not None:
            return value
    return None


def _as_dict(value: Any) -> Optional[Dict[str, Any]]:
    return value if isinstance(value, dict) else None


def _as_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_text(value: Any) -> Optional[str]:
    return str(value) if value is not None else None


def _as_amount(value: Any) -> Optional[Amount]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_identifier(value: Any) -> Optional[Identifier]:
    if value is None:
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return str(value)


def _status_from_raw(raw: Dict[str, Any]) -> ExecutionStatus:
    status = raw.get("status")
    if isinstance(status, str) and status in _FAILED_STATUSES:
        return "failed"
    if raw.get("success") is False:
        return "failed"
    if isinstance(status, str) and status in _PARTIAL_STATUSES:
        return "partial"
    return "succeeded"


def _subject_kind(
    action: str,
    raw: Dict[str, Any],
    booking: Optional[Dict[str, Any]],
    cancellation: Optional[Dict[str, Any]],
) -> ExecutionSubjectKind:
    if (
        raw.get("type") == "availability"
        or isinstance(raw.get("slots"), list)
        or "AVAILABILITY" in action
    ):
        return "availability"
    if cancellation is not None or "CANCEL" in action:
        return "cancellation"
    if booking is not None:
        return "booking"
    return "none"


def normalize_execution_result(
    plan: Dict[str, Any],
    raw: Dict[str, Any],
) -> ExecutionResult:
    """Translate an action handler response into the canonical result contract."""
    action = str(plan.get("action") or "")
    intent_name = plan.get("intent_name") or plan.get("intent")
    slots = plan.get("slots") if isinstance(plan.get("slots"), dict) else {}
    booking = _as_dict(raw.get("booking"))
    cancellation = _as_dict(raw.get("cancellation"))

    booking_id = _first(raw, "booking_id", "bookingId")
    if booking_id is None and booking:
        booking_id = _first(booking, "id", "booking_id", "bookingId")
    if booking_id is None:
        booking_id = slots.get("booking_id")

    booking_code = _first(raw, "booking_code", "bookingCode")
    if booking_code is None and booking:
        booking_code = _first(booking, "booking_code", "bookingCode", "code")
    if booking_code is None and cancellation:
        booking_code = _first(
            cancellation, "booking_code", "bookingCode", "code"
        )
    if booking_code is None:
        booking_code = slots.get("booking_code")

    service_name = _first(raw, "service_name", "serviceName")
    if service_name is None and booking:
        service_name = _first(booking, "service_name", "serviceName")
        service = booking.get("service")
        if service_name is None and isinstance(service, dict):
            service_name = _first(service, "name", "display_name")
    if service_name is None:
        service_name = _first(slots, "service_name", "service_id")

    datetime_range = (
        slots.get("datetime_range")
        if isinstance(slots.get("datetime_range"), dict)
        else {}
    )
    starts_at = _first(
        raw, "starts_at", "startsAt", "start_time", "startTime"
    )
    ends_at = _first(raw, "ends_at", "endsAt", "end_time", "endTime")
    if booking:
        starts_at = starts_at or _first(
            booking,
            "starts_at",
            "startsAt",
            "start_time",
            "startTime",
            "start",
            "check_in",
        )
        ends_at = ends_at or _first(
            booking,
            "ends_at",
            "endsAt",
            "end_time",
            "endTime",
            "end",
            "check_out",
        )
    starts_at = starts_at or _first(
        slots, "starts_at", "start_time", "check_in"
    )
    ends_at = ends_at or _first(slots, "ends_at", "end_time", "check_out")
    starts_at = starts_at or _first(datetime_range, "start")
    ends_at = ends_at or _first(datetime_range, "end")

    total_amount = _first(raw, "total_amount", "totalAmount", "amount")
    currency = _first(raw, "currency")
    if booking:
        total_amount = total_amount or _first(
            booking, "total_amount", "totalAmount", "amount"
        )
        currency = currency or _first(booking, "currency")

    kind = _subject_kind(action, raw, booking, cancellation)
    availability: Optional[ExecutionAvailability] = None
    if kind == "availability":
        raw_slots = raw.get("slots")
        availability = {
            "slots": list(raw_slots) if isinstance(raw_slots, list) else [],
            "time_resolution": _as_dict(raw.get("time_resolution")),
        }

    error: Optional[ExecutionError] = None
    status = _status_from_raw(raw)
    if status == "failed":
        error = {
            "code": _as_text(_first(raw, "error_code", "error")),
            "message": _as_text(_first(raw, "message", "error_message")),
        }

    organization_id = _as_int(slots.get("organization_id"))
    if organization_id is None:
        raise ValueError("organization_id is required for an execution result")

    return {
        "schema_version": 1,
        "action": action,
        "status": status,
        "intent_name": _as_text(intent_name),
        "refs": {
            "organization_id": organization_id,
            "customer_id": _as_int(slots.get("customer_id")),
            "booking_id": _as_identifier(booking_id),
            "booking_code": _as_text(booking_code),
        },
        "subject": {
            "kind": kind,
            "booking": booking,
            "cancellation": cancellation,
            "service_name": _as_text(service_name),
            "starts_at": _as_text(starts_at),
            "ends_at": _as_text(ends_at),
            "total_amount": _as_amount(total_amount),
            "currency": _as_text(currency),
        },
        "availability": availability,
        "error": error,
    }
