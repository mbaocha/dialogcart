"""Server-side decision trace logging and compact turn log helpers."""

from __future__ import annotations

import logging
from typing import Any, Dict, Mapping, Optional


def compact_session_snapshot(
    session: Optional[Mapping[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Return a small session view without catalog/org payload."""
    if not isinstance(session, dict):
        return None

    slots = session.get("slots")
    slot_values: Dict[str, Any] = {}
    if isinstance(slots, dict):
        slot_values = {key: value for key, value in slots.items() if value is not None}

    facts = session.get("facts")
    compact_facts: Dict[str, Any] = {}
    if isinstance(facts, dict):
        for key in (
            "booking_id",
            "dates",
            "times",
            "service_id",
            "date_time_pairs",
        ):
            if key in facts:
                compact_facts[key] = facts[key]

    intent = session.get("intent_name") or session.get("intent")
    if isinstance(intent, dict):
        intent = intent.get("name")

    return {
        "intent_name": intent,
        "status": session.get("status"),
        "missing_slots": session.get("missing_slots"),
        "slots": slot_values,
        **({"facts": compact_facts} if compact_facts else {}),
    }


def log_decision_trace_text(
    logger: logging.Logger,
    fields: Mapping[str, Any],
) -> None:
    """Write the human-readable decision trace to server logs."""
    text = fields.get("decision_trace_text")
    if not text:
        return
    view = fields.get("trace_view") or "summary"
    logger.info("[decision_trace:%s]\n%s", view, text)
