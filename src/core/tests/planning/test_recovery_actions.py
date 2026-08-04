"""Unit tests for structured recovery actions."""

from core.planning.recovery_actions import (
    BROWSE_NEXT,
    BROWSE_PREVIOUS,
    CHOOSE_ANOTHER_DATE,
    CHOOSE_VISIBLE_OPTION,
    action_types,
    recovery_actions_for_browse_boundary,
    recovery_actions_for_browse_window,
    recovery_actions_for_selection_mismatch,
)
from core.planning.time_resolution import apply_post_bind_time_resolution
from core.rendering.availability_renderer import (
    format_mismatch_recovery_text,
    resolve_time_mismatch_text,
)
from core.workflows.availability.presentation import (
    build_availability_presentation,
    build_presented_availability_page,
)


def test_recovery_actions_for_browse_window_first_middle_last():
    first = action_types(
        recovery_actions_for_browse_window(
            {"has_more_any": True, "has_previous_any": False, "suggested_next": "next"}
        )
    )
    assert first == [BROWSE_NEXT]

    middle = action_types(
        recovery_actions_for_browse_window(
            {
                "has_more_any": True,
                "has_previous_any": True,
                "suggested_next": "next",
                "suggested_previous": "previous",
            }
        )
    )
    assert middle == [BROWSE_NEXT, BROWSE_PREVIOUS]

    last = action_types(
        recovery_actions_for_browse_window(
            {
                "has_more_any": False,
                "has_previous_any": True,
                "suggested_previous": "previous",
            }
        )
    )
    assert last == [BROWSE_PREVIOUS]


def test_recovery_actions_for_selection_mismatch_locations():
    earlier = action_types(
        recovery_actions_for_selection_mismatch(
            mismatch_location="EARLIER_PAGE",
            browse_hints={"has_previous_any": True, "suggested_previous": "previous"},
        )
    )
    assert earlier == [BROWSE_PREVIOUS, CHOOSE_VISIBLE_OPTION]

    later = action_types(
        recovery_actions_for_selection_mismatch(
            mismatch_location="LATER_PAGE",
            browse_hints={"has_more_any": True, "suggested_next": "next"},
        )
    )
    assert later == [BROWSE_NEXT, CHOOSE_VISIBLE_OPTION]

    missing = action_types(
        recovery_actions_for_selection_mismatch(
            mismatch_location="NOT_IN_CACHE",
            browse_hints={"has_more_any": True, "has_previous_any": True},
        )
    )
    assert missing == [CHOOSE_VISIBLE_OPTION, CHOOSE_ANOTHER_DATE]


def test_recovery_actions_for_browse_boundary():
    next_bound = action_types(
        recovery_actions_for_browse_boundary(
            direction="next",
            browse_hints={"has_previous_any": True, "suggested_previous": "previous"},
        )
    )
    assert next_bound == [BROWSE_PREVIOUS, CHOOSE_ANOTHER_DATE]

    prev_bound = action_types(
        recovery_actions_for_browse_boundary(
            direction="previous",
            browse_hints={"has_more_any": True, "suggested_next": "next"},
        )
    )
    assert prev_bound == [BROWSE_NEXT, CHOOSE_ANOTHER_DATE]


def test_presented_availability_includes_recovery_actions():
    raw = [
        {
            "starts_at": f"2026-07-09T{h:02d}:00:00.000Z",
            "ends_at": f"2026-07-09T{h:02d}:30:00.000Z",
        }
        for h in range(9, 18)
    ]
    presented = build_presented_availability_page(
        raw, page_index=0, page_size=6, search_date="2026-07-09"
    )
    assert action_types(presented.get("recovery_actions")) == [BROWSE_NEXT]


def test_apply_post_bind_attaches_recovery_actions():
    raw = [
        {
            "starts_at": f"2026-07-09T{h:02d}:00:00.000Z",
            "ends_at": f"2026-07-09T{h:02d}:30:00.000Z",
        }
        for h in range(9, 18)
    ]

    session = {
        "last_execution_result": {
            "type": "availability",
            "status": "success",
            "search_date": "2026-07-09",
            "slots": raw,
        },
        "presented_availability": build_presented_availability_page(
            raw, page_index=1, page_size=6, search_date="2026-07-09"
        ),
        "availability_presentation": build_availability_presentation(
            raw, page_index=1, page_size=6
        ),
    }
    merged = {
        "slots": {"service_id": "premium haircut"},
        "time_proposal": {"mode": "exact", "value": "9am"},
    }
    apply_post_bind_time_resolution(merged, session)
    actions = action_types(merged["time_resolution"].get("recovery_actions"))
    assert actions == [BROWSE_PREVIOUS, CHOOSE_VISIBLE_OPTION]


def test_renderer_uses_recovery_actions_not_browse_hint_inference():
    """Explicit recovery_actions win over contradictory browse_hints."""
    text = resolve_time_mismatch_text(
        requested_time="09:00",
        mismatch_location="EARLIER_PAGE",
        browse_hints={
            # Would have allowed next if renderer still invented policy.
            "has_more_any": True,
            "has_previous_any": False,
            "suggested_next": "next",
        },
        recovery_actions=[
            {"type": BROWSE_PREVIOUS},
            {"type": CHOOSE_VISIBLE_OPTION},
        ],
    )
    assert "`previous`" in text
    assert "`next`" not in text
    assert format_mismatch_recovery_text(
        [{"type": BROWSE_PREVIOUS}, {"type": CHOOSE_VISIBLE_OPTION}]
    ) in text
