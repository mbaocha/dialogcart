"""Build LLM render requests for SEARCH_AVAILABILITY execution results."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from core.rendering.llm_renderer import LlmRenderRequest

_DEFAULT_MAX_TIMES = 6


def _slot_start_iso(slot: Dict[str, Any]) -> Optional[str]:
    if not isinstance(slot, dict):
        return None
    start = slot.get("starts_at") or slot.get("start") or slot.get("start_time")
    return str(start) if start else None


def _format_display_time(iso_start: str) -> str:
    """Format ISO datetime to a short time label."""
    raw = iso_start.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(raw)
        text = dt.strftime("%I:%M %p")
        return text.lstrip("0") if text.startswith("0") else text
    except ValueError:
        if "T" in iso_start:
            return iso_start.split("T", 1)[1][:5]
        return iso_start


def summarize_availability_slots(
    raw_slots: Any,
    *,
    max_times: int = _DEFAULT_MAX_TIMES,
) -> Dict[str, Any]:
    """
    Dedupe availability slots by start time and cap for LLM context.

    Returns:
        {
            "date": "YYYY-MM-DD" | None,
            "times": ["9:00 AM", ...],
            "presented_slots": [slot dicts shown to the user],
            "more_count": int,
            "total_unique": int,
        }
    """
    if not isinstance(raw_slots, list):
        return {
            "date": None,
            "times": [],
            "presented_slots": [],
            "more_count": 0,
            "total_unique": 0,
        }

    unique_slots: List[Dict[str, Any]] = []
    unique_starts: List[str] = []
    seen: set[str] = set()
    for slot in raw_slots:
        if not isinstance(slot, dict):
            continue
        start = _slot_start_iso(slot)
        if not start or start in seen:
            continue
        seen.add(start)
        unique_starts.append(start)
        unique_slots.append(slot)

    date_label = unique_starts[0][:10] if unique_starts and len(unique_starts[0]) >= 10 else None
    presented_slots = unique_slots[:max_times]
    display_starts = unique_starts[:max_times]
    times = [_format_display_time(s) for s in display_starts]
    total = len(unique_starts)
    more_count = max(0, total - len(display_starts))

    return {
        "date": date_label,
        "times": times,
        "presented_slots": presented_slots,
        "more_count": more_count,
        "total_unique": total,
    }


def build_presented_availability(
    raw_slots: Any,
    *,
    max_times: int = _DEFAULT_MAX_TIMES,
    search_date: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the selectable availability payload shown to the user."""
    summary = summarize_availability_slots(raw_slots, max_times=max_times)
    date_label = summary["date"]
    if not date_label and isinstance(search_date, str) and search_date:
        date_label = search_date.split("T")[0].split(" ")[0]
    return {
        "search_date": date_label,
        "slots": list(summary["presented_slots"]),
        "times": list(summary["times"]),
        "more_count": summary["more_count"],
        "total_unique": summary["total_unique"],
    }


def _service_name_from_decision(decision: Optional[Dict[str, Any]]) -> str:
    if not isinstance(decision, dict):
        return "your appointment"
    facts = decision.get("facts") if isinstance(decision.get("facts"), dict) else {}
    slots = facts.get("slots") if isinstance(facts.get("slots"), dict) else {}
    service_id = slots.get("service_id") or facts.get("service_id")
    if service_id:
        return str(service_id).title() if str(service_id).islower() else str(service_id)
    return "your appointment"


def build_availability_render_request(
    decision: Optional[Dict[str, Any]],
    execution_result: Dict[str, Any],
    *,
    structured_context: Optional[Dict[str, Any]] = None,
    conversation_history: Optional[List[Dict[str, str]]] = None,
    max_times: int = _DEFAULT_MAX_TIMES,
) -> Optional[LlmRenderRequest]:
    """Build render request for a successful availability search, or None if no slots."""
    summary = summarize_availability_slots(
        execution_result.get("slots") or [], max_times=max_times
    )
    if summary["total_unique"] == 0:
        return None

    service_name = _service_name_from_decision(decision)
    availability_facts = {
        "service_name": service_name,
        "date": summary["date"],
        "times": summary["times"],
        "more_count": summary["more_count"],
    }

    render_instruction = (
        f"The user is booking {service_name}. "
        "Present the available appointment times listed below in a short bullet list. "
        "Ask which time they would like. "
        "Keep the reply to 2–3 sentences plus the list. "
        "Do not invent times or mention staff names."
    )

    facts: Dict[str, Any] = {
        "structured_context": structured_context or {},
        "availability": availability_facts,
    }

    return LlmRenderRequest(
        render_instruction=render_instruction,
        facts=facts,
        conversation_history=conversation_history or [],
    )
