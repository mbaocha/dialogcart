"""Build LLM render requests for SEARCH_AVAILABILITY execution results."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from core.rendering.llm_renderer import LlmRenderRequest

try:
    from core.orchestration.time_resolution import (
        TIME_MATCH_EXACT,
        TIME_MATCH_MISMATCH,
        TIME_MATCH_NOT_APPLICABLE,
    )
except ImportError:
    TIME_MATCH_EXACT = "TIME_MATCH_EXACT"
    TIME_MATCH_MISMATCH = "TIME_MATCH_MISMATCH"
    TIME_MATCH_NOT_APPLICABLE = "TIME_MATCH_NOT_APPLICABLE"

_DEFAULT_MAX_TIMES = 6


def _slot_start_iso(slot: Dict[str, Any]) -> Optional[str]:
    if not isinstance(slot, dict):
        return None
    start = slot.get("starts_at") or slot.get("start") or slot.get("start_time")
    return str(start) if start else None


def _normalize_search_date(date_raw: Any) -> Optional[str]:
    """Normalize a date value to YYYY-MM-DD."""
    if not date_raw or not isinstance(date_raw, str):
        return None
    return date_raw.split("T")[0].split(" ")[0]


def _slot_date_iso(slot: Dict[str, Any]) -> Optional[str]:
    """Return YYYY-MM-DD from a slot start timestamp, if present."""
    start = _slot_start_iso(slot)
    if start and len(start) >= 10:
        return start[:10]
    return None


def _filter_slots_to_search_date(raw_slots: Any, search_date: str) -> List[Dict[str, Any]]:
    """Keep only slots whose start date matches search_date."""
    normalized = _normalize_search_date(search_date)
    if not normalized or not isinstance(raw_slots, list):
        return []
    filtered: List[Dict[str, Any]] = []
    for slot in raw_slots:
        if not isinstance(slot, dict):
            continue
        if _slot_date_iso(slot) == normalized:
            filtered.append(slot)
    return filtered


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


def dedupe_availability_slots(
    raw_slots: Any,
) -> tuple[List[Dict[str, Any]], List[str], Optional[str]]:
    """Return deduped slot dicts, ISO starts, and optional YYYY-MM-DD date label."""
    if not isinstance(raw_slots, list):
        return [], [], None

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

    date_label = (
        unique_starts[0][:10]
        if unique_starts and len(unique_starts[0]) >= 10
        else None
    )
    return unique_slots, unique_starts, date_label


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
    unique_slots, unique_starts, date_label = dedupe_availability_slots(raw_slots)
    if not unique_slots:
        return {
            "date": None,
            "times": [],
            "presented_slots": [],
            "more_count": 0,
            "total_unique": 0,
        }

    if date_label:
        day_slots: List[Dict[str, Any]] = []
        day_starts: List[str] = []
        for slot, start in zip(unique_slots, unique_starts):
            if start[:10] == date_label:
                day_slots.append(slot)
                day_starts.append(start)
        unique_slots = day_slots
        unique_starts = day_starts

    if not unique_slots:
        return {
            "date": None,
            "times": [],
            "presented_slots": [],
            "more_count": 0,
            "total_unique": 0,
        }

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
    normalized_search_date = _normalize_search_date(search_date)
    slots_for_summary = raw_slots
    if normalized_search_date:
        filtered = _filter_slots_to_search_date(raw_slots, normalized_search_date)
        if filtered:
            slots_for_summary = filtered

    summary = summarize_availability_slots(slots_for_summary, max_times=max_times)
    date_label = summary["date"]
    if not date_label and normalized_search_date:
        date_label = normalized_search_date
    return {
        "search_date": date_label,
        "slots": list(summary["presented_slots"]),
        "times": list(summary["times"]),
        "more_count": summary["more_count"],
        "total_unique": summary["total_unique"],
    }


def build_presented_availability_page(
    raw_slots: Any,
    *,
    page_index: int,
    page_size: int = _DEFAULT_MAX_TIMES,
    search_date: Optional[str] = None,
) -> Dict[str, Any]:
    """Build presented_availability for a page slice over cached search slots."""
    normalized_search_date = _normalize_search_date(search_date)
    slots_for_page = raw_slots
    if normalized_search_date:
        filtered = _filter_slots_to_search_date(raw_slots, normalized_search_date)
        if filtered:
            slots_for_page = filtered

    unique_slots, unique_starts, date_label = dedupe_availability_slots(slots_for_page)
    if normalized_search_date and slots_for_page is not raw_slots:
        date_label = normalized_search_date
    elif not date_label and normalized_search_date:
        date_label = normalized_search_date

    if date_label:
        day_slots: List[Dict[str, Any]] = []
        day_starts: List[str] = []
        for slot, start in zip(unique_slots, unique_starts):
            if start[:10] == date_label:
                day_slots.append(slot)
                day_starts.append(start)
        unique_slots = day_slots
        unique_starts = day_starts

    total = len(unique_slots)
    start = page_index * page_size
    page_slots = unique_slots[start : start + page_size]
    display_starts = unique_starts[start : start + page_size]
    times = [_format_display_time(s) for s in display_starts]
    more_count = max(0, total - (start + len(page_slots)))
    return {
        "search_date": date_label,
        "slots": list(page_slots),
        "times": list(times),
        "more_count": more_count,
        "total_unique": total,
    }


def build_availability_presentation(
    raw_slots: Any,
    *,
    page_size: int = _DEFAULT_MAX_TIMES,
    page_index: int = 0,
) -> Dict[str, Any]:
    """Build availability pagination state for a page over cached search slots."""
    unique_slots, _, _ = dedupe_availability_slots(raw_slots)
    total = len(unique_slots)
    return {
        "page_index": page_index,
        "page_size": page_size,
        "has_next": total > (page_index + 1) * page_size,
        "has_previous": page_index > 0,
    }


def compute_target_page_index(
    current_index: int,
    direction: str,
    total_unique: int,
    page_size: int,
) -> tuple[int, bool]:
    """Return ``(target_page_index, exhausted)`` for a browse direction.

    When ``exhausted`` is True, the requested page does not exist and the caller
    must not repeat the current page.
    """
    if direction == "next":
        next_index = current_index + 1
        if next_index * page_size >= total_unique:
            return current_index, True
        return next_index, False
    if direction == "previous":
        if current_index <= 0:
            return current_index, True
        return current_index - 1, False
    return current_index, True


def _service_name_from_decision(decision: Optional[Dict[str, Any]]) -> str:
    if not isinstance(decision, dict):
        return "your appointment"
    facts = decision.get("facts") if isinstance(decision.get("facts"), dict) else {}
    slots = facts.get("slots") if isinstance(facts.get("slots"), dict) else {}
    service_id = slots.get("service_id") or facts.get("service_id")
    if service_id:
        return str(service_id).title() if str(service_id).islower() else str(service_id)
    return "your appointment"


def _format_alternative_labels(alternatives: List[str]) -> List[str]:
    return [_format_display_time(iso) for iso in alternatives if iso]


def build_availability_render_request(
    decision: Optional[Dict[str, Any]],
    execution_result: Dict[str, Any],
    *,
    structured_context: Optional[Dict[str, Any]] = None,
    conversation_history: Optional[List[Dict[str, str]]] = None,
    max_times: int = _DEFAULT_MAX_TIMES,
    time_resolution: Optional[Dict[str, Any]] = None,
) -> Optional[LlmRenderRequest]:
    """Build render request for a successful availability search, or None if no slots."""
    resolution = time_resolution or execution_result.get("time_resolution")
    if not isinstance(resolution, dict):
        resolution = {"outcome": TIME_MATCH_NOT_APPLICABLE}

    summary = summarize_availability_slots(
        execution_result.get("slots") or [], max_times=max_times
    )
    service_name = _service_name_from_decision(decision)

    outcome = resolution.get("outcome") or resolution.get("status")
    if outcome in ("exact_match",):
        outcome = TIME_MATCH_EXACT
    elif outcome in ("no_match",):
        outcome = TIME_MATCH_MISMATCH

    if summary["total_unique"] == 0 and outcome != TIME_MATCH_MISMATCH:
        return None

    availability_facts: Dict[str, Any] = {
        "service_name": service_name,
        "date": summary["date"],
        "times": summary["times"],
        "more_count": summary["more_count"],
    }

    if outcome == TIME_MATCH_EXACT:
        requested = resolution.get("requested_time")
        matched = resolution.get("matched_offer")
        availability_facts["matched_time"] = (
            _format_display_time(str(matched)) if matched else requested
        )
        render_instruction = (
            f"The user is booking {service_name}. "
            f"Their requested time ({requested}) is available (matched offer: {matched}). "
            "Ask them to confirm they want to proceed with this appointment. "
            "Do not list other times or suggest alternatives. "
            "Do not decide whether the time matches — that has already been determined. "
            "Keep the reply to 2–3 sentences."
        )
    elif outcome == TIME_MATCH_MISMATCH:
        alternatives = resolution.get("alternatives") or []
        alt_labels = _format_alternative_labels(
            [str(a) for a in alternatives if a][:max_times]
        )
        availability_facts["times"] = alt_labels
        availability_facts["more_count"] = max(0, len(alternatives) - len(alt_labels))
        requested = resolution.get("requested_time")
        if alt_labels:
            render_instruction = (
                f"The user is booking {service_name}. "
                f"Their requested time ({requested}) is not available. "
                "Explain that clearly, then present the alternative times listed below "
                "in a short bullet list and ask which they would prefer. "
                "Do not claim the requested time is available. "
                "Do not pick a time for the user — matching has already been done. "
                "Keep the reply to 2–3 sentences plus the list."
            )
        else:
            render_instruction = (
                f"The user is booking {service_name}. "
                f"Their requested time ({requested}) is not available, and there are "
                "no alternative times on that date. "
                "Explain that clearly and suggest trying another date or time. "
                "Do not invent availability. Keep the reply to 2–3 sentences."
            )
    else:
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
        "time_resolution": resolution,
    }

    return LlmRenderRequest(
        render_instruction=render_instruction,
        facts=facts,
        conversation_history=conversation_history or [],
    )


def build_availability_no_more_render_request(
    decision: Optional[Dict[str, Any]],
    *,
    direction: str,
    structured_context: Optional[Dict[str, Any]] = None,
    conversation_history: Optional[List[Dict[str, str]]] = None,
) -> LlmRenderRequest:
    """Build render request when the user browses past the last cached page."""
    service_name = _service_name_from_decision(decision)
    if direction == "previous":
        instruction = (
            f"The user is booking {service_name}. "
            "They asked to see earlier times, but they are already viewing the "
            "earliest available times from the last search. "
            "Tell them clearly there are no earlier times to show. "
            "Do not repeat the time list. Keep it to 1–2 sentences."
        )
    else:
        instruction = (
            f"The user is booking {service_name}. "
            "They asked to see more times, but there are no additional available "
            "times beyond what was already shown from the last search. "
            "Tell them clearly there are no more available times to show. "
            "Do not repeat the time list. Keep it to 1–2 sentences."
        )
    return LlmRenderRequest(
        render_instruction=instruction,
        facts={"structured_context": structured_context or {}},
        conversation_history=conversation_history or [],
    )
