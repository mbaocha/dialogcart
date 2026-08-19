"""Availability presentation: window construction, paging, and browse metadata.

Owns grouping, filtering, dedupe, page limits, and public PresentedAvailability
projection. Internal cursor/index math stays in this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from typing import Any, Dict, List, Mapping, Optional, Tuple

from core.workflows.availability.contracts import (
    AvailabilityCache,
    BrowseHints,
    BrowseIntent,
    BrowseProjection,
    PresentedAvailability,
)

DEFAULT_MAX_TIMES = 6

# Backward-compatible alias used by pagination and tests.
_DEFAULT_MAX_TIMES = DEFAULT_MAX_TIMES

# Private persistence key nested inside presented_availability (not a domain contract).
_CURSOR_KEY = "_cursor"


# ---------------------------------------------------------------------------
# Slot helpers
# ---------------------------------------------------------------------------


def _slot_start_iso(slot: Dict[str, Any]) -> Optional[str]:
    if not isinstance(slot, dict):
        return None
    start = slot.get("starts_at") or slot.get("start") or slot.get("start_time")
    return str(start) if start else None


def normalize_search_date(date_raw: Any) -> Optional[str]:
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


def filter_slots_to_search_date(raw_slots: Any, search_date: str) -> List[Dict[str, Any]]:
    """Keep only slots whose start date matches search_date."""
    normalized = normalize_search_date(search_date)
    if not normalized or not isinstance(raw_slots, list):
        return []
    filtered: List[Dict[str, Any]] = []
    for slot in raw_slots:
        if not isinstance(slot, dict):
            continue
        if _slot_date_iso(slot) == normalized:
            filtered.append(slot)
    return filtered


def resolve_criteria_span(
    *,
    slots: Optional[Dict[str, Any]] = None,
    date_proposal: Optional[Dict[str, Any]] = None,
    fingerprint_slots: Optional[Dict[str, Any]] = None,
    search_date: Optional[str] = None,
) -> Tuple[str, Optional[str], Optional[str]]:
    """Derive presentation span from search criteria (not provider surplus).

    Returns ``(criteria_span, search_date, search_end_date)`` where
    ``criteria_span`` is ``\"single_day\"`` or ``\"multi_day\"``.
    """
    criteria = fingerprint_slots if isinstance(fingerprint_slots, dict) else {}
    plan_slots = slots if isinstance(slots, dict) else {}
    proposal = date_proposal if isinstance(date_proposal, dict) else {}

    def _norm(raw: Any) -> Optional[str]:
        return normalize_search_date(raw) if isinstance(raw, str) else None

    date_range = plan_slots.get("date_range") or criteria.get("date_range")
    if isinstance(date_range, dict):
        start = _norm(date_range.get("start") or date_range.get("start_date"))
        end = _norm(date_range.get("end") or date_range.get("end_date"))
        if start and end and end != start:
            return "multi_day", start, end
        if start:
            return "single_day", start, start

    start_date = _norm(
        plan_slots.get("start_date")
        or criteria.get("start_date")
        or proposal.get("start")
    )
    end_date = _norm(
        plan_slots.get("end_date")
        or criteria.get("end_date")
        or proposal.get("end")
    )
    if start_date and end_date and end_date != start_date:
        return "multi_day", start_date, end_date

    mode = str(proposal.get("mode") or "").strip().lower()
    if mode in ("range", "flexible"):
        start = start_date or _norm(search_date)
        end = end_date or start
        if start and end and end != start:
            return "multi_day", start, end
        # Explicit multi-day mode without a distinct end still spans dates.
        return "multi_day", start, end

    single = _norm(
        plan_slots.get("date")
        or criteria.get("date")
        or start_date
        or proposal.get("start")
        or search_date
    )
    if single or mode == "single_day":
        day = single or start_date
        if day:
            return "single_day", day, day

    # Undated exploratory search — criteria intentionally span provider dates.
    return "multi_day", _norm(search_date), None


def criteria_is_single_day(
    *,
    slots: Optional[Dict[str, Any]] = None,
    date_proposal: Optional[Dict[str, Any]] = None,
    fingerprint_slots: Optional[Dict[str, Any]] = None,
    search_date: Optional[str] = None,
) -> bool:
    """True when canonical search criteria constrain presentation to one day."""
    span, _, _ = resolve_criteria_span(
        slots=slots,
        date_proposal=date_proposal,
        fingerprint_slots=fingerprint_slots,
        search_date=search_date,
    )
    return span == "single_day"


def search_criteria_from_session(
    session_state: Optional[Dict[str, Any]],
) -> tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    """Read canonical planning search criteria from session for presentation shaping."""
    if not isinstance(session_state, dict):
        return {}, None
    slots: Dict[str, Any] = {}
    top_slots = session_state.get("slots")
    if isinstance(top_slots, dict):
        slots.update(top_slots)
    planning = session_state.get("planning")
    if isinstance(planning, dict):
        planning_slots = planning.get("slots")
        if isinstance(planning_slots, dict):
            slots = {**planning_slots, **slots}
        proposals = planning.get("proposals")
        if isinstance(proposals, dict) and isinstance(proposals.get("date"), dict):
            return slots, proposals.get("date")
    date_proposal = session_state.get("date_proposal")
    if isinstance(date_proposal, dict):
        return slots, date_proposal
    return slots, None


def presentation_slots_from_cache(
    cache: AvailabilityCache,
    *,
    slots: Optional[Dict[str, Any]] = None,
    date_proposal: Optional[Dict[str, Any]] = None,
    fingerprint_slots: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Shape the presentation result set from cache using search criteria.

    Keeps the full provider response in ``cache[\"slots\"]``. Single-day criteria
    expose only that day's offers to pagination; multi-day / exploratory criteria
    keep all qualifying dates. Span is derived from planning criteria — never
    from persisted cache presentation flags or provider surplus.
    """
    raw = cache.get("slots") or []
    if not isinstance(raw, list):
        return []
    span, start, _end = resolve_criteria_span(
        slots=slots,
        date_proposal=date_proposal,
        fingerprint_slots=fingerprint_slots,
        search_date=None,
    )
    if span != "single_day":
        return list(raw)
    day = start
    if not day:
        return list(raw)
    return filter_slots_to_search_date(raw, day)


def format_display_time(iso_start: str) -> str:
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


def compute_presented_options_reference(
    presented: Optional[PresentedAvailability],
    fingerprint: Optional[str],
) -> Optional[str]:
    """Identify the exact visible availability window without persisting an ID."""
    if not fingerprint or not isinstance(presented, dict):
        return None
    slots = presented.get("slots")
    labels = presented.get("times")
    if not isinstance(slots, list) or not slots or not isinstance(labels, list):
        return None
    if len(slots) != len(labels):
        return None
    starts = [_slot_start_iso(slot) for slot in slots if isinstance(slot, dict)]
    if len(starts) != len(slots) or any(not start for start in starts):
        return None
    identity = {
        "version": 1,
        "fingerprint": str(fingerprint),
        "visible_slot_starts": starts,
    }
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    return "avp1_" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]


def presented_options_for_nlu(
    presented: Optional[PresentedAvailability],
    fingerprint: Optional[str],
) -> Optional[Dict[str, Any]]:
    """Project only the visible semantic option labels into NLU context."""
    reference = compute_presented_options_reference(presented, fingerprint)
    if reference is None or not isinstance(presented, dict):
        return None
    labels = presented.get("times")
    if not isinstance(labels, list):
        return None
    return {
        "reference": reference,
        "options": [
            {"index": index, "label": str(label)}
            for index, label in enumerate(labels, start=1)
        ],
    }


def trusted_presented_option(
    presented: Optional[PresentedAvailability],
    fingerprint: Optional[str],
    *,
    presentation_ref: Any,
    option: Any,
) -> Optional[Dict[str, Any]]:
    """Validate an untrusted NLU reference and return its current visible slot."""
    expected = compute_presented_options_reference(presented, fingerprint)
    if expected is None or str(presentation_ref or "") != expected:
        return None
    if isinstance(option, bool):
        return None
    try:
        index = int(option)
    except (TypeError, ValueError):
        return None
    slots = presented.get("slots") if isinstance(presented, dict) else None
    if not isinstance(slots, list) or index < 1 or index > len(slots):
        return None
    slot = slots[index - 1]
    return dict(slot) if isinstance(slot, dict) and _slot_start_iso(slot) else None


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


# ---------------------------------------------------------------------------
# Internal AvailabilityView (private)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _AvailabilityGroup:
    date: str
    slots: Tuple[Dict[str, Any], ...]


@dataclass(frozen=True)
class _AvailabilityView:
    groups: Tuple[_AvailabilityGroup, ...]
    page_size: int


@dataclass(frozen=True)
class _Cursor:
    group_index: int
    page_index: int
    page_size: int


def _build_availability_view(
    raw_slots: Any,
    *,
    page_size: int = DEFAULT_MAX_TIMES,
) -> _AvailabilityView:
    """Group/dedupe/sort cache slots into a private chronological view."""
    unique_slots, unique_starts, _ = dedupe_availability_slots(raw_slots)
    by_date: Dict[str, List[Dict[str, Any]]] = {}
    for slot, start in zip(unique_slots, unique_starts):
        date = start[:10] if len(start) >= 10 else None
        if not date:
            continue
        by_date.setdefault(date, []).append(slot)

    groups: List[_AvailabilityGroup] = []
    for date in sorted(by_date.keys()):
        day_slots = by_date[date]
        day_slots.sort(key=lambda s: _slot_start_iso(s) or "")
        groups.append(_AvailabilityGroup(date=date, slots=tuple(day_slots)))
    return _AvailabilityView(groups=tuple(groups), page_size=max(1, int(page_size)))


def _pages_in_group(group: _AvailabilityGroup, page_size: int) -> int:
    if not group.slots:
        return 0
    return (len(group.slots) + page_size - 1) // page_size


def _clamp_cursor(view: _AvailabilityView, cursor: _Cursor) -> Optional[_Cursor]:
    if not view.groups:
        return None
    gi = max(0, min(cursor.group_index, len(view.groups) - 1))
    group = view.groups[gi]
    pages = _pages_in_group(group, cursor.page_size)
    if pages <= 0:
        return _Cursor(group_index=gi, page_index=0, page_size=cursor.page_size)
    pi = max(0, min(cursor.page_index, pages - 1))
    return _Cursor(group_index=gi, page_index=pi, page_size=cursor.page_size)


def _cursor_from_presented(
    view: _AvailabilityView,
    presented: Optional[PresentedAvailability],
) -> _Cursor:
    """Recover private cursor from presented payload or reconstruct from window."""
    page_size = view.page_size
    if isinstance(presented, dict):
        raw = presented.get(_CURSOR_KEY)
        if isinstance(raw, dict):
            try:
                cursor = _Cursor(
                    group_index=int(raw.get("group_index", 0)),
                    page_index=int(raw.get("page_index", 0)),
                    page_size=int(raw.get("page_size") or page_size),
                )
                clamped = _clamp_cursor(view, cursor)
                if clamped is not None:
                    return clamped
            except (TypeError, ValueError):
                pass

        search_date = normalize_search_date(presented.get("search_date"))
        if search_date:
            for gi, group in enumerate(view.groups):
                if group.date == search_date:
                    # Infer page from first visible slot when possible.
                    page_index = 0
                    visible = presented.get("slots") if isinstance(presented.get("slots"), list) else []
                    if visible and group.slots:
                        first_start = _slot_start_iso(visible[0]) if isinstance(visible[0], dict) else None
                        if first_start:
                            for si, slot in enumerate(group.slots):
                                if _slot_start_iso(slot) == first_start:
                                    page_index = si // page_size
                                    break
                    clamped = _clamp_cursor(
                        view,
                        _Cursor(group_index=gi, page_index=page_index, page_size=page_size),
                    )
                    if clamped is not None:
                        return clamped

    return _Cursor(group_index=0, page_index=0, page_size=page_size)


def _encode_cursor(cursor: _Cursor) -> Dict[str, int]:
    return {
        "group_index": cursor.group_index,
        "page_index": cursor.page_index,
        "page_size": cursor.page_size,
    }


def _window_for_cursor(
    view: _AvailabilityView,
    cursor: _Cursor,
) -> Tuple[List[Dict[str, Any]], List[str], Optional[str], int, int]:
    """Return page slots, display times, date, more_count, total_unique for date."""
    clamped = _clamp_cursor(view, cursor)
    if clamped is None or not view.groups:
        return [], [], None, 0, 0
    group = view.groups[clamped.group_index]
    total = len(group.slots)
    start = clamped.page_index * clamped.page_size
    page_slots = list(group.slots[start : start + clamped.page_size])
    starts = [_slot_start_iso(s) or "" for s in page_slots]
    times = [format_display_time(s) for s in starts if s]
    more_count = max(0, total - (start + len(page_slots)))
    return page_slots, times, group.date, more_count, total


def _browse_hints_for_cursor(view: _AvailabilityView, cursor: _Cursor) -> BrowseHints:
    clamped = _clamp_cursor(view, cursor)
    if clamped is None or not view.groups:
        return {
            "has_more_times": False,
            "has_previous_times": False,
            "has_more_any": False,
            "has_previous_any": False,
            "suggested_next": None,
            "suggested_previous": None,
            "more_count": 0,
            "total_unique": 0,
        }

    group = view.groups[clamped.group_index]
    pages = _pages_in_group(group, clamped.page_size)
    has_more_times = clamped.page_index + 1 < pages
    has_previous_times = clamped.page_index > 0
    # Multi-day criteria result sets may contain further date groups; that is
    # page movement inside the criteria-shaped set, not date-axis browse.
    has_later_group = clamped.group_index + 1 < len(view.groups)
    has_earlier_group = clamped.group_index > 0
    has_more_any = has_more_times or has_later_group
    has_previous_any = has_previous_times or has_earlier_group

    suggested_next: Optional[str] = "next" if has_more_any else None
    suggested_previous: Optional[str] = "previous" if has_previous_any else None

    more_count = max(0, len(group.slots) - (clamped.page_index + 1) * clamped.page_size)
    return {
        "has_more_times": has_more_times,
        "has_previous_times": has_previous_times,
        "has_more_any": has_more_any,
        "has_previous_any": has_previous_any,
        "suggested_next": suggested_next,  # type: ignore[typeddict-item]
        "suggested_previous": suggested_previous,  # type: ignore[typeddict-item]
        "more_count": more_count,
        "total_unique": len(group.slots),
    }


def _project_presented(
    view: _AvailabilityView,
    cursor: _Cursor,
    *,
    search_date: Optional[str] = None,
    fingerprint: Optional[str] = None,
    browse_status: Optional[str] = None,
) -> PresentedAvailability:
    clamped = _clamp_cursor(view, cursor) or _Cursor(0, 0, view.page_size)
    page_slots, times, date_label, more_count, total = _window_for_cursor(view, clamped)
    browse_hints = _browse_hints_for_cursor(view, clamped)
    from core.planning.recovery_actions import recovery_actions_for_browse_window

    presented: PresentedAvailability = {
        "search_date": date_label or normalize_search_date(search_date),
        "slots": page_slots,
        "times": times,
        "more_count": more_count,
        "total_unique": total,
        "browse_hints": browse_hints,
        "recovery_actions": recovery_actions_for_browse_window(browse_hints),  # type: ignore[misc]
        _CURSOR_KEY: _encode_cursor(clamped),  # type: ignore[misc]
    }
    if fingerprint:
        presented["fingerprint"] = fingerprint
    if browse_status:
        presented["browse_status"] = browse_status
    return presented


def _initial_cursor(
    view: _AvailabilityView,
    *,
    search_date: Optional[str] = None,
) -> _Cursor:
    page_size = view.page_size
    if not view.groups:
        return _Cursor(0, 0, page_size)
    normalized = normalize_search_date(search_date)
    if normalized:
        for gi, group in enumerate(view.groups):
            if group.date == normalized:
                return _Cursor(gi, 0, page_size)
    return _Cursor(0, 0, page_size)


def build_initial_presentation(
    cache: AvailabilityCache,
    *,
    page_size: int = DEFAULT_MAX_TIMES,
    search_date: Optional[str] = None,
    slots: Optional[Dict[str, Any]] = None,
    date_proposal: Optional[Dict[str, Any]] = None,
    fingerprint_slots: Optional[Dict[str, Any]] = None,
) -> PresentedAvailability:
    """Build the first visible window from a criteria-shaped result set."""
    shaped = presentation_slots_from_cache(
        cache,
        slots=slots,
        date_proposal=date_proposal,
        fingerprint_slots=fingerprint_slots,
    )
    view = _build_availability_view(shaped, page_size=page_size)
    preferred = search_date or cache.get("search_date")
    if not preferred and slots:
        span, start, _ = resolve_criteria_span(
            slots=slots,
            date_proposal=date_proposal,
            fingerprint_slots=fingerprint_slots,
        )
        if span == "single_day":
            preferred = start
    cursor = _initial_cursor(view, search_date=preferred)
    return _project_presented(
        view,
        cursor,
        search_date=preferred,
        fingerprint=cache.get("fingerprint"),
    )


def project_presentation_to_date(
    cache: AvailabilityCache,
    target_date: str,
    *,
    current_presentation: Optional[PresentedAvailability] = None,
    page_size: Optional[int] = None,
) -> BrowseProjection:
    """Legacy date jump — retained only for discovery bridge compatibility.

    Absolute date requests must flow through SEARCH_AVAILABILITY, not browse.
    Always preserves the current window and reports ``target_date_not_in_cache``.
    """
    _ = cache, target_date, page_size
    if isinstance(current_presentation, dict) and isinstance(
        current_presentation.get("slots"), list
    ):
        presented = dict(current_presentation)
        presented["browse_status"] = "target_date_not_in_cache"
        return {
            "presented": presented,
            "moved": False,
            "reason_code": "target_date_not_in_cache",
        }
    empty: PresentedAvailability = {
        "slots": [],
        "times": [],
        "more_count": 0,
        "total_unique": 0,
        "browse_status": "target_date_not_in_cache",
    }
    return {"presented": empty, "moved": False, "reason_code": "target_date_not_in_cache"}


def cache_contains_date(cache: AvailabilityCache, target_date: str) -> bool:
    """True when normalized target_date exists as a non-empty group in cache."""
    normalized = normalize_search_date(target_date)
    if not normalized:
        return False
    view = _build_availability_view(cache.get("slots") or [])
    return any(group.date == normalized for group in view.groups)


def advance_presentation(
    cache: AvailabilityCache,
    current_presentation: Optional[PresentedAvailability],
    browse_intent: BrowseIntent,
    *,
    page_size: Optional[int] = None,
    slots: Optional[Dict[str, Any]] = None,
    date_proposal: Optional[Dict[str, Any]] = None,
    fingerprint_slots: Optional[Dict[str, Any]] = None,
) -> BrowseProjection:
    """Advance the page cursor inside the criteria-shaped presentation result set.

    Never changes search criteria. Single-day searches cannot paginate into
    provider surplus dates outside the requested day.
    """
    shaped = presentation_slots_from_cache(
        cache,
        slots=slots,
        date_proposal=date_proposal,
        fingerprint_slots=fingerprint_slots,
    )
    resolved_page_size = page_size or DEFAULT_MAX_TIMES
    if isinstance(current_presentation, dict):
        raw_cursor = current_presentation.get(_CURSOR_KEY)
        if isinstance(raw_cursor, dict) and raw_cursor.get("page_size"):
            try:
                resolved_page_size = int(raw_cursor["page_size"])
            except (TypeError, ValueError):
                pass

    view = _build_availability_view(shaped, page_size=resolved_page_size)
    if not view.groups:
        empty = _project_presented(view, _Cursor(0, 0, resolved_page_size))
        empty["browse_status"] = "exhausted"
        return {
            "presented": empty,
            "moved": False,
            "reason_code": "exhausted",
        }

    cursor = _cursor_from_presented(view, current_presentation)
    direction = browse_intent.get("direction")
    axis = browse_intent.get("axis_hint") or "any"

    def _stay(reason: str) -> BrowseProjection:
        # Exhaustion must not clear the last successful visible window.
        if isinstance(current_presentation, dict) and isinstance(
            current_presentation.get("slots"), list
        ):
            presented = dict(current_presentation)
            presented["browse_status"] = reason
            # Refresh public hints from the preserved cursor when possible.
            hints = _browse_hints_for_cursor(view, cursor)
            presented["browse_hints"] = hints
            from core.planning.recovery_actions import recovery_actions_for_browse_window

            presented["recovery_actions"] = recovery_actions_for_browse_window(hints)
            return {"presented": presented, "moved": False, "reason_code": reason}
        presented = _project_presented(
            view,
            cursor,
            fingerprint=cache.get("fingerprint"),
            browse_status=reason,
        )
        return {"presented": presented, "moved": False, "reason_code": reason}

    def _move(new_cursor: _Cursor) -> BrowseProjection:
        presented = _project_presented(
            view, new_cursor, fingerprint=cache.get("fingerprint")
        )
        return {"presented": presented, "moved": True, "reason_code": "moved"}

    group = view.groups[cursor.group_index]
    pages = _pages_in_group(group, cursor.page_size)

    if direction == "next":
        if axis == "time":
            if cursor.page_index + 1 < pages:
                return _move(
                    _Cursor(cursor.group_index, cursor.page_index + 1, cursor.page_size)
                )
            return _stay("exhausted")
        # Page through the criteria-shaped set (times, then later groups when multi-day).
        if cursor.page_index + 1 < pages:
            return _move(
                _Cursor(cursor.group_index, cursor.page_index + 1, cursor.page_size)
            )
        if cursor.group_index + 1 < len(view.groups):
            return _move(_Cursor(cursor.group_index + 1, 0, cursor.page_size))
        return _stay("exhausted")

    if direction == "previous":
        if axis == "time":
            if cursor.page_index > 0:
                return _move(
                    _Cursor(cursor.group_index, cursor.page_index - 1, cursor.page_size)
                )
            return _stay("exhausted")
        if cursor.page_index > 0:
            return _move(
                _Cursor(cursor.group_index, cursor.page_index - 1, cursor.page_size)
            )
        if cursor.group_index > 0:
            prev = view.groups[cursor.group_index - 1]
            last_page = max(0, _pages_in_group(prev, cursor.page_size) - 1)
            return _move(_Cursor(cursor.group_index - 1, last_page, cursor.page_size))
        return _stay("exhausted")

    return _stay("exhausted")


# ---------------------------------------------------------------------------
# Legacy summarizers / page builders (compat for tests and initial search)
# ---------------------------------------------------------------------------


def summarize_availability_slots(
    raw_slots: Any,
    *,
    max_times: int = DEFAULT_MAX_TIMES,
) -> Dict[str, Any]:
    """Dedupe availability slots by start time and cap for presentation."""
    view = _build_availability_view(raw_slots, page_size=max_times)
    if not view.groups:
        return {
            "date": None,
            "times": [],
            "presented_slots": [],
            "more_count": 0,
            "total_unique": 0,
        }
    # Preserve prior single-day summary behaviour: first chronological date.
    group = view.groups[0]
    page_slots = list(group.slots[:max_times])
    starts = [_slot_start_iso(s) or "" for s in page_slots]
    times = [format_display_time(s) for s in starts if s]
    total = len(group.slots)
    return {
        "date": group.date,
        "times": times,
        "presented_slots": page_slots,
        "more_count": max(0, total - len(page_slots)),
        "total_unique": total,
    }


def build_presented_availability(
    raw_slots: Any,
    *,
    max_times: int = DEFAULT_MAX_TIMES,
    search_date: Optional[str] = None,
    fingerprint: Optional[str] = None,
) -> PresentedAvailability:
    """Build the selectable availability payload shown to the user.

    When ``search_date`` is provided, the presentation result set is shaped to
    that single day (provider surplus on other days is excluded from paging).
    """
    shaped = (
        filter_slots_to_search_date(raw_slots, search_date)
        if search_date
        else raw_slots
    )
    view = _build_availability_view(shaped, page_size=max_times)
    cursor = _initial_cursor(view, search_date=search_date)
    return _project_presented(
        view, cursor, search_date=search_date, fingerprint=fingerprint
    )


def build_presented_availability_page(
    raw_slots: Any,
    *,
    page_index: int,
    page_size: int = DEFAULT_MAX_TIMES,
    search_date: Optional[str] = None,
    fingerprint: Optional[str] = None,
) -> PresentedAvailability:
    """Build presented_availability for a page slice within one date group."""
    shaped = (
        filter_slots_to_search_date(raw_slots, search_date)
        if search_date
        else raw_slots
    )
    view = _build_availability_view(shaped, page_size=page_size)
    cursor = _initial_cursor(view, search_date=search_date)
    cursor = _Cursor(cursor.group_index, max(0, int(page_index)), page_size)
    return _project_presented(
        view, cursor, search_date=search_date, fingerprint=fingerprint
    )


def build_availability_presentation(
    raw_slots: Any,
    *,
    page_size: int = DEFAULT_MAX_TIMES,
    page_index: int = 0,
    search_date: Optional[str] = None,
) -> Dict[str, Any]:
    """Build legacy availability_presentation metadata for session persistence."""
    view = _build_availability_view(raw_slots, page_size=page_size)
    cursor = _initial_cursor(view, search_date=search_date)
    cursor = _Cursor(cursor.group_index, max(0, int(page_index)), page_size)
    clamped = _clamp_cursor(view, cursor)
    if clamped is None or not view.groups:
        return {
            "page_index": 0,
            "page_size": page_size,
            "has_next": False,
            "has_previous": False,
        }
    group = view.groups[clamped.group_index]
    pages = _pages_in_group(group, page_size)
    has_next_times = clamped.page_index + 1 < pages
    has_later_group = clamped.group_index + 1 < len(view.groups)
    has_previous_times = clamped.page_index > 0
    has_earlier_group = clamped.group_index > 0
    return {
        "page_index": clamped.page_index,
        "page_size": page_size,
        "has_next": has_next_times or has_later_group,
        "has_previous": has_previous_times or has_earlier_group,
    }


def compute_target_page_index(
    current_index: int,
    direction: str,
    total_unique: int,
    page_size: int,
) -> tuple[int, bool]:
    """Legacy single-axis page math (time-only within a flat list).

    Prefer ``advance_presentation`` for unified browse.
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


def presentation_meta_from_presented(
    presented: PresentedAvailability,
) -> Dict[str, Any]:
    """Derive legacy availability_presentation fields from a presented window."""
    hints = presented.get("browse_hints") if isinstance(presented, dict) else None
    hints = hints if isinstance(hints, dict) else {}
    cursor = presented.get(_CURSOR_KEY) if isinstance(presented, dict) else None
    page_index = 0
    page_size = DEFAULT_MAX_TIMES
    if isinstance(cursor, dict):
        try:
            page_index = int(cursor.get("page_index") or 0)
            page_size = int(cursor.get("page_size") or DEFAULT_MAX_TIMES)
        except (TypeError, ValueError):
            pass
    return {
        "page_index": page_index,
        "page_size": page_size,
        "has_next": bool(hints.get("has_more_any")),
        "has_previous": bool(hints.get("has_previous_any")),
    }


# ---------------------------------------------------------------------------
# Selection mismatch location (explanation only; does not change bind rules)
# ---------------------------------------------------------------------------

SELECTION_MISMATCH_CURRENT_PAGE = "CURRENT_PAGE"
SELECTION_MISMATCH_EARLIER_PAGE = "EARLIER_PAGE"
SELECTION_MISMATCH_LATER_PAGE = "LATER_PAGE"
SELECTION_MISMATCH_NOT_IN_CACHE = "NOT_IN_CACHE"


def _parse_start_date_time(start: str) -> Optional[Tuple[str, str]]:
    """Parse an ISO start into (YYYY-MM-DD, HH:MM)."""
    raw = str(start).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except (ValueError, TypeError):
        return None
    return parsed.date().isoformat(), f"{parsed.hour:02d}:{parsed.minute:02d}"


def classify_selection_mismatch_location(
    *,
    cache: Optional[AvailabilityCache],
    presented: Optional[PresentedAvailability],
    requested_time: str,
    search_date: Optional[str] = None,
) -> str:
    """Locate a normalized ``HH:MM`` relative to the current presented window.

    Uses the ordered trusted cache and the existing presented cursor/window.
    Does not change selection eligibility — classification is for wording only.
    """
    requested = str(requested_time or "").strip()
    if not requested or not isinstance(cache, dict):
        return SELECTION_MISMATCH_NOT_IN_CACHE

    raw_slots = cache.get("slots")
    if not isinstance(raw_slots, list) or not raw_slots:
        return SELECTION_MISMATCH_NOT_IN_CACHE

    page_size = DEFAULT_MAX_TIMES
    if isinstance(presented, dict):
        raw_cursor = presented.get(_CURSOR_KEY)
        if isinstance(raw_cursor, dict) and raw_cursor.get("page_size") is not None:
            try:
                page_size = int(raw_cursor["page_size"])
            except (TypeError, ValueError):
                pass

    view = _build_availability_view(raw_slots, page_size=page_size)
    if not view.groups:
        return SELECTION_MISMATCH_NOT_IN_CACHE

    cursor = _cursor_from_presented(view, presented)
    page_slots, _times, window_date, _more, _total = _window_for_cursor(view, cursor)
    window_starts = {
        start for start in (_slot_start_iso(slot) for slot in page_slots) if start
    }

    expected_date = normalize_search_date(search_date)
    if not expected_date and isinstance(presented, dict):
        expected_date = normalize_search_date(presented.get("search_date"))
    if not expected_date:
        expected_date = window_date

    ordered_starts: List[str] = []
    for group in view.groups:
        for slot in group.slots:
            start = _slot_start_iso(slot)
            if start:
                ordered_starts.append(start)

    matches: List[str] = []
    for start in ordered_starts:
        parts = _parse_start_date_time(start)
        if not parts:
            continue
        offer_date, offer_time = parts
        if offer_time != requested:
            continue
        if expected_date and offer_date != expected_date:
            continue
        matches.append(start)

    if not matches:
        return SELECTION_MISMATCH_NOT_IN_CACHE

    for start in matches:
        if start in window_starts:
            return SELECTION_MISMATCH_CURRENT_PAGE

    if not window_starts:
        return SELECTION_MISMATCH_NOT_IN_CACHE

    window_ordered = [s for s in ordered_starts if s in window_starts]
    if not window_ordered:
        return SELECTION_MISMATCH_NOT_IN_CACHE

    match = matches[0]
    first_visible = window_ordered[0]
    last_visible = window_ordered[-1]
    if match < first_visible:
        return SELECTION_MISMATCH_EARLIER_PAGE
    if match > last_visible:
        return SELECTION_MISMATCH_LATER_PAGE
    return SELECTION_MISMATCH_NOT_IN_CACHE


# ---------------------------------------------------------------------------
# Session adapters (canonical nested Session V2 availability.*)
# ---------------------------------------------------------------------------

# Historical flat keys — read fallback only during migration; never written by helpers.
_LEGACY_CACHE_KEY = "last_execution_result"
_LEGACY_PRESENTED_KEY = "presented_availability"
_LEGACY_FINGERPRINT_KEY = "availability_fingerprint"
_LEGACY_PRESENTATION_KEY = "availability_presentation"


def _availability_section(session_state: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not isinstance(session_state, dict):
        return None
    availability = session_state.get("availability")
    return availability if isinstance(availability, dict) else None


def _ensure_availability_section(session_state: Dict[str, Any]) -> Dict[str, Any]:
    availability = session_state.get("availability")
    if not isinstance(availability, dict):
        availability = {}
        session_state["availability"] = availability
    cache = availability.get("cache")
    if not isinstance(cache, dict):
        cache = {}
        availability["cache"] = cache
    presentation = availability.get("presentation")
    if not isinstance(presentation, dict):
        presentation = {}
        availability["presentation"] = presentation
    return availability


def _defensive_dict(value: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(value, dict):
        return None
    return deepcopy(value)


def availability_fingerprint_from_session(
    session_state: Optional[Dict[str, Any]],
) -> Optional[str]:
    """Read canonical availability.fingerprint (legacy flat fallback)."""
    availability = _availability_section(session_state)
    if availability is not None and availability.get("fingerprint") is not None:
        return availability.get("fingerprint")
    if isinstance(session_state, dict):
        legacy = session_state.get(_LEGACY_FINGERPRINT_KEY)
        if legacy is not None:
            return legacy
    return None


def availability_cache_from_session(
    session_state: Optional[Dict[str, Any]],
) -> Optional[AvailabilityCache]:
    """Sole domain-level reader of the persisted availability cache field."""
    if not isinstance(session_state, dict):
        return None

    last_result = None
    availability = _availability_section(session_state)
    if availability is not None:
        cache = availability.get("cache")
        if isinstance(cache, dict) and cache.get("search_result") is not None:
            last_result = cache.get("search_result")
    if last_result is None:
        last_result = session_state.get(_LEGACY_CACHE_KEY)

    if not isinstance(last_result, dict):
        return None
    if last_result.get("type") != "availability" or last_result.get("status") != "success":
        return None
    slots = last_result.get("slots")
    if not isinstance(slots, list):
        return None
    cache_out: AvailabilityCache = {
        "type": "availability",
        "status": "success",
        "slots": list(slots),
        "fingerprint": last_result.get("availability_fingerprint")
        or availability_fingerprint_from_session(session_state),
        "search_date": normalize_search_date(last_result.get("search_date")),
    }
    if isinstance(last_result.get("time_resolution"), dict):
        cache_out["time_resolution"] = deepcopy(last_result["time_resolution"])
    return cache_out


def _is_presented_window(value: Any) -> bool:
    """True for a presentation window (slots list and/or non-empty display times)."""
    if not isinstance(value, dict):
        return False
    if isinstance(value.get("slots"), list):
        return True
    times = value.get("times")
    return isinstance(times, list) and any(times)


def presented_availability_from_session(
    session_state: Optional[Dict[str, Any]],
) -> Optional[PresentedAvailability]:
    """Sole domain-level reader of the persisted presentation window."""
    if not isinstance(session_state, dict):
        return None
    availability = _availability_section(session_state)
    if availability is not None:
        presentation = availability.get("presentation")
        if isinstance(presentation, dict):
            presented = presentation.get("presented")
            if _is_presented_window(presented):
                return _defensive_dict(presented)  # type: ignore[return-value]
    presented = session_state.get(_LEGACY_PRESENTED_KEY)
    if _is_presented_window(presented):
        return _defensive_dict(presented)  # type: ignore[return-value]
    return None


# Backward-compatible alias used within this package.
presented_from_session = presented_availability_from_session


def availability_pagination_from_session(
    session_state: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Return page_index/page_size from canonical presentation (flat fallback)."""
    if not isinstance(session_state, dict):
        return None
    availability = _availability_section(session_state)
    if availability is not None:
        presentation = availability.get("presentation")
        if isinstance(presentation, dict) and (
            presentation.get("page_index") is not None
            or presentation.get("page_size") is not None
        ):
            return {
                "page_index": presentation.get("page_index") or 0,
                "page_size": presentation.get("page_size"),
            }
    legacy = session_state.get(_LEGACY_PRESENTATION_KEY)
    if isinstance(legacy, dict) and (
        legacy.get("page_index") is not None or legacy.get("page_size") is not None
    ):
        return {
            "page_index": legacy.get("page_index") or 0,
            "page_size": legacy.get("page_size"),
        }
    return None


def has_trusted_availability_cache(
    session_state: Optional[Dict[str, Any]],
) -> bool:
    """True when session holds a non-empty trusted AvailabilityCache."""
    cache = availability_cache_from_session(session_state)
    return cache is not None and bool(cache.get("slots"))


def set_availability_fingerprint(
    session_state: Dict[str, Any],
    fingerprint: Optional[str],
) -> None:
    """Write canonical availability.fingerprint only (no flat dual-write)."""
    availability = _ensure_availability_section(session_state)
    if fingerprint is None:
        availability.pop("fingerprint", None)
    else:
        availability["fingerprint"] = fingerprint


def set_availability_search_result(
    session_state: Dict[str, Any],
    search_result: Optional[Mapping[str, Any]],
) -> None:
    """Write canonical availability.cache.search_result only."""
    availability = _ensure_availability_section(session_state)
    cache = availability["cache"]
    if search_result is None:
        cache.pop("search_result", None)
    else:
        cache["search_result"] = deepcopy(dict(search_result))


def set_presented_availability(
    session_state: Dict[str, Any],
    presented: Optional[Mapping[str, Any]],
) -> None:
    """Write canonical availability.presentation.presented only."""
    availability = _ensure_availability_section(session_state)
    presentation = availability["presentation"]
    if presented is None:
        presentation.pop("presented", None)
    else:
        presentation["presented"] = deepcopy(dict(presented))


def set_availability_pagination(
    session_state: Dict[str, Any],
    *,
    page_index: Optional[int] = None,
    page_size: Optional[int] = None,
    presentation: Optional[Mapping[str, Any]] = None,
) -> None:
    """Write canonical page_index/page_size (and optional metadata keys)."""
    availability = _ensure_availability_section(session_state)
    target = availability["presentation"]
    if isinstance(presentation, Mapping):
        if presentation.get("page_index") is not None:
            target["page_index"] = presentation.get("page_index")
        if "page_size" in presentation:
            target["page_size"] = presentation.get("page_size")
        return
    if page_index is not None:
        target["page_index"] = page_index
    if page_size is not None:
        target["page_size"] = page_size


def apply_availability_artifacts(
    session_state: Dict[str, Any],
    *,
    fingerprint: Any = None,
    search_result: Any = None,
    presented: Any = None,
    presentation: Any = None,
) -> None:
    """Apply one or more availability artifacts to nested Session V2 only."""
    if fingerprint is not None:
        set_availability_fingerprint(session_state, fingerprint)
    if search_result is not None:
        set_availability_search_result(session_state, search_result)
    if presented is not None:
        set_presented_availability(session_state, presented)
    if isinstance(presentation, Mapping):
        set_availability_pagination(session_state, presentation=presentation)


def clear_availability_artifacts(session_state: Optional[Dict[str, Any]]) -> None:
    """Clear nested availability artifacts and any lingering flat mirrors."""
    if not isinstance(session_state, dict):
        return
    availability = session_state.get("availability")
    if isinstance(availability, dict):
        availability["fingerprint"] = None
        cache = availability.get("cache")
        if not isinstance(cache, dict):
            cache = {}
            availability["cache"] = cache
        cache["search_result"] = None
        presentation = availability.get("presentation")
        if not isinstance(presentation, dict):
            presentation = {}
            availability["presentation"] = presentation
        presentation["presented"] = None
        presentation["page_index"] = 0
        presentation["page_size"] = None
    for key in (
        _LEGACY_FINGERPRINT_KEY,
        _LEGACY_CACHE_KEY,
        _LEGACY_PRESENTED_KEY,
        _LEGACY_PRESENTATION_KEY,
    ):
        session_state.pop(key, None)


def clear_availability_presentation(session_state: Optional[Dict[str, Any]]) -> None:
    """Clear presented window and pagination; keep fingerprint/cache."""
    if not isinstance(session_state, dict):
        return
    availability = _ensure_availability_section(session_state)
    presentation = availability["presentation"]
    presentation["presented"] = None
    presentation["page_index"] = 0
    presentation["page_size"] = None
    session_state.pop(_LEGACY_PRESENTED_KEY, None)
    session_state.pop(_LEGACY_PRESENTATION_KEY, None)


def project_availability_cache_to_session(
    cache: AvailabilityCache,
) -> Dict[str, Any]:
    """Map AvailabilityCache onto a nested availability patch (no flat keys).

    Returns a minimal session fragment with ``availability.*`` only. Callers that
    previously expected ``{last_execution_result: ...}`` should apply the patch
    via ``apply_availability_artifacts`` / ``set_availability_search_result``.
    """
    payload: Dict[str, Any] = {
        "type": cache.get("type") or "availability",
        "status": cache.get("status") or "success",
        "slots": list(cache.get("slots") or []),
    }
    if cache.get("fingerprint"):
        payload["availability_fingerprint"] = cache["fingerprint"]
    if cache.get("search_date"):
        payload["search_date"] = cache["search_date"]
    fragment: Dict[str, Any] = {"availability": {}}
    apply_availability_artifacts(
        fragment,
        fingerprint=cache.get("fingerprint"),
        search_result=payload,
    )
    return fragment


def ensure_presented_availability(
    *,
    session_state: Optional[Dict[str, Any]] = None,
    raw_slots: Any = None,
    search_date: Optional[str] = None,
    fingerprint: Optional[str] = None,
) -> Optional[PresentedAvailability]:
    """Return session presentation when present, else build from cache/raw slots."""
    from core.workflows.availability.discovery.bridge import present_via_discovery

    presented = presented_availability_from_session(session_state)
    if presented is not None and isinstance(presented.get("slots"), list) and presented.get("slots"):
        return presented

    criteria_slots, criteria_date_proposal = search_criteria_from_session(session_state)
    offer_slots = raw_slots
    date = search_date
    fp = fingerprint
    if offer_slots is None:
        cache = availability_cache_from_session(session_state)
        if cache is None:
            return presented  # may be empty window
        return present_via_discovery(
            cache,
            search_date=date or cache.get("search_date"),
            slots=criteria_slots,
            date_proposal=criteria_date_proposal,
        )
    cache: AvailabilityCache = {
        "slots": list(offer_slots) if isinstance(offer_slots, list) else [],
        "fingerprint": fp,
        "search_date": date,
        "type": "availability",
        "status": "ok",
    }
    return present_via_discovery(
        cache,
        search_date=date,
        slots=criteria_slots,
        date_proposal=criteria_date_proposal,
    )
