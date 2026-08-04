"""Build LLM render requests for SEARCH_AVAILABILITY results.

Owns wording only. Callers must supply an already-prepared PresentedAvailability.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.rendering.llm_renderer import LlmRenderRequest
from core.workflows.availability.contracts import PresentedAvailability
from core.workflows.availability.presentation import format_display_time

try:
    from core.planning.time_resolution import (
        TIME_MATCH_EXACT,
        TIME_MATCH_MISMATCH,
        TIME_MATCH_NOT_APPLICABLE,
    )
except ImportError:
    TIME_MATCH_EXACT = "TIME_MATCH_EXACT"
    TIME_MATCH_MISMATCH = "TIME_MATCH_MISMATCH"
    TIME_MATCH_NOT_APPLICABLE = "TIME_MATCH_NOT_APPLICABLE"


def _service_name_from_execution(execution_result: Dict[str, Any]) -> str:
    subject = execution_result.get("subject")
    if not isinstance(subject, dict):
        return "your appointment"
    service_name = subject.get("service_name")
    if service_name:
        text = str(service_name)
        return text.title() if text.islower() else text
    return "your appointment"


def _format_alternative_labels(alternatives: List[str]) -> List[str]:
    return [format_display_time(iso) for iso in alternatives if iso]


def _explicit_availability_requires_list_rendering(
    decision: Optional[Dict[str, Any]],
) -> bool:
    """Defend browse/list UX from stale exact-match execution artifacts."""
    if not isinstance(decision, dict):
        return False
    plan = decision.get("plan")
    if not isinstance(plan, dict):
        plan = decision
    if plan.get("turn_operation") not in ("AVAILABILITY", "CHECK_AVAILABILITY"):
        return False
    context = plan.get("execution_proposal_context")
    if not isinstance(context, dict):
        return False
    return (
        not context.get("current_turn_has_explicit_time", False)
        and not context.get("confirmation_continuation", False)
    )



def _recovery_action_types(recovery_actions: Optional[Any]) -> List[str]:
    from core.planning.recovery_actions import action_types

    return action_types(recovery_actions if isinstance(recovery_actions, list) else None)


def format_browse_window_recovery_text(
    recovery_actions: Optional[Any],
) -> Optional[str]:
    """User-facing browse-window navigation sentence from structured actions."""
    from core.planning.recovery_actions import BROWSE_NEXT, BROWSE_PREVIOUS

    types = set(_recovery_action_types(recovery_actions))
    has_next = BROWSE_NEXT in types
    has_previous = BROWSE_PREVIOUS in types
    if has_next and has_previous:
        return (
            "You can reply with `next` for later times or `previous` to go back."
        )
    if has_next:
        return "You can also reply with `next` to see more times."
    if has_previous:
        return "You can also reply with `previous` to go back."
    return None


def browse_navigation_hint_text(
    browse_hints: Optional[Dict[str, Any]],
    *,
    recovery_actions: Optional[Any] = None,
) -> Optional[str]:
    """User-facing navigation sentence for the current browse window, or None.

    Prefer structured ``recovery_actions`` when provided; otherwise derive from
    browse_hints for legacy callers.
    """
    if recovery_actions is not None:
        return format_browse_window_recovery_text(recovery_actions)
    from core.planning.recovery_actions import recovery_actions_for_browse_window

    return format_browse_window_recovery_text(
        recovery_actions_for_browse_window(browse_hints)
    )


def _browse_guidance_clause(
    browse_hints: Optional[Dict[str, Any]],
    *,
    recovery_actions: Optional[Any] = None,
) -> str:
    """Advertise only valid page-navigation directions for the current window."""
    hint = browse_navigation_hint_text(
        browse_hints, recovery_actions=recovery_actions
    )
    if hint:
        return f" Then tell them: {hint}"
    return " Do not mention next or previous page navigation."


def format_recovery_actions_llm_clause(
    recovery_actions: Optional[Any],
    *,
    context: str = "mismatch",
) -> str:
    """LLM instruction fragment for structured recovery actions."""
    from core.planning.recovery_actions import (
        BROWSE_NEXT,
        BROWSE_PREVIOUS,
        CHOOSE_ANOTHER_DATE,
        CHOOSE_VISIBLE_OPTION,
    )

    types = _recovery_action_types(recovery_actions)
    if not types:
        return ""

    has_next = BROWSE_NEXT in types
    has_previous = BROWSE_PREVIOUS in types
    has_visible = CHOOSE_VISIBLE_OPTION in types
    has_date = CHOOSE_ANOTHER_DATE in types

    if context == "browse_boundary_previous":
        if has_next and has_date:
            return (
                " Tell them they can reply with `next` to return to the later "
                "times, or ask for another date. Do not advertise `previous`."
            )
        if has_date:
            return (
                " Ask them to ask for another date. Do not advertise `previous`."
            )
        return ""

    if context == "browse_boundary_next":
        if has_previous and has_date:
            return (
                " Tell them they can reply with `previous` to go back to the "
                "earlier times, or ask for another date. Do not advertise `next`."
            )
        if has_date:
            return (
                " Ask them to ask for another date. Do not advertise `next`."
            )
        return ""

    # selection mismatch
    if has_previous and has_visible:
        return (
            " Tell them to reply with `previous` to return to those times, "
            "or choose one of the currently shown times. Do not advertise `next` "
            "as the route to the requested time."
        )
    if has_next and has_visible:
        return (
            " Tell them to reply with `next` to return to those times, "
            "or choose one of the currently shown times. Do not advertise `previous` "
            "as the route to the requested time."
        )
    if has_visible and has_date:
        return (
            " Ask them to choose one of the currently shown times or ask for "
            "another date. Do not advertise next/previous as a route to that time."
        )
    if has_visible:
        return " Ask them to choose one of the currently shown times."
    return ""


def format_mismatch_recovery_text(
    recovery_actions: Optional[Any],
) -> Optional[str]:
    """Deterministic recovery sentence for selection-mismatch actions."""
    from core.planning.recovery_actions import (
        BROWSE_NEXT,
        BROWSE_PREVIOUS,
        CHOOSE_ANOTHER_DATE,
        CHOOSE_VISIBLE_OPTION,
    )

    types = _recovery_action_types(recovery_actions)
    if not types:
        return None
    has_next = BROWSE_NEXT in types
    has_previous = BROWSE_PREVIOUS in types
    has_visible = CHOOSE_VISIBLE_OPTION in types
    has_date = CHOOSE_ANOTHER_DATE in types

    if has_previous and has_visible:
        return (
            "Reply `previous` to return to those times, "
            "or choose one of the times above."
        )
    if has_next and has_visible:
        return (
            "Reply `next` to return to those times, "
            "or choose one of the times above."
        )
    if has_visible and has_date:
        return (
            "Please choose one of the times currently shown, "
            "or ask for another date."
        )
    if has_visible:
        return "Please choose one of the times currently shown."
    return None


def format_browse_boundary_recovery_text(
    recovery_actions: Optional[Any],
    *,
    direction: str,
) -> Optional[str]:
    """Deterministic recovery sentence for browse boundary/exhaustion."""
    from core.planning.recovery_actions import (
        BROWSE_NEXT,
        BROWSE_PREVIOUS,
        CHOOSE_ANOTHER_DATE,
    )

    types = _recovery_action_types(recovery_actions)
    axis = str(direction or "next").strip().lower()
    has_next = BROWSE_NEXT in types
    has_previous = BROWSE_PREVIOUS in types
    has_date = CHOOSE_ANOTHER_DATE in types

    if axis == "previous":
        if has_next and has_date:
            return (
                "You can reply with `next` to return to the later "
                "times, or ask for another date."
            )
        if has_date:
            return "Ask for another date."
        return None

    if has_previous and has_date:
        return (
            "You can reply with `previous` to go back to the earlier "
            "times, or ask for another date."
        )
    if has_date:
        return "Ask for another date."
    return None


def _looks_like_availability_assistant_reply(text: str) -> bool:
    """True when an assistant turn is prior availability listing / exhaustion copy."""
    lowered = (text or "").lower()
    if not lowered.strip():
        return False
    markers = (
        "available time",
        "available appointment",
        "no more available",
        "no more times",
        "no additional times",
        "which time",
        "which would you like",
        "here are the available",
    )
    return any(marker in lowered for marker in markers)


def _message_kind(message: Dict[str, Any]) -> str:
    """Return structured message kind when persisted history carries one."""
    for key in ("kind", "message_kind", "render_kind", "type"):
        raw = message.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip().lower()
    return ""


def _looks_like_confirmation_assistant_reply(message: Dict[str, Any]) -> bool:
    """True for pending-confirmation prompts from the booking confirmation renderer.

    Prefer structured message kind when present; otherwise match the narrow
    confirmation wording produced by ``render_booking_confirmation_prompt``.
    """
    kind = _message_kind(message)
    if kind in {
        "confirmation",
        "booking_confirmation",
        "awaiting_confirmation",
        "confirm_prompt",
    }:
        return True

    lowered = str(message.get("text") or "").lower()
    if not lowered.strip():
        return False
    if "you're about to book" in lowered or "you are about to book" in lowered:
        return True
    if "would you like me to go ahead" in lowered:
        return True
    if "go ahead?" in lowered and ("book" in lowered or "appointment" in lowered):
        return True
    return False


def _looks_like_abandoned_time_selection(text: str) -> bool:
    """True when a user turn only selected a clock time for the prior offer window."""
    import re

    lowered = (text or "").strip().lower()
    if not lowered:
        return False

    # Keep search / service / date requests — not abandoned offer picks.
    keep_markers = (
        "availability",
        "available",
        "haircut",
        "service",
        "premium",
        "flexi",
        "january",
        "february",
        "march",
        "april",
        "june",
        "july",
        "august",
        "september",
        "october",
        "november",
        "december",
    )
    if any(marker in lowered for marker in keep_markers):
        return False
    if re.search(r"\b\d{1,2}(?:st|nd|rd|th)\b", lowered):
        return False

    has_clock = bool(
        re.search(
            r"\b\d{1,2}(?::\d{2})?\s*(?:am|pm)\b|\b\d{1,2}:\d{2}\b",
            lowered,
        )
    )
    if not has_clock:
        return False

    selection_verbs = (
        "book me",
        "book for",
        "choose",
        "select",
        "take",
        "i'll take",
        "ill take",
        "for me",
        "that one",
    )
    return any(verb in lowered for verb in selection_verbs) or len(lowered) <= 24


def _history_for_fresh_availability_search(
    conversation_history: Optional[List[Dict[str, str]]],
    *,
    search_date: Optional[str],
) -> List[Dict[str, str]]:
    """Keep user intent context; drop stale search/confirmation dialogue.

    Fresh SEARCH_AVAILABILITY results must not reintroduce:
    - prior availability listing / exhaustion assistant turns
    - superseded pending-confirmation prompts
    - the immediately preceding abandoned time-selection user turn

    Browse-status rendering uses a separate builder and keeps full history for
    genuine continuation. Session messages are never mutated — only the copy
    supplied to ``LlmRenderRequest`` is sanitized.
    """
    history = list(conversation_history or [])
    if not search_date or not history:
        return history

    # Walk chronologically; when dropping a confirmation assistant turn, also
    # drop the immediately preceding user time-selection that produced it.
    filtered: List[Dict[str, str]] = []
    for message in history:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "").strip().lower()
        text = str(message.get("text") or "")

        if role == "assistant" and (
            _looks_like_availability_assistant_reply(text)
            or _looks_like_confirmation_assistant_reply(message)
        ):
            if filtered:
                prev = filtered[-1]
                prev_role = str(prev.get("role") or "").strip().lower()
                prev_text = str(prev.get("text") or "")
                if prev_role == "user" and (
                    _looks_like_abandoned_time_selection(prev_text)
                    or _message_kind(prev)
                    in {"time_selection", "selection", "offer_selection"}
                ):
                    filtered.pop()
            continue

        filtered.append(message)
    return filtered


def _backend_availability_message(execution_result: Dict[str, Any]) -> Optional[str]:
    """Prefer the availability service's own empty/outcome message when present."""
    for key in ("message",):
        top = execution_result.get(key)
        if isinstance(top, str) and top.strip():
            return top.strip()
    availability = execution_result.get("availability")
    if isinstance(availability, dict):
        nested = availability.get("message")
        if isinstance(nested, str) and nested.strip():
            return nested.strip()
    return None


def build_availability_render_request(
    decision: Optional[Dict[str, Any]],
    execution_result: Dict[str, Any],
    *,
    presented: PresentedAvailability,
    structured_context: Optional[Dict[str, Any]] = None,
    conversation_history: Optional[List[Dict[str, str]]] = None,
    time_resolution: Optional[Dict[str, Any]] = None,
    max_times: int = 6,
) -> Optional[LlmRenderRequest]:
    """Build render request from prepared PresentedAvailability (wording only)."""
    if not isinstance(presented, dict):
        return None

    availability = execution_result.get("availability")
    resolution = time_resolution
    if resolution is None and isinstance(availability, dict):
        resolution = availability.get("time_resolution")
    if not isinstance(resolution, dict):
        resolution = {"outcome": TIME_MATCH_NOT_APPLICABLE}

    times = list(presented.get("times") or [])
    more_count = int(presented.get("more_count") or 0)
    total_unique = int(
        presented.get("total_unique")
        or (len(presented.get("slots") or []) + more_count)
    )
    service_name = _service_name_from_execution(execution_result)
    browse_hints = presented.get("browse_hints")
    browse_hints = browse_hints if isinstance(browse_hints, dict) else {}
    recovery_actions = presented.get("recovery_actions")
    if not isinstance(recovery_actions, list):
        from core.planning.recovery_actions import recovery_actions_for_browse_window

        recovery_actions = recovery_actions_for_browse_window(browse_hints)
    search_date = presented.get("search_date")
    if not search_date and isinstance(availability, dict):
        search_date = availability.get("search_date")

    outcome = resolution.get("outcome") or resolution.get("status")
    if outcome in ("exact_match",):
        outcome = TIME_MATCH_EXACT
    elif outcome in ("no_match",):
        outcome = TIME_MATCH_MISMATCH
    if (
        outcome == TIME_MATCH_EXACT
        and _explicit_availability_requires_list_rendering(decision)
    ):
        resolution = {**resolution, "outcome": TIME_MATCH_NOT_APPLICABLE}
        outcome = TIME_MATCH_NOT_APPLICABLE

    availability_facts: Dict[str, Any] = {
        "service_name": service_name,
        "date": search_date,
        "times": times,
        "more_count": more_count,
        "browse_hints": browse_hints,
        "recovery_actions": recovery_actions,
    }

    if total_unique == 0 and outcome != TIME_MATCH_MISMATCH:
        backend_message = _backend_availability_message(execution_result)
        availability_facts["empty"] = True
        availability_facts["times"] = []
        availability_facts["more_count"] = 0
        if backend_message:
            availability_facts["backend_message"] = backend_message
        date_clause = f" for {search_date}" if search_date else ""
        if backend_message:
            render_instruction = (
                f"The user is booking {service_name}. "
                f"Availability search succeeded with no open slots{date_clause}. "
                f"Authoritative outcome from the availability service: "
                f'"{backend_message}". '
                "Rewrite that outcome as a brief natural reply (1–2 sentences) and "
                "invite them to try another day. "
                "Do not invent times or claim any slots are available."
            )
        else:
            render_instruction = (
                f"The user is booking {service_name}. "
                f"Availability search succeeded with no open slots{date_clause}. "
                "Tell them clearly that nothing is available"
                + (f" on {search_date}" if search_date else "")
                + ". Invite them to try another day. "
                "Do not invent times. Keep the reply to 1–2 sentences."
            )
        return LlmRenderRequest(
            render_instruction=render_instruction,
            facts={
                "structured_context": structured_context or {},
                "availability": availability_facts,
                "time_resolution": resolution,
            },
            conversation_history=_history_for_fresh_availability_search(
                conversation_history,
                search_date=str(search_date) if search_date else None,
            ),
        )

    if outcome == TIME_MATCH_EXACT:
        requested = resolution.get("requested_time")
        matched = resolution.get("matched_offer")
        availability_facts["matched_time"] = (
            format_display_time(str(matched)) if matched else requested
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
        mismatch_location = str(resolution.get("mismatch_location") or "").strip()
        mismatch_recovery = resolution.get("recovery_actions")
        if not isinstance(mismatch_recovery, list):
            from core.planning.recovery_actions import (
                recovery_actions_for_selection_mismatch,
            )

            mismatch_recovery = recovery_actions_for_selection_mismatch(
                mismatch_location=mismatch_location,
                browse_hints=browse_hints,
            )
        availability_facts["recovery_actions"] = mismatch_recovery
        recovery_clause = format_recovery_actions_llm_clause(
            mismatch_recovery, context="mismatch"
        )
        if mismatch_location == "EARLIER_PAGE":
            render_instruction = (
                f"The user is booking {service_name}. "
                f"Their requested time ({requested}) is not on the currently shown page; "
                "it was on an earlier page."
                + (recovery_clause or " Ask them to choose one of the currently shown times.")
                + " Do not claim the time is currently selectable. "
                "Do not bind or invent availability. Keep the reply to 1–2 sentences."
            )
        elif mismatch_location == "LATER_PAGE":
            render_instruction = (
                f"The user is booking {service_name}. "
                f"Their requested time ({requested}) is not on the currently shown page; "
                "it was on a later page."
                + (recovery_clause or " Ask them to choose one of the currently shown times.")
                + " Do not claim the time is currently selectable. "
                "Do not bind or invent availability. Keep the reply to 1–2 sentences."
            )
        elif mismatch_location == "NOT_IN_CACHE":
            date_clause = f" for {search_date}" if search_date else ""
            render_instruction = (
                f"The user is booking {service_name}. "
                f"Their requested time ({requested}) is not available{date_clause}. "
                "Tell them clearly it is unavailable."
                + (
                    recovery_clause
                    or (
                        " Ask them to choose one of the currently shown times or ask for "
                        "another date. Do not advertise next/previous as a route to that time."
                    )
                )
                + " Do not claim it was shown on an earlier or later page. "
                "Do not invent availability. Keep the reply to 1–2 sentences."
            )
        elif alt_labels:
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
        guidance = _browse_guidance_clause(
            browse_hints, recovery_actions=recovery_actions
        )
        date_clause = f" for {search_date}" if search_date else ""
        date_authority = ""
        if search_date:
            date_authority = (
                f" The authoritative date for this reply is {search_date} from the "
                "Availability facts below. Do not mention any other dates from earlier "
                "conversation turns (including exhausted prior searches)."
            )
        render_instruction = (
            f"The user is booking {service_name}. "
            f"Present the available appointment times{date_clause} listed below "
            "in a short bullet list under a heading like "
            f"\"Available times{date_clause}:\". "
            "Ask which time they would like. "
            f"{guidance}"
            f"{date_authority}"
            " Do not teach date-axis commands such as \"next day\" or \"previous day\". "
            " Keep the reply to 2–3 sentences plus the list. "
            "Do not invent times or mention staff names. "
            "Do not invent browse directions beyond the navigation sentence above."
        )

    facts: Dict[str, Any] = {
        "structured_context": structured_context or {},
        "availability": availability_facts,
        "time_resolution": resolution,
    }

    return LlmRenderRequest(
        render_instruction=render_instruction,
        facts=facts,
        conversation_history=_history_for_fresh_availability_search(
            conversation_history,
            search_date=str(search_date) if search_date else None,
        ),
    )


def build_availability_browse_status_render_request(
    decision: Optional[Dict[str, Any]],
    *,
    direction: str,
    browse_status: str,
    browse_hints: Optional[Dict[str, Any]] = None,
    search_date: Optional[str] = None,
    structured_context: Optional[Dict[str, Any]] = None,
    conversation_history: Optional[List[Dict[str, str]]] = None,
) -> LlmRenderRequest:
    """Build render request for browse exhaustion (prepared facts only)."""
    service_name = "your appointment"
    if isinstance(decision, dict):
        facts = decision.get("facts")
        facts = facts if isinstance(facts, dict) else {}
        slots = facts.get("slots")
        slots = slots if isinstance(slots, dict) else {}
        service_id = slots.get("service_id") or facts.get("service_id")
        if service_id:
            text = str(service_id)
            service_name = text.title() if text.islower() else text

    hints = browse_hints if isinstance(browse_hints, dict) else {}
    date_label = search_date or hints.get("search_date")
    date_clause = f" for {date_label}" if date_label else ""
    from core.planning.recovery_actions import recovery_actions_for_browse_boundary

    recovery = recovery_actions_for_browse_boundary(
        direction=direction, browse_hints=hints
    )
    if direction == "previous":
        nav_clause = format_recovery_actions_llm_clause(
            recovery, context="browse_boundary_previous"
        )
        instruction = (
            f"The user is booking {service_name}. "
            "They asked to go back, but they are already at the earliest available "
            f"results{date_clause} from the last search. "
            "Tell them clearly there is nothing earlier to show."
            + nav_clause
            + " Do not repeat the time list. Do not teach date-axis browse commands. "
            "Keep it to 1–2 sentences."
        )
    else:
        nav_clause = format_recovery_actions_llm_clause(
            recovery, context="browse_boundary_next"
        )
        instruction = (
            f"The user is booking {service_name}. "
            "They asked to see more availability, but there is nothing further "
            f"to show{date_clause} from the last search. "
            "Tell them clearly there are no more times"
            + (f" for {date_label}" if date_label else "")
            + "."
            + nav_clause
            + " Do not repeat the time list. Do not teach date-axis browse commands. "
            "Keep it to 1–2 sentences."
        )

    return LlmRenderRequest(
        render_instruction=instruction,
        facts={
            "structured_context": structured_context or {},
            "browse_status": browse_status,
            "browse_hints": hints,
            "recovery_actions": recovery,
            "direction": direction,
            "search_date": date_label,
        },
        # History is retained for continuity metadata only. Wording for browse
        # status is resolved deterministically (see resolve_browse_status_text)
        # so prior availability lists cannot be re-presented as a fresh offer.
        conversation_history=conversation_history or [],
    )


_BROWSE_STATUS_DEFAULT_TEXT = (
    "There are no more times to show from your last search. "
    "Ask for another date."
)


def _format_requested_clock_label(requested_time: Optional[str]) -> Optional[str]:
    """Turn a normalized clock (e.g. ``17:00``) into a short display label."""
    if not isinstance(requested_time, str):
        return None
    raw = requested_time.strip()
    if not raw:
        return None
    if len(raw) == 5 and raw[2] == ":":
        return format_display_time(f"2000-01-01T{raw}:00")
    if "T" in raw:
        return format_display_time(raw)
    return raw


def resolve_time_mismatch_text(
    *,
    requested_time: Optional[str] = None,
    times: Optional[List[str]] = None,
    alternatives: Optional[List[str]] = None,
    mismatch_location: Optional[str] = None,
    search_date: Optional[str] = None,
    browse_hints: Optional[Dict[str, Any]] = None,
    recovery_actions: Optional[Any] = None,
) -> str:
    """Deterministic wording when a requested time is not among presented offers.

    Explanation uses structured ``mismatch_location``. Recovery wording uses
    structured ``recovery_actions`` (owned by planning/presentation).
    Legacy callers without a location keep the prior alternatives listing.
    """
    from core.planning.recovery_actions import (
        recovery_actions_for_selection_mismatch,
    )
    from core.planning.time_resolution import (
        MISMATCH_LOCATION_EARLIER_PAGE,
        MISMATCH_LOCATION_LATER_PAGE,
        MISMATCH_LOCATION_NOT_IN_CACHE,
    )

    requested_label = _format_requested_clock_label(requested_time)
    location = str(mismatch_location or "").strip().upper()
    actions = recovery_actions
    if not isinstance(actions, list):
        actions = recovery_actions_for_selection_mismatch(
            mismatch_location=location,
            browse_hints=browse_hints,
        )
    recovery = format_mismatch_recovery_text(actions)

    if location == MISMATCH_LOCATION_EARLIER_PAGE:
        base = (
            f"{requested_label} isn't one of the times currently shown—"
            "it was on an earlier page."
            if requested_label
            else (
                "That time isn't one of the times currently shown—"
                "it was on an earlier page."
            )
        )
        return f"{base} {recovery}" if recovery else base

    if location == MISMATCH_LOCATION_LATER_PAGE:
        base = (
            f"{requested_label} isn't one of the times currently shown—"
            "it was on a later page."
            if requested_label
            else (
                "That time isn't one of the times currently shown—"
                "it was on a later page."
            )
        )
        return f"{base} {recovery}" if recovery else base

    if location == MISMATCH_LOCATION_NOT_IN_CACHE:
        date_label = _format_exhaustion_date_label(
            str(search_date) if search_date else None
        )
        if requested_label and date_label:
            unavailable = f"{requested_label} isn't available for {date_label}"
        elif requested_label:
            unavailable = f"{requested_label} isn't available"
        else:
            unavailable = "That time isn't available"
        if recovery:
            return f"{unavailable}. {recovery}"
        return (
            f"{unavailable}. Please choose one of the times currently shown, "
            "or ask for another date."
        )

    labels: List[str] = []
    if times:
        labels = [str(t) for t in times if t]
    elif alternatives:
        labels = _format_alternative_labels([str(a) for a in alternatives if a])

    unavailable = (
        f"{requested_label} isn't available"
        if requested_label
        else "That time isn't available"
    )

    if not labels:
        return f"{unavailable}. Please choose another time."

    if len(labels) == 1:
        times_clause = labels[0]
    elif len(labels) == 2:
        times_clause = f"{labels[0]} and {labels[1]}"
    else:
        times_clause = ", ".join(labels[:-1]) + f" and {labels[-1]}"
    return (
        f"{unavailable}. The available times are {times_clause}. "
        "Which one would you prefer?"
    )


def _format_exhaustion_date_label(search_date: Optional[str]) -> Optional[str]:
    """Format YYYY-MM-DD as a short display label (e.g. July 24)."""
    if not isinstance(search_date, str) or len(search_date.strip()) < 10:
        return None
    raw = search_date.strip()[:10]
    try:
        from datetime import datetime

        dt = datetime.strptime(raw, "%Y-%m-%d")
        return f"{dt.strftime('%B')} {dt.day}"
    except ValueError:
        return raw


def resolve_browse_status_text(
    *,
    browse_status: str,
    direction: str = "next",
    browse_hints: Optional[Dict[str, Any]] = None,
    search_date: Optional[str] = None,
    recovery_actions: Optional[Any] = None,
) -> str:
    """Deterministic user-facing wording for browse exhaustion / boundary status.

    Must never re-list previously presented availability times.
    Must never teach date-axis browse commands.
    Recovery wording comes from structured ``recovery_actions``.
    """
    from core.planning.recovery_actions import recovery_actions_for_browse_boundary

    hints = browse_hints if isinstance(browse_hints, dict) else {}
    status = str(browse_status or "").strip()
    axis = str(direction or "next").strip().lower()
    date_raw = search_date or hints.get("search_date")
    date_label = _format_exhaustion_date_label(
        str(date_raw) if date_raw else None
    )
    actions = recovery_actions
    if not isinstance(actions, list):
        actions = recovery_actions_for_browse_boundary(
            direction=axis, browse_hints=hints
        )
    recovery = format_browse_boundary_recovery_text(actions, direction=axis)

    if axis == "previous":
        if date_label:
            base = f"There are no earlier available times for {date_label}."
        else:
            base = "There is nothing earlier to show from your last search."
        return f"{base} {recovery}" if recovery else base

    if date_label:
        base = f"There are no more times for {date_label}."
    elif status:
        base = "There are no more times to show from your last search."
    else:
        base = "There are no more times to show from your last search."
    return f"{base} {recovery}" if recovery else base


def build_availability_no_more_render_request(
    decision: Optional[Dict[str, Any]],
    *,
    direction: str,
    structured_context: Optional[Dict[str, Any]] = None,
    conversation_history: Optional[List[Dict[str, str]]] = None,
    browse_status: str = "exhausted",
    browse_hints: Optional[Dict[str, Any]] = None,
    search_date: Optional[str] = None,
) -> LlmRenderRequest:
    """Compatibility wrapper for browse exhaustion rendering."""
    return build_availability_browse_status_render_request(
        decision,
        direction=direction,
        browse_status=browse_status,
        browse_hints=browse_hints,
        search_date=search_date,
        structured_context=structured_context,
        conversation_history=conversation_history,
    )
