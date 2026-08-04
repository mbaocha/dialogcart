"""Tests for availability render-request construction and wording."""

from core.rendering.availability_renderer import (
    build_availability_browse_status_render_request,
    build_availability_render_request,
)
from core.workflows.availability.presentation import build_presented_availability


def test_build_render_request_includes_availability_facts():
    decision = {
        "intent_name": "CREATE_APPOINTMENT",
        "facts": {"slots": {"service_id": "premium haircut"}},
    }
    execution = {
        "schema_version": 1,
        "status": "succeeded",
        "subject": {"service_name": "premium haircut"},
        "availability": {
            "slots": [
                {
                    "starts_at": "2026-07-02T09:00:00.000Z",
                    "ends_at": "2026-07-02T09:30:00.000Z",
                },
            ],
            "time_resolution": None,
        },
    }
    presented = build_presented_availability(execution["availability"]["slots"])
    req = build_availability_render_request(decision, execution, presented=presented)
    assert req is not None
    assert req.facts["availability"]["service_name"] == "Premium Haircut"
    assert req.facts["availability"]["times"]
    assert not req.facts["availability"].get("empty")
    assert "bullet list" in req.render_instruction.lower()


def test_explicit_availability_without_current_time_defensively_lists_exact_match():
    decision = {
        "plan": {
            "turn_operation": "AVAILABILITY",
            "execution_proposal_context": {
                "current_turn_has_explicit_time": False,
                "confirmation_continuation": False,
            },
        },
    }
    execution = {
        "schema_version": 1,
        "status": "succeeded",
        "subject": {"service_name": "premium haircut"},
        "availability": {
            "slots": [
                {
                    "starts_at": "2026-07-21T09:00:00.000Z",
                    "ends_at": "2026-07-21T09:30:00.000Z",
                },
            ],
            "time_resolution": {
                "outcome": "TIME_MATCH_EXACT",
                "requested_time": "09:00",
                "matched_offer": "2026-07-21T09:00:00.000Z",
            },
        },
    }
    presented = build_presented_availability(execution["availability"]["slots"])
    req = build_availability_render_request(decision, execution, presented=presented)

    assert req is not None
    assert "bullet list" in req.render_instruction.lower()
    assert "confirm" not in req.render_instruction.lower()
    assert req.facts["time_resolution"]["outcome"] == "TIME_MATCH_NOT_APPLICABLE"


def test_successful_empty_search_builds_render_request_with_backend_message():
    """Succeeded SEARCH with zero slots must still produce conversational text."""
    backend_message = "No available slots for the selected date."
    execution = {
        "schema_version": 1,
        "status": "succeeded",
        "subject": {"service_name": "premium haircut"},
        "message": backend_message,
        "availability": {
            "slots": [],
            "search_date": "2026-01-14",
            "time_resolution": None,
            "message": backend_message,
        },
    }
    presented = {
        "search_date": "2026-01-14",
        "slots": [],
        "times": [],
        "more_count": 0,
        "total_unique": 0,
        "browse_hints": {},
    }
    req = build_availability_render_request({}, execution, presented=presented)
    assert req is not None
    assert req.facts["availability"]["empty"] is True
    assert req.facts["availability"]["backend_message"] == backend_message
    assert req.facts["availability"]["date"] == "2026-01-14"
    assert req.facts["availability"]["times"] == []
    assert backend_message in req.render_instruction
    assert "no open slots" in req.render_instruction.lower()
    assert "bullet list" not in req.render_instruction.lower()

    from core.rendering.llm_renderer import _build_user_message

    prompt = _build_user_message(req)
    assert backend_message in prompt
    assert "2026-01-14" in prompt or "Service message" in prompt


def test_successful_empty_search_without_backend_message_still_renders():
    execution = {
        "schema_version": 1,
        "status": "succeeded",
        "subject": {"service_name": "premium haircut"},
        "availability": {"slots": [], "search_date": "2026-01-14", "time_resolution": None},
    }
    presented = {
        "search_date": "2026-01-14",
        "slots": [],
        "times": [],
        "more_count": 0,
        "total_unique": 0,
        "browse_hints": {},
    }
    req = build_availability_render_request({}, execution, presented=presented)
    assert req is not None
    assert req.facts["availability"]["empty"] is True
    assert "backend_message" not in req.facts["availability"]
    assert "2026-01-14" in req.render_instruction
    assert "nothing is available" in req.render_instruction.lower()


def test_time_match_mismatch_empty_alternatives_unchanged():
    execution = {
        "schema_version": 1,
        "status": "succeeded",
        "subject": {"service_name": "premium haircut"},
        "availability": {
            "slots": [],
            "time_resolution": {
                "outcome": "TIME_MATCH_MISMATCH",
                "requested_time": "09:00",
                "alternatives": [],
            },
        },
    }
    presented = {
        "search_date": "2026-01-14",
        "slots": [],
        "times": [],
        "more_count": 0,
        "total_unique": 0,
        "browse_hints": {},
    }
    req = build_availability_render_request({}, execution, presented=presented)
    assert req is not None
    assert "not available" in req.render_instruction.lower()
    assert "no alternative times" in req.render_instruction.lower()
    assert req.facts["availability"].get("empty") is not True


def test_inject_availability_text_populates_result_for_successful_empty_search(monkeypatch):
    """Injection path must set response text for empty succeeded SEARCH."""
    from core.rendering import response_renderer as rr

    monkeypatch.setattr(
        rr,
        "render_llm",
        lambda request: (
            "No available slots were found for January 14. "
            "Would you like to try another day?"
        ),
    )
    result: dict = {
        "_workflow_result": {
            "presented_availability": {
                "search_date": "2026-01-14",
                "slots": [],
                "times": [],
                "more_count": 0,
                "total_unique": 0,
                "browse_hints": {},
            }
        }
    }
    execution = {
        "schema_version": 1,
        "status": "succeeded",
        "subject": {"service_name": "premium haircut"},
        "message": "No available slots for the selected date.",
        "availability": {
            "slots": [],
            "search_date": "2026-01-14",
            "message": "No available slots for the selected date.",
        },
    }
    rr._inject_availability_text(result, {}, execution, session_state={"messages": []})
    text = result.get("text") or ""
    assert text.strip()
    assert "no available" in text.lower() or "january 14" in text.lower()
    assert "no text — try a booking request" not in text.lower()


def test_inject_availability_text_skips_failed_execution(monkeypatch):
    from core.rendering import response_renderer as rr

    called = {"render": False}

    def _should_not_run(_request):
        called["render"] = True
        return "should not appear"

    monkeypatch.setattr(rr, "render_llm", _should_not_run)
    result: dict = {}
    execution = {
        "schema_version": 1,
        "status": "failed",
        "availability": {"slots": []},
        "error": {"code": "upstream", "message": "API returned error 500"},
    }
    rr._inject_availability_text(result, {}, execution, session_state={})
    assert "text" not in result
    assert called["render"] is False


def test_fresh_search_render_drops_prior_availability_history_and_grounds_july_21():
    """July 20 exhaustion history must not reach a July 21 SEARCH render request."""
    decision = {"intent_name": "CREATE_APPOINTMENT"}
    execution = {
        "schema_version": 1,
        "status": "succeeded",
        "subject": {"service_name": "premium haircut"},
        "availability": {
            "slots": [
                {
                    "starts_at": "2026-07-21T10:00:00.000Z",
                    "ends_at": "2026-07-21T10:30:00.000Z",
                },
                {
                    "starts_at": "2026-07-21T11:00:00.000Z",
                    "ends_at": "2026-07-21T11:30:00.000Z",
                },
            ],
            "time_resolution": None,
        },
    }
    presented = {
        "search_date": "2026-07-21",
        "slots": execution["availability"]["slots"],
        "times": ["10:00", "11:00"],
        "more_count": 0,
        "total_unique": 2,
        "browse_hints": {},
    }
    history = [
        {"role": "user", "text": "Book me a haircut"},
        {"role": "user", "text": "Premium"},
        {
            "role": "assistant",
            "text": (
                "Here are the available times for July 20:\n"
                "- 10:00\n- 11:00\nWhich would you like?"
            ),
        },
        {"role": "user", "text": "Are there more times for July 20?"},
        {
            "role": "assistant",
            "text": "There are no more available times to show from your last search.",
        },
        {"role": "user", "text": "Show dates for July 21"},
    ]

    req = build_availability_render_request(
        decision,
        execution,
        presented=presented,
        conversation_history=history,
    )
    assert req is not None
    assert req.facts["availability"]["date"] == "2026-07-21"
    assert req.facts["availability"]["times"] == ["10:00", "11:00"]
    assert "2026-07-21" in req.render_instruction
    assert "authoritative date" in req.render_instruction.lower()

    hist_roles = [
        (m.get("role"), str(m.get("text") or "").lower())
        for m in req.conversation_history
    ]
    assert all(
        role != "assistant"
        or (
            "july 20" not in text
            and "no more available times" not in text
            and "available times for" not in text
        )
        for role, text in hist_roles
    )
    assert any(
        role == "user" and "july 21" in text for role, text in hist_roles
    )


def test_browse_status_render_keeps_history_for_same_date_continuation():
    """Genuine browse exhaustion rendering retains conversation history."""
    history = [
        {"role": "user", "text": "Are there more times for July 20?"},
        {
            "role": "assistant",
            "text": "Here are the available times for July 20:\n- 10:00",
        },
    ]
    req = build_availability_browse_status_render_request(
        {"facts": {"slots": {"service_id": "premium haircut"}}},
        direction="next",
        browse_status="exhausted",
        browse_hints={},
        search_date="2026-07-20",
        conversation_history=history,
    )
    assert len(req.conversation_history) == 2
    assert "july 20" in req.conversation_history[1]["text"].lower()
    instruction = req.render_instruction.lower()
    assert "nothing further" in instruction or "no more times" in instruction
    assert "next day" not in instruction


def test_resolve_browse_status_text_scoped_to_search_date():
    from core.rendering.availability_renderer import resolve_browse_status_text

    text = resolve_browse_status_text(
        browse_status="exhausted",
        direction="next",
        search_date="2026-07-24",
        browse_hints={
            "has_previous_any": True,
            "has_more_any": False,
            "suggested_previous": "previous",
            "suggested_next": None,
        },
    )
    assert "no more times for july 24" in text.lower()
    assert "previous" in text.lower()
    assert "another date" in text.lower()
    assert "`next`" not in text.lower()
    assert "next day" not in text.lower()


def test_browse_guidance_clause_first_middle_last_pages():
    from core.rendering.availability_renderer import (
        _browse_guidance_clause,
        browse_navigation_hint_text,
    )

    first = browse_navigation_hint_text(
        {
            "has_more_any": True,
            "has_previous_any": False,
            "suggested_next": "next",
            "suggested_previous": None,
        }
    )
    assert first is not None
    assert "`next`" in first
    assert "previous" not in first.lower()
    assert "`next`" in _browse_guidance_clause(
        {
            "has_more_any": True,
            "has_previous_any": False,
            "suggested_next": "next",
            "suggested_previous": None,
        }
    )

    middle = browse_navigation_hint_text(
        {
            "has_more_any": True,
            "has_previous_any": True,
            "suggested_next": "next",
            "suggested_previous": "previous",
        }
    )
    assert middle is not None
    assert "`next`" in middle and "`previous`" in middle

    last = browse_navigation_hint_text(
        {
            "has_more_any": False,
            "has_previous_any": True,
            "suggested_next": None,
            "suggested_previous": "previous",
        }
    )
    assert last is not None
    assert "`previous`" in last
    assert "`next`" not in last


def test_resolve_browse_status_text_previous_boundary_advertises_next():
    from core.rendering.availability_renderer import resolve_browse_status_text

    text = resolve_browse_status_text(
        browse_status="exhausted",
        direction="previous",
        search_date="2026-07-24",
        browse_hints={
            "has_previous_any": False,
            "has_more_any": True,
            "suggested_previous": None,
            "suggested_next": "next",
        },
    )
    assert "earlier" in text.lower()
    assert "`next`" in text.lower()
    assert "`previous`" not in text.lower()
    assert "another date" in text.lower()


def test_resolve_browse_status_text_previous_boundary_without_next():
    from core.rendering.availability_renderer import resolve_browse_status_text

    text = resolve_browse_status_text(
        browse_status="exhausted",
        direction="previous",
        search_date="2026-07-24",
        browse_hints={
            "has_previous_any": False,
            "has_more_any": False,
            "suggested_previous": None,
            "suggested_next": None,
        },
    )
    assert "earlier" in text.lower()
    assert "`next`" not in text.lower()
    assert "`previous`" not in text.lower()
    assert "another date" in text.lower()


def test_resolve_time_mismatch_text_earlier_page_advertises_previous():
    from core.rendering.availability_renderer import resolve_time_mismatch_text

    text = resolve_time_mismatch_text(
        requested_time="09:00",
        mismatch_location="EARLIER_PAGE",
        recovery_actions=[
            {"type": "browse_previous"},
            {"type": "choose_visible_option"},
        ],
    )
    assert "earlier page" in text.lower()
    assert "`previous`" in text
    assert "`next`" not in text
    assert "currently shown" in text.lower() or "times above" in text.lower()


def test_resolve_time_mismatch_text_later_page_advertises_next():
    from core.rendering.availability_renderer import resolve_time_mismatch_text

    text = resolve_time_mismatch_text(
        requested_time="17:00",
        mismatch_location="LATER_PAGE",
        recovery_actions=[
            {"type": "browse_next"},
            {"type": "choose_visible_option"},
        ],
    )
    assert "later page" in text.lower()
    assert "`next`" in text
    assert "`previous`" not in text


def test_resolve_time_mismatch_text_not_in_cache_no_page_claim():
    from core.rendering.availability_renderer import resolve_time_mismatch_text

    text = resolve_time_mismatch_text(
        requested_time="20:00",
        mismatch_location="NOT_IN_CACHE",
        search_date="2026-07-28",
        recovery_actions=[
            {"type": "choose_visible_option"},
            {"type": "choose_another_date"},
        ],
    )
    assert "isn't available for july 28" in text.lower()
    assert "another date" in text.lower()
    assert "earlier page" not in text.lower()
    assert "later page" not in text.lower()
    assert "`next`" not in text
    assert "`previous`" not in text


def test_fresh_search_drops_stale_confirmation_and_time_selection_history():
    """Superseded July 22 confirmation must not ground a July 23 SEARCH render."""
    decision = {"intent_name": "CREATE_APPOINTMENT"}
    execution = {
        "schema_version": 1,
        "status": "succeeded",
        "subject": {"service_name": "premium haircut"},
        "availability": {
            "slots": [
                {
                    "starts_at": "2026-07-23T09:00:00.000Z",
                    "ends_at": "2026-07-23T09:30:00.000Z",
                },
                {
                    "starts_at": "2026-07-23T10:00:00.000Z",
                    "ends_at": "2026-07-23T10:30:00.000Z",
                },
            ],
            "time_resolution": {"outcome": "TIME_MATCH_NOT_APPLICABLE"},
        },
    }
    presented = {
        "search_date": "2026-07-23",
        "slots": execution["availability"]["slots"],
        "times": ["09:00", "10:00"],
        "more_count": 0,
        "total_unique": 2,
        "browse_hints": {"suggested_next": "next"},
    }
    history = [
        {"role": "user", "text": "Book me a haircut"},
        {"role": "user", "text": "Premium"},
        {
            "role": "assistant",
            "text": (
                "Here are the available times for July 22:\n"
                "- 09:00\n- 14:00\nWhich would you like?"
            ),
        },
        {"role": "user", "text": "show more"},
        {
            "role": "assistant",
            "text": (
                "Here are more available times for July 22:\n"
                "- 15:00\n- 16:00\nWhich would you like?"
            ),
        },
        {"role": "user", "text": "book me for 2pm"},
        {
            "role": "assistant",
            "text": (
                "You're about to book a Premium Haircut on July 22 at 2:00 PM. "
                "Would you like me to go ahead?"
            ),
        },
        {"role": "user", "text": "show availability for July 23rd"},
    ]

    req = build_availability_render_request(
        decision,
        execution,
        presented=presented,
        conversation_history=history,
    )
    assert req is not None
    assert req.facts["availability"]["date"] == "2026-07-23"
    assert req.facts["time_resolution"]["outcome"] == "TIME_MATCH_NOT_APPLICABLE"

    hist_text = "\n".join(
        f"{m.get('role')}: {m.get('text')}" for m in req.conversation_history
    ).lower()
    assert "you're about to book" not in hist_text
    assert "go ahead" not in hist_text
    assert "book me for 2pm" not in hist_text
    assert "2:00 pm" not in hist_text
    assert "available times for july 22" not in hist_text
    assert "premium" in hist_text or "haircut" in hist_text
    assert any(
        m.get("role") == "user" and "july 23" in str(m.get("text") or "").lower()
        for m in req.conversation_history
    )

    # Facts-grounded render (same contract as E2E fake LLM) must not echo stale booking.
    avail = req.facts["availability"]
    date_phrase = "July 23"
    times = avail.get("times") or []
    lines = "\n".join(f"- {t}" for t in times)
    grounded = (
        f"Here are available times for {date_phrase}:\n"
        f"{lines}\nWhich would you like?"
    )
    assert "july 23" in grounded.lower()
    assert "july 22" not in grounded.lower()
    assert "2:00" not in grounded
    assert "2pm" not in grounded.lower()
    assert "don't have" not in grounded.lower()
    assert "unable" not in grounded.lower()
    # Sanitized history must not reintroduce confirmation into the LLM user message.
    from core.rendering.llm_renderer import _build_user_message

    prompt = _build_user_message(req).lower()
    assert "you're about to book" not in prompt
    assert "go ahead" not in prompt
    assert "book me for 2pm" not in prompt
    assert avail["date"] in prompt or "2026-07-23" in prompt


def test_browse_continuation_not_treated_as_fresh_search_history_reset():
    """Browse-status path keeps active July 22 context (not a SEARCH reset)."""
    history = [
        {
            "role": "assistant",
            "text": "Here are the available times for July 22:\n- 09:00\n- 10:00",
        },
        {"role": "user", "text": "show more"},
        {
            "role": "assistant",
            "text": (
                "You're about to book a Premium Haircut on July 22 at 2:00 PM. "
                "Would you like me to go ahead?"
            ),
        },
    ]
    # Confirmation should not appear mid-browse normally; still, browse-status
    # must not apply fresh-search filtering when continuing the same search.
    req = build_availability_browse_status_render_request(
        {"facts": {"slots": {"service_id": "premium haircut"}}},
        direction="next",
        browse_status="exhausted",
        browse_hints={"suggested_next": "next"},
        search_date="2026-07-22",
        conversation_history=history,
    )
    assert len(req.conversation_history) == 3
    assert "july 22" in req.conversation_history[0]["text"].lower()
    assert "go ahead" in req.conversation_history[2]["text"].lower()
    assert "next day" not in req.render_instruction.lower()


def test_inject_rendering_text_mismatch_without_organization_id(monkeypatch):
    """Unavailable-time clarification must render with no org_id on session/slots."""
    from core.planning.time_resolution import TIME_MATCH_MISMATCH
    from core.rendering import response_renderer as rr

    monkeypatch.setattr(
        rr,
        "render_llm",
        lambda request: (
            "5:00 PM isn’t available. Available times: 1:30 PM and 2:00 PM. "
            "Which works for you?"
        ),
    )

    session = {
        "intent_name": "CREATE_APPOINTMENT",
        "slots": {"service_id": "premium haircut"},
        "missing_slots": [],
        "last_execution_result": {
            "type": "availability",
            "status": "success",
            "slots": [
                {
                    "starts_at": "2026-07-22T13:30:00.000Z",
                    "ends_at": "2026-07-22T14:00:00.000Z",
                },
                {
                    "starts_at": "2026-07-22T14:00:00.000Z",
                    "ends_at": "2026-07-22T14:30:00.000Z",
                },
            ],
            "search_date": "2026-07-22",
        },
        "presented_availability": {
            "search_date": "2026-07-22",
            "slots": [
                {
                    "starts_at": "2026-07-22T13:30:00.000Z",
                    "ends_at": "2026-07-22T14:00:00.000Z",
                },
                {
                    "starts_at": "2026-07-22T14:00:00.000Z",
                    "ends_at": "2026-07-22T14:30:00.000Z",
                },
            ],
            "times": ["1:30 PM", "2:00 PM"],
            "more_count": 0,
            "total_unique": 2,
        },
        "messages": [],
    }
    assert "organization_id" not in session
    assert "organization_id" not in session["slots"]

    decision = {
        "intent_name": "CREATE_APPOINTMENT",
        "time_match_outcome": TIME_MATCH_MISMATCH,
        "awaiting": "TIME_SELECTION",
        "plan": {
            "status": "NEEDS_CLARIFICATION",
            "awaiting": "TIME_SELECTION",
            "time_match_outcome": TIME_MATCH_MISMATCH,
            "action": None,
        },
        "facts": {
            "missing_slots": [],
            "slots": {"service_id": "premium haircut"},
            "time_match_outcome": TIME_MATCH_MISMATCH,
            "time_resolution": {
                "outcome": TIME_MATCH_MISMATCH,
                "requested_time": "17:00",
                "alternatives": [
                    "2026-07-22T13:30:00.000Z",
                    "2026-07-22T14:00:00.000Z",
                ],
            },
        },
        "time_resolution": {
            "outcome": TIME_MATCH_MISMATCH,
            "requested_time": "17:00",
            "alternatives": [
                "2026-07-22T13:30:00.000Z",
                "2026-07-22T14:00:00.000Z",
            ],
        },
    }
    result: dict = {"outcome": {"status": "NEEDS_CLARIFICATION", "missing_slots": []}}
    rr._inject_rendering_text(result, decision, session_state=session)

    text = result.get("text") or ""
    assert text.strip(), "mismatch clarification must not produce empty text"
    assert "available" in text.lower() or "1:30" in text or "2:00" in text
    assert "organization_id" not in session["slots"]
    # Booking slots unchanged by rendering
    assert session["slots"] == {"service_id": "premium haircut"}


def test_inject_rendering_text_mismatch_fallback_when_llm_empty(monkeypatch):
    """Mismatch path must use deterministic fallback instead of missing-slots silence."""
    from core.planning.time_resolution import TIME_MATCH_MISMATCH
    from core.rendering import response_renderer as rr

    monkeypatch.setattr(rr, "render_llm", lambda _request: None)

    session = {
        "slots": {"service_id": "premium haircut"},
        "last_execution_result": {
            "type": "availability",
            "status": "success",
            "slots": [
                {
                    "starts_at": "2026-07-22T13:30:00.000Z",
                    "ends_at": "2026-07-22T14:00:00.000Z",
                },
            ],
            "search_date": "2026-07-22",
        },
        "presented_availability": {
            "search_date": "2026-07-22",
            "slots": [
                {
                    "starts_at": "2026-07-22T13:30:00.000Z",
                    "ends_at": "2026-07-22T14:00:00.000Z",
                },
            ],
            "times": ["1:30 PM"],
            "more_count": 0,
            "total_unique": 1,
        },
        "messages": [],
    }
    decision = {
        "time_match_outcome": TIME_MATCH_MISMATCH,
        "awaiting": "TIME_SELECTION",
        "plan": {"awaiting": "TIME_SELECTION", "time_match_outcome": TIME_MATCH_MISMATCH},
        "facts": {"missing_slots": []},
        "time_resolution": {
            "outcome": TIME_MATCH_MISMATCH,
            "requested_time": "17:00",
            "alternatives": ["2026-07-22T13:30:00.000Z"],
        },
    }
    result: dict = {}
    rr._inject_rendering_text(result, decision, session_state=session)
    text = result.get("text") or ""
    assert text.strip()
    assert "isn't available" in text
    assert "1:30" in text
