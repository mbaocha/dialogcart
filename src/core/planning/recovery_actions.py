"""Structured recovery actions for conversation workflows.

Planning / presentation / workflows decide *which* recoveries are valid.
Rendering decides *how* to present them. Renderers must not invent actions.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence

# Stable action types — generic across workflows (browse, clarification, etc.).
BROWSE_NEXT = "browse_next"
BROWSE_PREVIOUS = "browse_previous"
CHOOSE_ANOTHER_DATE = "choose_another_date"
CHOOSE_VISIBLE_OPTION = "choose_visible_option"

RecoveryAction = Dict[str, Any]


def recovery_action(action_type: str) -> RecoveryAction:
    """Build a single recovery action dict."""
    return {"type": str(action_type).strip()}


def normalize_recovery_actions(
    actions: Optional[Sequence[Mapping[str, Any]]],
) -> List[RecoveryAction]:
    """Return a deduped list of well-formed recovery actions (order preserved)."""
    if not isinstance(actions, Sequence) or isinstance(actions, (str, bytes)):
        return []
    out: List[RecoveryAction] = []
    seen: set[str] = set()
    for raw in actions:
        if not isinstance(raw, Mapping):
            continue
        action_type = str(raw.get("type") or "").strip()
        if not action_type or action_type in seen:
            continue
        seen.add(action_type)
        out.append(recovery_action(action_type))
    return out


def action_types(actions: Optional[Sequence[Mapping[str, Any]]]) -> List[str]:
    return [a["type"] for a in normalize_recovery_actions(actions)]


def has_action(
    actions: Optional[Sequence[Mapping[str, Any]]],
    action_type: str,
) -> bool:
    return action_type in action_types(actions)


def accepts_empty_availability_recovery(
    luma_response: Optional[Mapping[str, Any]],
    session_state: Optional[Mapping[str, Any]],
) -> bool:
    """Whether a bare affirmative accepts a structured another-date recovery."""
    if not isinstance(luma_response, Mapping) or not isinstance(session_state, Mapping):
        return False
    response_act = luma_response.get("response_act")
    intent = luma_response.get("intent")
    intent_name = intent.get("name") if isinstance(intent, Mapping) else intent
    if response_act != "CONFIRM_ACTION" and intent_name != "CONFIRM_ACTION":
        return False
    if session_state.get("confirmation_state") == "pending":
        return False
    availability = session_state.get("availability")
    if not isinstance(availability, Mapping):
        return False
    presentation = availability.get("presentation")
    presented = presentation.get("presented") if isinstance(presentation, Mapping) else None
    if not isinstance(presented, Mapping):
        return False
    if presented.get("slots"):
        return False
    return has_action(presented.get("recovery_actions"), CHOOSE_ANOTHER_DATE)


def _browse_nav_capabilities(
    browse_hints: Optional[Mapping[str, Any]],
) -> tuple[bool, bool]:
    hints = browse_hints if isinstance(browse_hints, Mapping) else {}
    can_next = bool(
        hints.get("suggested_next")
        or hints.get("has_more_any")
        or hints.get("has_more_times")
        or hints.get("has_next")
    )
    can_previous = bool(
        hints.get("suggested_previous")
        or hints.get("has_previous_any")
        or hints.get("has_previous_times")
        or hints.get("has_previous")
    )
    return can_next, can_previous


def recovery_actions_for_browse_window(
    browse_hints: Optional[Mapping[str, Any]],
) -> List[RecoveryAction]:
    """Valid page-navigation recoveries for the current presented window."""
    can_next, can_previous = _browse_nav_capabilities(browse_hints)
    actions: List[RecoveryAction] = []
    if can_next:
        actions.append(recovery_action(BROWSE_NEXT))
    if can_previous:
        actions.append(recovery_action(BROWSE_PREVIOUS))
    return actions


def recovery_actions_for_browse_boundary(
    *,
    direction: str,
    browse_hints: Optional[Mapping[str, Any]] = None,
) -> List[RecoveryAction]:
    """Valid recoveries when browse cannot move further in ``direction``."""
    can_next, can_previous = _browse_nav_capabilities(browse_hints)
    axis = str(direction or "next").strip().lower()
    actions: List[RecoveryAction] = []
    if axis == "previous":
        if can_next:
            actions.append(recovery_action(BROWSE_NEXT))
        actions.append(recovery_action(CHOOSE_ANOTHER_DATE))
        return actions
    if can_previous:
        actions.append(recovery_action(BROWSE_PREVIOUS))
    actions.append(recovery_action(CHOOSE_ANOTHER_DATE))
    return actions


def recovery_actions_for_selection_mismatch(
    *,
    mismatch_location: str,
    browse_hints: Optional[Mapping[str, Any]] = None,
) -> List[RecoveryAction]:
    """Valid recoveries when a requested time is not on the current page."""
    location = str(mismatch_location or "").strip().upper()
    can_next, can_previous = _browse_nav_capabilities(browse_hints)
    if location == "EARLIER_PAGE":
        actions: List[RecoveryAction] = []
        if can_previous:
            actions.append(recovery_action(BROWSE_PREVIOUS))
        actions.append(recovery_action(CHOOSE_VISIBLE_OPTION))
        return actions
    if location == "LATER_PAGE":
        actions = []
        if can_next:
            actions.append(recovery_action(BROWSE_NEXT))
        actions.append(recovery_action(CHOOSE_VISIBLE_OPTION))
        return actions
    if location == "NOT_IN_CACHE":
        return [
            recovery_action(CHOOSE_VISIBLE_OPTION),
            recovery_action(CHOOSE_ANOTHER_DATE),
        ]
    return []
