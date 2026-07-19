"""Availability presentation: window construction, paging, and browse metadata.

Owns grouping, filtering, dedupe, page limits, and public PresentedAvailability
projection. Internal cursor/index math stays in this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

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
            "has_next_date": False,
            "has_previous_date": False,
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
    has_next_date = clamped.group_index + 1 < len(view.groups)
    has_previous_date = clamped.group_index > 0
    has_more_any = has_more_times or has_next_date
    has_previous_any = has_previous_times or has_previous_date

    suggested_next: Optional[str] = None
    if has_more_times:
        suggested_next = "show more"
    elif has_next_date:
        suggested_next = "next day"

    suggested_previous: Optional[str] = None
    if has_previous_times or has_previous_date:
        if has_previous_times:
            suggested_previous = "go back"
        else:
            suggested_previous = "previous day"

    more_count = max(0, len(group.slots) - (clamped.page_index + 1) * clamped.page_size)
    return {
        "has_more_times": has_more_times,
        "has_previous_times": has_previous_times,
        "has_next_date": has_next_date,
        "has_previous_date": has_previous_date,
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
    fingerprint: Optional[str] = None,
    browse_status: Optional[str] = None,
) -> PresentedAvailability:
    clamped = _clamp_cursor(view, cursor) or _Cursor(0, 0, view.page_size)
    page_slots, times, date_label, more_count, total = _window_for_cursor(view, clamped)
    presented: PresentedAvailability = {
        "search_date": date_label,
        "slots": page_slots,
        "times": times,
        "more_count": more_count,
        "total_unique": total,
        "browse_hints": _browse_hints_for_cursor(view, clamped),
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
) -> PresentedAvailability:
    """Build the first visible window from a trusted AvailabilityCache."""
    slots = cache.get("slots") or []
    view = _build_availability_view(slots, page_size=page_size)
    preferred = search_date or cache.get("search_date")
    cursor = _initial_cursor(view, search_date=preferred)
    return _project_presented(
        view, cursor, fingerprint=cache.get("fingerprint")
    )


def project_presentation_to_date(
    cache: AvailabilityCache,
    target_date: str,
    *,
    current_presentation: Optional[PresentedAvailability] = None,
    page_size: Optional[int] = None,
) -> BrowseProjection:
    """Project the first page of a named date when it exists in the trusted cache.

    Does not invent empty date groups. On miss, preserves the prior window and
    returns ``target_date_not_in_cache``.
    """
    resolved_page_size = page_size or DEFAULT_MAX_TIMES
    if isinstance(current_presentation, dict):
        raw_cursor = current_presentation.get(_CURSOR_KEY)
        if isinstance(raw_cursor, dict) and raw_cursor.get("page_size"):
            try:
                resolved_page_size = int(raw_cursor["page_size"])
            except (TypeError, ValueError):
                pass

    slots = cache.get("slots") or []
    view = _build_availability_view(slots, page_size=resolved_page_size)
    normalized = normalize_search_date(target_date)

    def _preserve(reason: str) -> BrowseProjection:
        if isinstance(current_presentation, dict) and isinstance(
            current_presentation.get("slots"), list
        ):
            presented = dict(current_presentation)
            presented["browse_status"] = reason
            return {"presented": presented, "moved": False, "reason_code": reason}
        cursor = _cursor_from_presented(view, current_presentation)
        presented = _project_presented(
            view, cursor, fingerprint=cache.get("fingerprint"), browse_status=reason
        )
        return {"presented": presented, "moved": False, "reason_code": reason}

    if not normalized or not view.groups:
        return _preserve("target_date_not_in_cache")

    for gi, group in enumerate(view.groups):
        if group.date == normalized:
            presented = _project_presented(
                view,
                _Cursor(gi, 0, resolved_page_size),
                fingerprint=cache.get("fingerprint"),
            )
            return {
                "presented": presented,
                "moved": True,
                "reason_code": "date_projected",
            }
    return _preserve("target_date_not_in_cache")


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
) -> BrowseProjection:
    """Advance the presentation window according to BrowseIntent."""
    slots = cache.get("slots") or []
    resolved_page_size = page_size or DEFAULT_MAX_TIMES
    if isinstance(current_presentation, dict):
        raw_cursor = current_presentation.get(_CURSOR_KEY)
        if isinstance(raw_cursor, dict) and raw_cursor.get("page_size"):
            try:
                resolved_page_size = int(raw_cursor["page_size"])
            except (TypeError, ValueError):
                pass

    view = _build_availability_view(slots, page_size=resolved_page_size)
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
            return _stay("no_more_times_for_date")
        if axis == "date":
            if cursor.group_index + 1 < len(view.groups):
                return _move(
                    _Cursor(cursor.group_index + 1, 0, cursor.page_size)
                )
            return _stay("no_next_date")
        # any: times then next date
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
            return _stay("no_previous_times_for_date")
        if axis == "date":
            if cursor.group_index > 0:
                # Date-axis always opens the first page of the target date.
                return _move(_Cursor(cursor.group_index - 1, 0, cursor.page_size))
            return _stay("no_previous_date")
        # any: previous time page, else final page of previous date
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
    """Build the selectable availability payload shown to the user."""
    view = _build_availability_view(raw_slots, page_size=max_times)
    cursor = _initial_cursor(view, search_date=search_date)
    return _project_presented(view, cursor, fingerprint=fingerprint)


def build_presented_availability_page(
    raw_slots: Any,
    *,
    page_index: int,
    page_size: int = DEFAULT_MAX_TIMES,
    search_date: Optional[str] = None,
    fingerprint: Optional[str] = None,
) -> PresentedAvailability:
    """Build presented_availability for a page slice within one date group."""
    view = _build_availability_view(raw_slots, page_size=page_size)
    cursor = _initial_cursor(view, search_date=search_date)
    cursor = _Cursor(cursor.group_index, max(0, int(page_index)), page_size)
    return _project_presented(view, cursor, fingerprint=fingerprint)


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
    has_next_date = clamped.group_index + 1 < len(view.groups)
    has_previous_times = clamped.page_index > 0
    has_previous_date = clamped.group_index > 0
    return {
        "page_index": clamped.page_index,
        "page_size": page_size,
        "has_next": has_next_times or has_next_date,
        "has_previous": has_previous_times or has_previous_date,
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
# Session adapters
# ---------------------------------------------------------------------------

# Persistence field names — private to this adapter. Domain code must not use them.
_SESSION_CACHE_KEY = "last_execution_result"
_SESSION_PRESENTED_KEY = "presented_availability"


def availability_cache_from_session(
    session_state: Optional[Dict[str, Any]],
) -> Optional[AvailabilityCache]:
    """Sole domain-level reader of the persisted availability cache field."""
    if not isinstance(session_state, dict):
        return None
    last_result = session_state.get(_SESSION_CACHE_KEY)
    if not isinstance(last_result, dict):
        return None
    if last_result.get("type") != "availability" or last_result.get("status") != "success":
        return None
    slots = last_result.get("slots")
    if not isinstance(slots, list):
        return None
    cache: AvailabilityCache = {
        "type": "availability",
        "status": "success",
        "slots": list(slots),
        "fingerprint": last_result.get("availability_fingerprint"),
        "search_date": normalize_search_date(last_result.get("search_date")),
    }
    if isinstance(last_result.get("time_resolution"), dict):
        cache["time_resolution"] = last_result["time_resolution"]
    return cache


def presented_availability_from_session(
    session_state: Optional[Dict[str, Any]],
) -> Optional[PresentedAvailability]:
    """Sole domain-level reader of the persisted presentation window."""
    if not isinstance(session_state, dict):
        return None
    presented = session_state.get(_SESSION_PRESENTED_KEY)
    if isinstance(presented, dict) and isinstance(presented.get("slots"), list):
        return presented  # type: ignore[return-value]
    return None


# Backward-compatible alias used within this package.
presented_from_session = presented_availability_from_session


def has_trusted_availability_cache(
    session_state: Optional[Dict[str, Any]],
) -> bool:
    """True when session holds a non-empty trusted AvailabilityCache."""
    cache = availability_cache_from_session(session_state)
    return cache is not None and bool(cache.get("slots"))


def project_availability_cache_to_session(
    cache: AvailabilityCache,
) -> Dict[str, Any]:
    """Map AvailabilityCache to persistence fields (session projector only)."""
    payload: Dict[str, Any] = {
        "type": cache.get("type") or "availability",
        "status": cache.get("status") or "success",
        "slots": list(cache.get("slots") or []),
    }
    if cache.get("fingerprint"):
        payload["availability_fingerprint"] = cache["fingerprint"]
    if cache.get("search_date"):
        payload["search_date"] = cache["search_date"]
    return {_SESSION_CACHE_KEY: payload}


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

    slots = raw_slots
    date = search_date
    fp = fingerprint
    if slots is None:
        cache = availability_cache_from_session(session_state)
        if cache is None:
            return presented  # may be empty window
        return present_via_discovery(
            cache, search_date=date or cache.get("search_date")
        )
    cache: AvailabilityCache = {
        "slots": list(slots) if isinstance(slots, list) else [],
        "fingerprint": fp,
        "search_date": date,
        "type": "availability",
        "status": "ok",
    }
    return present_via_discovery(cache, search_date=date)
