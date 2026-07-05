"""Tests for availability slot summarization and render request building."""

from core.rendering.availability_renderer import (
    build_availability_render_request,
    build_presented_availability,
    summarize_availability_slots,
)


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
