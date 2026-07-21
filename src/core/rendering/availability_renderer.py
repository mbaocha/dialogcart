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


def _browse_guidance_clause(browse_hints: Optional[Dict[str, Any]]) -> str:
    """Wording hints based on prepared browse_hints only."""
    if not isinstance(browse_hints, dict):
        return ""
    suggested_next = browse_hints.get("suggested_next")
    if suggested_next == "show more":
        return (
            ' You can mention that the user can say "show more" to see additional times.'
        )
    if suggested_next == "next day":
        return (
            ' You can mention that the user can say "next day" to see another '
            "available date."
        )
    if browse_hints.get("has_more_any"):
        return (
            ' You can mention that the user can say "show more" to continue browsing.'
        )
    return ""


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
        guidance = _browse_guidance_clause(browse_hints)
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
            "in a short bullet list. "
            "Ask which time they would like. "
            f"{guidance}"
            f"{date_authority}"
            " Keep the reply to 2–3 sentences plus the list. "
            "Do not invent times or mention staff names. "
            "Do not invent browse options beyond the provided browse_hints."
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
    structured_context: Optional[Dict[str, Any]] = None,
    conversation_history: Optional[List[Dict[str, str]]] = None,
) -> LlmRenderRequest:
    """Build render request for axis-aware browse exhaustion (prepared facts only)."""
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
    has_next_date = bool(hints.get("has_next_date"))
    has_previous_date = bool(hints.get("has_previous_date"))

    if browse_status == "no_more_times_for_date":
        if has_next_date:
            instruction = (
                f"The user is booking {service_name}. "
                "They asked for more times on the current date, but there are no "
                "additional times left that day. "
                'Tell them clearly, and mention they can say "next day" to see '
                "another available date. "
                "Do not repeat the time list. Keep it to 1–2 sentences."
            )
        else:
            instruction = (
                f"The user is booking {service_name}. "
                "They asked for more times, but there are no additional times on "
                "this date and no later dates in the current search. "
                "Tell them clearly there are no more times to show. "
                "Do not repeat the time list. Keep it to 1–2 sentences."
            )
    elif browse_status == "no_previous_times_for_date":
        if has_previous_date:
            instruction = (
                f"The user is booking {service_name}. "
                "They asked for earlier times on the current date, but they are "
                "already viewing the earliest times that day. "
                'Mention they can say "previous day" to see an earlier available date. '
                "Do not repeat the time list. Keep it to 1–2 sentences."
            )
        else:
            instruction = (
                f"The user is booking {service_name}. "
                "They asked for earlier times, but they are already viewing the "
                "earliest available times from the last search. "
                "Tell them clearly there are no earlier times to show. "
                "Do not repeat the time list. Keep it to 1–2 sentences."
            )
    elif browse_status == "no_next_date":
        instruction = (
            f"The user is booking {service_name}. "
            "They asked for the next day, but there are no later dates with "
            "availability in the current search. "
            "Tell them clearly. Do not invent dates. Keep it to 1–2 sentences."
        )
    elif browse_status == "no_previous_date":
        instruction = (
            f"The user is booking {service_name}. "
            "They asked for the previous day, but there are no earlier dates with "
            "availability in the current search. "
            "Tell them clearly. Do not invent dates. Keep it to 1–2 sentences."
        )
    elif browse_status == "target_date_not_in_cache":
        instruction = (
            f"The user is booking {service_name}. "
            "They asked about a date that is not in the current availability results. "
            "Tell them clearly that date is not available in the current search, "
            "and offer to check another day from the current results or search again. "
            "Do not invent times for that date. Keep it to 1–2 sentences."
        )
    elif direction == "previous":
        instruction = (
            f"The user is booking {service_name}. "
            "They asked to go back, but they are already at the earliest available "
            "results from the last search. "
            "Tell them clearly there is nothing earlier to show. "
            "Do not repeat the time list. Keep it to 1–2 sentences."
        )
    else:
        instruction = (
            f"The user is booking {service_name}. "
            "They asked to see more availability, but there is nothing further "
            "to show from the last search. "
            "Tell them clearly there are no more available times or dates. "
            "Do not repeat the time list. Keep it to 1–2 sentences."
        )

    return LlmRenderRequest(
        render_instruction=instruction,
        facts={
            "structured_context": structured_context or {},
            "browse_status": browse_status,
            "browse_hints": hints,
            "direction": direction,
        },
        conversation_history=conversation_history or [],
    )


def build_availability_no_more_render_request(
    decision: Optional[Dict[str, Any]],
    *,
    direction: str,
    structured_context: Optional[Dict[str, Any]] = None,
    conversation_history: Optional[List[Dict[str, str]]] = None,
    browse_status: str = "exhausted",
    browse_hints: Optional[Dict[str, Any]] = None,
) -> LlmRenderRequest:
    """Compatibility wrapper for browse exhaustion rendering."""
    return build_availability_browse_status_render_request(
        decision,
        direction=direction,
        browse_status=browse_status,
        browse_hints=browse_hints,
        structured_context=structured_context,
        conversation_history=conversation_history,
    )
