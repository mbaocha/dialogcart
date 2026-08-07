"""Focused tests: criteria-shaped presentation result sets and pure pagination."""

from __future__ import annotations

from core.workflows.availability.contracts import AvailabilityCache
from core.workflows.availability.discovery.bridge import present_via_discovery
from core.workflows.availability.presentation import (
    advance_presentation,
    build_initial_presentation,
    build_presented_availability,
    presentation_slots_from_cache,
    resolve_criteria_span,
)


JULY_24 = "2026-07-24"
JULY_25 = "2026-07-25"
JULY_26 = "2026-07-26"


def _slots(*days: str, hours=range(9, 12)) -> list:
    out = []
    for day in days:
        for h in hours:
            out.append(
                {
                    "starts_at": f"{day}T{h:02d}:00:00Z",
                    "ends_at": f"{day}T{h:02d}:30:00Z",
                }
            )
    return out


def test_single_day_criteria_span_from_date():
    span, start, end = resolve_criteria_span(
        slots={"service_id": "premium haircut", "date": JULY_24},
        search_date=JULY_24,
    )
    assert span == "single_day"
    assert start == JULY_24
    assert end == JULY_24


def test_empty_search_preserves_authoritative_search_date():
    cache: AvailabilityCache = {
        "type": "availability",
        "status": "success",
        "slots": [],
        "search_date": "2026-09-08",
        "fingerprint": "fp-empty-september-8",
    }

    presented = present_via_discovery(
        cache,
        search_date="2026-09-08",
        slots={"service_id": "executive oil change", "date": "2026-09-08"},
    )

    assert presented["search_date"] == "2026-09-08"
    assert presented["slots"] == []
    assert presented["times"] == []
    assert presented["total_unique"] == 0
    assert cache["search_date"] == "2026-09-08"


def test_multi_day_criteria_span_from_date_range():
    span, start, end = resolve_criteria_span(
        slots={
            "service_id": "premium haircut",
            "date_range": {"start": JULY_24, "end": JULY_26},
        },
    )
    assert span == "multi_day"
    assert start == JULY_24
    assert end == JULY_26


def test_single_day_provider_surplus_excluded_from_presentation():
    """Provider returns July 24–26; single-day search presents only July 24."""
    cache: AvailabilityCache = {
        "type": "availability",
        "status": "success",
        "slots": _slots(JULY_24, JULY_25, JULY_26),
        "search_date": JULY_24,
        "fingerprint": "fp-july24",
    }
    criteria = {"service_id": "premium haircut", "date": JULY_24}
    shaped = presentation_slots_from_cache(cache, slots=criteria)
    assert shaped
    assert all(s["starts_at"].startswith(JULY_24) for s in shaped)
    assert len(shaped) == 3

    presented = build_initial_presentation(cache, slots=criteria)
    assert presented["search_date"] == JULY_24
    assert all(
        slot["starts_at"].startswith(JULY_24) for slot in (presented.get("slots") or [])
    )
    hints = presented.get("browse_hints") or {}
    assert hints.get("suggested_next") in (None, "next")
    assert "next day" not in str(hints.get("suggested_next") or "")


def test_single_day_next_never_spills_to_provider_surplus():
    """Paginate until exhaustion — never reach July 25 surplus rows."""
    many_hours = list(range(9, 18))  # 9 slots → 2 pages at size 6
    cache: AvailabilityCache = {
        "type": "availability",
        "status": "success",
        "slots": _slots(JULY_24, JULY_25, hours=many_hours),
        "search_date": JULY_24,
        "fingerprint": "fp-july24",
    }
    criteria = {"service_id": "premium haircut", "date": JULY_24}
    page0 = build_initial_presentation(cache, page_size=6, slots=criteria)
    assert all(s["starts_at"].startswith(JULY_24) for s in page0["slots"])

    page1 = advance_presentation(
        cache,
        page0,
        {"direction": "next", "axis_hint": "any"},
        page_size=6,
        slots=criteria,
    )
    assert page1["moved"] is True
    presented1 = page1["presented"]
    assert all(s["starts_at"].startswith(JULY_24) for s in presented1["slots"])
    assert presented1.get("fingerprint") == "fp-july24"

    exhausted = advance_presentation(
        cache,
        presented1,
        {"direction": "next", "axis_hint": "any"},
        page_size=6,
        slots=criteria,
    )
    assert exhausted["moved"] is False
    assert exhausted["reason_code"] == "exhausted"
    preserved = exhausted["presented"]
    assert all(s["starts_at"].startswith(JULY_24) for s in preserved["slots"])
    assert JULY_25 not in str(preserved.get("slots"))


def test_previous_restores_prior_page_without_fingerprint_change():
    cache: AvailabilityCache = {
        "type": "availability",
        "status": "success",
        "slots": _slots(JULY_24, hours=range(9, 18)),
        "search_date": JULY_24,
        "fingerprint": "fp-stable",
    }
    criteria = {"service_id": "premium haircut", "date": JULY_24}
    page0 = build_initial_presentation(cache, page_size=6, slots=criteria)
    page1 = advance_presentation(
        cache,
        page0,
        {"direction": "next", "axis_hint": "any"},
        page_size=6,
        slots=criteria,
    )
    assert page1["moved"] is True
    back = advance_presentation(
        cache,
        page1["presented"],
        {"direction": "previous", "axis_hint": "any"},
        page_size=6,
        slots=criteria,
    )
    assert back["moved"] is True
    restored = back["presented"]
    assert restored.get("fingerprint") == "fp-stable"
    assert restored["slots"][0]["starts_at"] == page0["slots"][0]["starts_at"]


def test_explicit_multi_day_criteria_keeps_qualifying_dates():
    cache: AvailabilityCache = {
        "type": "availability",
        "status": "success",
        "slots": _slots(JULY_24, JULY_25, JULY_26),
        "search_date": JULY_24,
        "fingerprint": "fp-range",
    }
    criteria = {
        "service_id": "premium haircut",
        "date_range": {"start": JULY_24, "end": JULY_26},
    }
    shaped = presentation_slots_from_cache(cache, slots=criteria)
    dates = {s["starts_at"][:10] for s in shaped}
    assert dates == {JULY_24, JULY_25, JULY_26}

    page0 = build_initial_presentation(cache, page_size=6, slots=criteria)
    nxt = advance_presentation(
        cache,
        page0,
        {"direction": "next", "axis_hint": "any"},
        page_size=6,
        slots=criteria,
    )
    assert nxt["moved"] is True
    assert nxt["presented"]["search_date"] == JULY_25


def test_build_presented_availability_filters_provider_surplus_when_search_date_set():
    raw = _slots(JULY_24, JULY_25)
    presented = build_presented_availability(raw, search_date=JULY_24)
    assert presented["search_date"] == JULY_24
    assert all(s["starts_at"].startswith(JULY_24) for s in presented["slots"])
