"""Presentation-readiness evidence for Stage 08 Decision.

Projects auto-reshow, cache-satisfiable browse, and terminal presentation/
recovery eligibility. Stage 08 consumes these flags when selecting outcomes —
Decision remains the owner of status/action/stage/awaiting precedence.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Tuple


PRESENTATION_ACTION_BRANCHES = frozenset(
    {
        "availability_reshow",
        "cache_satisfiable_browse",
        "recovery_presentation",
    }
)


def has_planner_presentation(
    *,
    action_branch: Optional[str],
    availability_reshow: bool,
    availability_browse: Optional[Mapping[str, Any]],
) -> bool:
    """True when Decision already holds a concrete planner presentation outcome."""
    if action_branch in PRESENTATION_ACTION_BRANCHES:
        return True
    if availability_reshow and action_branch == "availability_reshow":
        return True
    return bool(
        isinstance(availability_browse, Mapping)
        and availability_browse.get("direction")
    )


def _freeze_mapping(
    value: Optional[Mapping[str, Any]],
) -> Optional[Tuple[Tuple[str, Any], ...]]:
    if not isinstance(value, Mapping):
        return None
    return tuple(value.items())


@dataclass(frozen=True)
class PresentationReadinessEvidence:
    """Outcome-free presentation inputs Decision consumes when selecting outcomes.

    Does not select ``action`` / ``status`` / ``stage`` / ``awaiting``.
    """

    availability_reshow_allowed: bool = False
    cache_satisfiable_browse: Optional[Tuple[Tuple[str, Any], ...]] = None
    availability_browse: Optional[Tuple[Tuple[str, Any], ...]] = None
    has_presentation: bool = False
    recovery_presentation_eligible: bool = False
    unanswered_required_slots_without_presentation: bool = False
    promptable_optional_eligible: bool = False

    def cache_satisfiable_browse_dict(self) -> Optional[Dict[str, Any]]:
        if self.cache_satisfiable_browse is None:
            return None
        return dict(self.cache_satisfiable_browse)

    def availability_browse_dict(self) -> Optional[Dict[str, Any]]:
        if self.availability_browse is None:
            return None
        return dict(self.availability_browse)


def build_presentation_readiness_evidence(
    *,
    payload: Dict[str, Any],
    session_state: Optional[Dict[str, Any]] = None,
    requested_availability_reshow: bool = False,
    block_auto_reshow: bool = False,
    status: Optional[str] = None,
    action: Optional[str] = None,
    action_branch: Optional[str] = None,
    missing_slots: Optional[List[str]] = None,
    promptable_slots: Optional[List[str]] = None,
    ask_next: Optional[str] = None,
    has_planning_evidence: bool = False,
    turn_understanding: Optional[str] = None,
    availability_browse: Optional[Mapping[str, Any]] = None,
) -> PresentationReadinessEvidence:
    """Compute presentation/recovery readiness without selecting an outcome.

    Early call (no Decision snapshot): auto-reshow + cache-satisfiable browse.
    Late call (with Decision snapshot): terminal recovery / demotion / promptable
    eligibility for the provisional outcomes Decision already selected.
    """
    availability_reshow_allowed = bool(
        requested_availability_reshow and not block_auto_reshow
    )

    from core.workflows.availability.browse import (
        cache_satisfiable_browse_request,
    )

    cache_browse = cache_satisfiable_browse_request(payload, session_state)
    if not isinstance(cache_browse, dict):
        cache_browse = None

    if availability_browse is None:
        payload_browse = payload.get("availability_browse")
        if isinstance(payload_browse, dict):
            availability_browse = payload_browse
        else:
            availability_browse = None

    missing = list(missing_slots or [])
    promptables = list(promptable_slots or [])

    presentation_present = False
    recovery_eligible = False
    unanswered_required = False
    promptable_optional = False

    if status is not None:
        presentation_present = has_planner_presentation(
            action_branch=action_branch,
            availability_reshow=availability_reshow_allowed,
            availability_browse=availability_browse,
        )
        recovery_eligible = bool(
            status == "READY"
            and action is None
            and missing
            and not has_planning_evidence
            and turn_understanding == "UNRECOGNIZED_INPUT"
            and action_branch not in PRESENTATION_ACTION_BRANCHES
        )
        unanswered_required = bool(
            status == "READY"
            and action is None
            and missing
            and not presentation_present
        )
        promptable_optional = bool(
            status == "READY"
            and action is None
            and not missing
            and promptables
            and ask_next in promptables
            and not presentation_present
        )

    return PresentationReadinessEvidence(
        availability_reshow_allowed=availability_reshow_allowed,
        cache_satisfiable_browse=_freeze_mapping(cache_browse),
        availability_browse=_freeze_mapping(availability_browse),
        has_presentation=presentation_present,
        recovery_presentation_eligible=recovery_eligible,
        unanswered_required_slots_without_presentation=unanswered_required,
        promptable_optional_eligible=promptable_optional,
    )
