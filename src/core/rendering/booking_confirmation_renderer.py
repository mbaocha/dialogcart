"""Render booking confirmation prompts for AWAITING_CONFIRMATION turns."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional


def _format_service_label(service_id: Any) -> str:
    if not service_id:
        return "appointment"
    text = str(service_id).strip()
    if not text:
        return "appointment"
    # Title-case simple SKU labels (e.g. "premium haircut" → "Premium Haircut").
    return text.title()


def _format_date_label(date_value: Any) -> str:
    if not date_value:
        return "the selected date"
    raw = str(date_value).split("T")[0].split(" ")[0]
    try:
        parsed = datetime.strptime(raw, "%Y-%m-%d")
        return f"{parsed.strftime('%B')} {parsed.day}"
    except ValueError:
        return raw


def _format_time_label(time_value: Any) -> str:
    if not time_value:
        return "the selected time"
    raw = str(time_value).strip()
    lower = raw.lower()
    if "am" in lower or "pm" in lower:
        return raw
    if ":" in raw:
        parts = raw.split(":")
        try:
            hour = int(parts[0])
            minute = int(parts[1]) if len(parts) > 1 else 0
            dt = datetime(2000, 1, 1, hour, minute)
            text = dt.strftime("%I:%M %p")
            return text.lstrip("0") if text.startswith("0") else text
        except (ValueError, IndexError):
            return raw
    return raw


def render_booking_confirmation_prompt(
    slots: Optional[Dict[str, Any]] = None,
) -> str:
    """Build a short confirmation prompt from durable booking slots."""
    slots = slots or {}
    service = _format_service_label(slots.get("service_id"))
    date_label = _format_date_label(slots.get("date"))
    time_label = _format_time_label(slots.get("time"))
    return (
        f"You're about to book a {service} on {date_label} at {time_label}. "
        "Would you like me to go ahead?"
    )


def render_booking_confirmation_rejected() -> str:
    """Open-ended prompt after the user declines booking confirmation."""
    return "No problem, I won't book it. What would you like to change?"


def render_revision_acknowledgement(
    summary: Optional[Dict[str, Any]],
) -> str:
    """One concise acknowledgement for booking field changes, or empty string."""
    if not isinstance(summary, dict):
        return ""
    changes = summary.get("changes")
    if not isinstance(changes, list) or not changes:
        return ""

    by_field: Dict[str, Dict[str, Any]] = {}
    for change in changes:
        if not isinstance(change, dict):
            continue
        field = change.get("field")
        if field:
            by_field[str(field)] = change

    if not by_field:
        return ""

    # Prefer a single natural sentence; multi-field stays one short line.
    if len(by_field) > 1:
        return "Okay — I've updated your booking."

    field, change = next(iter(by_field.items()))
    to_value = change.get("to")
    if field == "time":
        return f"Sure — I've changed it to {_format_time_label(to_value)}."
    if field == "service":
        return f"Okay — I've switched it to a {_format_service_label(to_value)}."
    if field == "date":
        return f"Sure — let's check {_format_date_label(to_value)} instead."
    return "Okay — I've updated your booking."


def prefix_with_revision_acknowledgement(
    body: Optional[str],
    summary: Optional[Dict[str, Any]],
) -> str:
    """Prefix acknowledgement onto existing response body when present."""
    body_text = (body or "").strip()
    ack = render_revision_acknowledgement(summary)
    if not ack:
        return body_text
    if not body_text:
        return ack
    return f"{ack}\n\n{body_text}"
