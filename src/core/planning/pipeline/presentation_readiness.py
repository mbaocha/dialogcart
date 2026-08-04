"""Presentation-readiness evidence consumed by Stage 08 Decision.

This module computes presentation and recovery conditions without selecting the
final planner outcome. Stage 08 remains the owner of status/action/stage/
awaiting precedence.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple


_PRESENTATION_ACTION_BRANCHES = frozenset(
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
    availability_browse: Optional[Dict[str, Any]],
) -> bool:
    """Return whether Decision already has a concrete presentation branch."""
    if action_branch in _PRESENTATION_ACTION_BRANCHES:
        return True
    if availability_reshow and action_branch == "availability_reshow":
        return True
    return bool(
        isinstance(availability_browse, dict)
        and availability_browse.get("direction")
    )


@dataclass(frozen=True)
class PresentationReadinessEvidence:
    """Outcome-free presentation inputs for Stage 08 selection."""

    availability_reshow_allowed: bool = False
    availability_browse: Optional[Tuple[Tuple[str, Any], ...]] = None
    has_presentation: bool = False
    recovery_presentation_eligible: bool = False
    unanswered_required_slots_without_presentation: bool = False
    promptable_optional_eligible: bool = False

    def availability_browse_dict(self) -> Optional[Dict[str, Any]]:
        if self.availability_browse is None:
            return None
        return dict(self.availability_browse)


def build_presentation_readiness_evidence(
    *,
    payload: Dict[str, Any],
    requested_availability_reshow: bool,
    block_auto_reshow: bool,
    status: str,
    action: Optional[str],
    action_branch: Optional[str],
    missing_slots: list[str],
    promptable_slots: list[str],
    ask_next: Optional[str],
    has_planning_evidence: bool,
    turn_understanding: Optional[str],
) -> PresentationReadinessEvidence:
    """Compute presentation/recovery readiness without selecting an outcome."""
    availability_reshow_allowed = bool(
        requested_availability_reshow and not block_auto_reshow
    )

    browse = payload.get("availability_browse")
    if not isinstance(browse, dict):
        browse = None

    presentation_present = has_planner_presentation(
        action_branch=action_branch,
        availability_reshow=availability_reshow_allowed,
        availability_browse=browse,
    )

    recovery_eligible = bool(
        status == "READY"
        and action is None
        and missing_slots
        and not has_planning_evidence
        and turn_understanding == "UNRECOGNIZED_INPUT"
        and action_branch not in _PRESENTATION_ACTION_BRANCHES
    )

    unanswered_required = bool(
        status == "READY"
        and action is None
        and missing_slots
        and not presentation_present
    )

    promptable_optional = bool(
        status == "READY"
        and action is None
        and not missing_slots
        and promptable_slots
        and ask_next in promptable_slots
        and not presentation_present
    )

    frozen_browse = tuple(browse.items()) if browse is not None else None
    return PresentationReadinessEvidence(
        availability_reshow_allowed=availability_reshow_allowed,
        availability_browse=frozen_browse,
        has_presentation=presentation_present,
        recovery_presentation_eligible=recovery_eligible,
        unanswered_required_slots_without_presentation=unanswered_required,
        promptable_optional_eligible=promptable_optional,
    )
