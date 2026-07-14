"""Tests for availability slot summarization and render request building."""

from core.planning.temporal_proposal import try_bind_offered_time_selection
from core.rendering.availability_renderer import (
    build_availability_presentation,
    build_availability_render_request,
    build_presented_availability,
    build_presented_availability_page,
    summarize_availability_slots,
)


def _multi_day_raw_slots():
    return [
        {
            "starts_at": "2026-07-08T16:30:00.000Z",
            "ends_at": "2026-07-08T17:00:00.000Z",
        },
        {
            "starts_at": "2026-07-09T09:00:00.000Z",
            "ends_at": "2026-07-09T09:30:00.000Z",
        },
        {
            "starts_at": "2026-07-09T10:00:00.000Z",
            "ends_at": "2026-07-09T10:30:00.000Z",
        },
        {
            "starts_at": "2026-07-09T10:30:00.000Z",
            "ends_at": "2026-07-09T11:00:00.000Z",
        },
    ]

def test_summarize_dedupes_by_start_time():
    raw = [
        {"starts_at": "2026-07-02T09:00:00.000Z", "ends_at": "2026-07-02T09:30:00.000Z"},
        {"starts_at": "2026-07-02T09:00:00.000Z", "ends_at": "2026-07-02T09:30:00.000Z"},
        {"starts_at": "2026-07-02T09:30:00.000Z", "ends_at": "2026-07-02T10:00:00.000Z"},
    ]
    summary = summarize_availability_slots(raw, max_times=6)
    assert summary["total_unique"] == 2
    assert summary["date"] == "2026-07-02"
    assert len(summary["times"]) == 2
    assert len(summary["presented_slots"]) == 2
    assert summary["more_count"] == 0


def test_summarize_caps_times_and_reports_more():
    raw = [
        {"starts_at": f"2026-07-02T{h:02d}:00:00.000Z", "ends_at": f"2026-07-02T{h:02d}:30:00.000Z"}
        for h in range(9, 18)
    ]
    summary = summarize_availability_slots(raw, max_times=3)
    assert summary["total_unique"] == 9
    assert len(summary["times"]) == 3
    assert len(summary["presented_slots"]) == 3
    assert summary["more_count"] == 6


def test_build_presented_availability_matches_display_cap():
    raw = [
        {"starts_at": f"2026-07-02T{h:02d}:00:00.000Z", "ends_at": f"2026-07-02T{h:02d}:30:00.000Z"}
        for h in range(9, 18)
    ]
    presented = build_presented_availability(raw, max_times=6)
    assert presented["search_date"] == "2026-07-02"
    assert len(presented["slots"]) == 6
    assert presented["more_count"] == 3
    assert presented["slots"][0]["starts_at"].startswith("2026-07-02T09:00")
    assert presented["slots"][-1]["starts_at"].startswith("2026-07-02T14:00")


def test_build_availability_presentation_initial_page():
    raw = [
        {"starts_at": f"2026-07-02T{h:02d}:00:00.000Z", "ends_at": f"2026-07-02T{h:02d}:30:00.000Z"}
        for h in range(9, 18)
    ]
    presentation = build_availability_presentation(raw)
    assert presentation == {
        "page_index": 0,
        "page_size": 6,
        "has_next": True,
        "has_previous": False,
    }


def test_build_availability_presentation_no_extra_pages():
    raw = [
        {"starts_at": "2026-07-02T09:00:00.000Z", "ends_at": "2026-07-02T09:30:00.000Z"},
        {"starts_at": "2026-07-02T10:00:00.000Z", "ends_at": "2026-07-02T10:30:00.000Z"},
    ]
    presentation = build_availability_presentation(raw)
    assert presentation == {
        "page_index": 0,
        "page_size": 6,
        "has_next": False,
        "has_previous": False,
    }


def test_build_availability_presentation_empty_slots():
    presentation = build_availability_presentation([])
    assert presentation == {
        "page_index": 0,
        "page_size": 6,
        "has_next": False,
        "has_previous": False,
    }


def test_build_presented_availability_page_second_page():
    raw = [
        {"starts_at": f"2026-07-02T{h:02d}:00:00.000Z", "ends_at": f"2026-07-02T{h:02d}:30:00.000Z"}
        for h in range(9, 18)
    ]
    page1 = build_presented_availability_page(raw, page_index=1, page_size=6)
    assert len(page1["slots"]) == 3
    assert page1["slots"][0]["starts_at"].startswith("2026-07-02T15:00")
    assert page1["more_count"] == 0


def test_build_render_request_includes_availability_facts():
    decision = {
        "intent_name": "CREATE_APPOINTMENT",
        "facts": {"slots": {"service_id": "premium haircut"}},
    }
    execution = {
        "type": "availability",
        "status": "success",
        "slots": [
            {"starts_at": "2026-07-02T09:00:00.000Z", "ends_at": "2026-07-02T09:30:00.000Z"},
        ],
    }
    req = build_availability_render_request(decision, execution)
    assert req is not None
    assert req.facts["availability"]["service_name"] == "Premium Haircut"
    assert req.facts["availability"]["times"]
    assert "bullet list" in req.render_instruction.lower()


def test_build_render_request_none_when_no_slots():
    assert build_availability_render_request({}, {"type": "availability", "status": "success", "slots": []}) is None


def test_summarize_hardens_single_day_when_raw_slots_span_dates():
    summary = summarize_availability_slots(_multi_day_raw_slots(), max_times=6)
    assert summary["date"] == "2026-07-08"
    assert len(summary["presented_slots"]) == 1
    assert summary["presented_slots"][0]["starts_at"].startswith("2026-07-08T16:30")


def test_build_presented_availability_filters_to_search_date():
    raw = _multi_day_raw_slots()
    presented = build_presented_availability(raw, search_date="2026-07-08")
    assert presented["search_date"] == "2026-07-08"
    assert len(presented["slots"]) == 1
    assert all(slot["starts_at"].startswith("2026-07-08") for slot in presented["slots"])


def test_build_presented_availability_page_filters_to_search_date():
    raw = _multi_day_raw_slots()
    page = build_presented_availability_page(
        raw, page_index=0, page_size=6, search_date="2026-07-08"
    )
    assert page["search_date"] == "2026-07-08"
    assert len(page["slots"]) == 1
    assert page["slots"][0]["starts_at"].startswith("2026-07-08T16:30")


def test_build_presented_availability_preserves_full_cache_input():
    raw = _multi_day_raw_slots()
    original = [dict(slot) for slot in raw]
    build_presented_availability(raw, search_date="2026-07-08")
    assert raw == original
    assert len(raw) == 4


def test_time_only_bind_uses_single_day_presented_availability():
    raw_cache = _multi_day_raw_slots()
    presented = build_presented_availability(raw_cache, search_date="2026-07-08")
    session = {
        "last_execution_result": {
            "type": "availability",
            "status": "success",
            "search_date": "2026-07-08",
            "slots": raw_cache,
        },
        "presented_availability": presented,
    }
    result = try_bind_offered_time_selection(
        {"service_id": "premium haircut"},
        session,
        time_proposal={"mode": "exact", "value": "16:30"},
    )
    assert result is not None
    assert result["slots"]["date"] == "2026-07-08"
    assert result["slots"]["time"] == "16:30"
    assert len(session["last_execution_result"]["slots"]) == 4
