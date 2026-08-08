"""Deterministic business-hours normalization for Business Knowledge rendering."""

from __future__ import annotations

from core.rendering.business_hours import (
    normalize_business_hours,
    prepare_structured_context_for_render,
)
from core.rendering.llm_renderer import LlmRenderRequest, _build_user_message


def _commerce_week(
    open_days: set[int],
    *,
    start: str = "09:00",
    end: str = "17:00",
    open_overrides: dict[int, tuple[str, str]] | None = None,
) -> list[dict]:
    """Build Commerce dayOfWeek array (0=Sunday)."""
    overrides = open_overrides or {}
    rows = []
    for day in range(7):
        is_open = day in open_days
        day_start, day_end = overrides.get(day, (start, end))
        rows.append(
            {
                "dayOfWeek": day,
                "isOpen": is_open,
                "startTime": day_start,
                "endTime": day_end,
            }
        )
    return rows


def _named_week(open_days: set[str], *, start: str = "09:00", end: str = "17:00") -> dict:
    """Build monday/tuesday keyed schedule matching the ambiguous payload shape."""
    days = (
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
    )
    return {
        day: {
            "isOpen": day in open_days,
            "start": start,
            "end": end,
        }
        for day in days
    }


def test_mon_fri_opening_summary_never_includes_saturday():
    raw = _commerce_week({1, 2, 3, 4, 5})
    normalized = normalize_business_hours(raw)
    assert normalized is not None
    assert normalized["open_days"] == [
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
    ]
    assert normalized["closed_days"] == ["Saturday", "Sunday"]
    assert normalized["hours"] == "9:00 AM–5:00 PM"
    summary = normalized["opening_summary"]
    assert "Monday through Friday" in summary
    assert "Closed Saturday and Sunday" in summary
    assert "Monday through Saturday" not in summary


def test_named_dict_mon_fri_same_as_commerce_array():
    raw = _named_week({"monday", "tuesday", "wednesday", "thursday", "friday"})
    normalized = normalize_business_hours(raw)
    assert normalized is not None
    assert "Monday through Friday" in normalized["opening_summary"]
    assert "Monday through Saturday" not in normalized["opening_summary"]
    assert normalized["closed_days"] == ["Saturday", "Sunday"]


def test_mon_sat_business():
    raw = _commerce_week({1, 2, 3, 4, 5, 6})
    normalized = normalize_business_hours(raw)
    assert normalized is not None
    assert "Monday through Saturday" in normalized["opening_summary"]
    assert normalized["closed_days"] == ["Sunday"]
    assert "Friday" in normalized["open_days"]
    assert "Saturday" in normalized["open_days"]
    assert "Sunday" not in normalized["open_days"]


def test_seven_day_business():
    raw = _commerce_week({0, 1, 2, 3, 4, 5, 6})
    normalized = normalize_business_hours(raw)
    assert normalized is not None
    assert normalized["closed_days"] == []
    assert len(normalized["open_days"]) == 7
    assert normalized["opening_summary"] == "Open every day, 9:00 AM–5:00 PM."


def test_split_schedules():
    # Mon–Fri 9–5, Saturday 9–1, Sunday closed
    raw = _commerce_week(
        {1, 2, 3, 4, 5, 6},
        open_overrides={6: ("09:00", "13:00")},
    )
    normalized = normalize_business_hours(raw)
    assert normalized is not None
    assert "schedule" in normalized
    assert normalized["closed_days"] == ["Sunday"]
    summary = normalized["opening_summary"]
    assert "Monday through Friday" in summary
    assert "9:00 AM–5:00 PM" in summary
    assert "Saturday" in summary
    assert "1:00 PM" in summary
    assert "Closed Sunday" in summary


def test_entirely_closed_business():
    raw = _commerce_week(set())
    normalized = normalize_business_hours(raw)
    assert normalized is not None
    assert normalized["open_days"] == []
    assert len(normalized["closed_days"]) == 7
    assert normalized["opening_summary"] == "Closed every day."


def test_prepare_strips_raw_is_open_from_prompt_view():
    original = {
        "business_name": "Any Garage",
        "hours": _commerce_week({1, 2, 3, 4, 5}),
    }
    prepared = prepare_structured_context_for_render(original)
    assert "hours" not in prepared
    assert "isOpen" not in str(prepared)
    assert prepared["opening_hours"]["opening_summary"].startswith(
        "Open Monday through Friday"
    )
    # Original facts untouched for booking / other consumers
    assert "hours" in original
    assert isinstance(original["hours"], list)
    assert original["hours"][1]["isOpen"] is True


def test_prepare_leaves_simple_string_hours_untouched():
    original = {"hours": {"mon": "9am-6pm"}}
    prepared = prepare_structured_context_for_render(original)
    assert prepared["hours"] == {"mon": "9am-6pm"}
    assert "opening_hours" not in prepared


def test_renderer_prompt_uses_normalized_hours_not_raw_is_open():
    request = LlmRenderRequest(
        render_instruction="Answer the user.",
        user_request="What are your opening hours?",
        facts={
            "structured_context": {
                "business_name": "Any Garage",
                "hours": _commerce_week({1, 2, 3, 4, 5}),
            },
            "chunks": [],
        },
    )
    message = _build_user_message(request)
    assert "Monday through Friday" in message
    assert "Closed Saturday and Sunday" in message
    assert "Monday through Saturday" not in message
    assert '"isOpen"' not in message
    assert "opening_hours" in message
    # Raw facts still available outside the prompt builder
    assert request.facts["structured_context"]["hours"][1]["isOpen"] is True


def test_renderer_prompt_mon_sat_and_seven_day():
    mon_sat = LlmRenderRequest(
        render_instruction="Answer.",
        facts={"structured_context": {"hours": _commerce_week({1, 2, 3, 4, 5, 6})}},
    )
    mon_sat_msg = _build_user_message(mon_sat)
    assert "Monday through Saturday" in mon_sat_msg
    assert "Closed Sunday" in mon_sat_msg

    every_day = LlmRenderRequest(
        render_instruction="Answer.",
        facts={
            "structured_context": {
                "hours": _commerce_week({0, 1, 2, 3, 4, 5, 6}),
            }
        },
    )
    every_msg = _build_user_message(every_day)
    assert "Open every day" in every_msg
    assert "Closed" not in every_msg.split("opening_summary")[1].split("\n")[0]


def test_renderer_prompt_split_and_closed():
    split = LlmRenderRequest(
        render_instruction="Answer.",
        facts={
            "structured_context": {
                "business_hours": _commerce_week(
                    {1, 2, 3, 4, 5, 6},
                    open_overrides={6: ("09:00", "13:00")},
                )
            }
        },
    )
    split_msg = _build_user_message(split)
    assert "Monday through Friday" in split_msg
    assert "Saturday" in split_msg
    assert "1:00 PM" in split_msg
    assert '"isOpen"' not in split_msg

    closed = LlmRenderRequest(
        render_instruction="Answer.",
        facts={"structured_context": {"hours": _commerce_week(set())}},
    )
    closed_msg = _build_user_message(closed)
    assert "Closed every day" in closed_msg


def test_presentation_guidance_requires_exact_opening_hours():
    request = LlmRenderRequest(
        render_instruction="Answer.",
        facts={"structured_context": {"hours": _commerce_week({1, 2, 3, 4, 5})}},
    )
    message = _build_user_message(request)
    assert "opening_summary" in message.lower() or "open_days" in message.lower()
    assert "do not invent or extend open days" in message.lower()
