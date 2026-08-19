"""Execution-readiness evidence for Stage 08 Decision.

Planning / Evaluate constructs readiness (policy flags, bound-datetime trust,
execution proposal context). Stage 08 consumes it for action/status/stage/
awaiting selection — Decision does not recompute readiness overlays.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from core.planning.facts import build_policy_execution_flags
from core.planning.policy.action_policy import load_planning_policy, plan_intent
from core.planning.temporal_proposal import (
    ExecutionProposalResolutionContext,
    has_bound_booking_datetime,
)


@dataclass(frozen=True)
class ExecutionReadinessEvidence:
    """Typed readiness inputs Decision consumes when selecting outcomes.

    Does not select ``action`` / ``status`` / ``stage`` / ``awaiting``.
    """

    flags: Dict[str, Any] = field(default_factory=dict)
    executable_actions: Tuple[str, ...] = ()
    availability_resolved: bool = False
    availability_invalidated: bool = False
    bound_datetime_cleared: bool = False
    datetime_bound: bool = False
    execution_proposal_context: ExecutionProposalResolutionContext = field(
        default_factory=dict  # type: ignore[assignment]
    )
    revision_invalidated_availability: bool = False


def build_execution_readiness_evidence(
    *,
    intent_name: str,
    effective_slots: Dict[str, Any],
    payload: Dict[str, Any],
    session_state: Optional[Dict[str, Any]],
    missing_slots: List[str],
    needs_clarification: bool,
    availability_ready: bool,
    confirmation_state: Optional[str],
    organization_id: int,
    confirm_booking_continuation: bool,
    availability_invalidation: Any = None,
    bound_datetime_clear: Any = None,
) -> ExecutionReadinessEvidence:
    """Project Stage 05/06/03 overlays into execution-readiness evidence."""
    bound_datetime_cleared = bool(
        bound_datetime_clear is not None
        and getattr(bound_datetime_clear, "cleared", False)
    )
    revision_invalidated = bool(payload.get("_revision_invalidated_availability"))
    availability_invalidated = bool(
        (
            availability_invalidation is not None
            and getattr(availability_invalidation, "invalidated", False)
        )
        or revision_invalidated
    )

    availability_resolved = availability_ready
    if availability_invalidated:
        # Typed Stage 06 evidence and Stage 03 revision share the same effect:
        # prior availability trust must not drive this turn's proposals.
        availability_resolved = False

    executable_actions: List[str] = []
    if intent_name and intent_name != "UNKNOWN":
        _ea_policy = load_planning_policy()
        _ea_result = plan_intent(intent_name, effective_slots, _ea_policy)
        executable_actions = list(_ea_result.get("executable_actions", []))
    if payload.get("_blocked_entity_slots"):
        executable_actions = []
    from core.session.booking_lifecycle import BookingLifecycle, derive_booking_lifecycle

    committed_create = bool(
        intent_name == "CREATE_APPOINTMENT"
        and derive_booking_lifecycle(session_state) == BookingLifecycle.COMMITTED
    )
    if committed_create:
        executable_actions = []

    flags: Dict[str, Any] = {}
    if intent_name and intent_name != "UNKNOWN":
        flags = build_policy_execution_flags(
            intent_name=intent_name,
            slots=effective_slots,
            session_state=session_state,
            luma_response=payload,
            missing_slots=missing_slots,
            needs_clarification=needs_clarification,
            availability_resolved=availability_resolved,
            confirmation_state=confirmation_state,
            organization_id=organization_id,
            confirm_booking_continuation=confirm_booking_continuation,
        )
        if availability_invalidated:
            # Stage 06 supersession or Stage 03 criteria revision.
            flags["availability_ready"] = False
            flags["availability_resolved"] = False
            flags["availability_check_required"] = True
        if bound_datetime_cleared:
            flags["time_selection_ready"] = False
            flags["time_selection_required"] = intent_name == "CREATE_APPOINTMENT"
        if committed_create:
            flags["availability_ready"] = False
            flags["availability_resolved"] = False
            flags["user_confirmation_satisfied"] = False
            flags["booking_not_committed"] = False

    datetime_bound = has_bound_booking_datetime(
        effective_slots,
        None if bound_datetime_cleared else session_state,
        payload,
    )

    current_turn_has_explicit_time = bool(payload.get("_current_turn_has_time"))
    if (
        bound_datetime_cleared
        and bound_datetime_clear is not None
        and not getattr(bound_datetime_clear, "preserve_current_turn_time", False)
    ):
        # Cleared prior selection without a new uttered time (or with a stale
        # NLU echo of that selection): do not feed temporal/proposal into
        # execution as current-turn time evidence.
        current_turn_has_explicit_time = False
    current_turn_time_proposal = (
        payload.get("time_proposal")
        if current_turn_has_explicit_time
        and isinstance(payload.get("time_proposal"), dict)
        else None
    )
    current_turn_temporal = (
        payload.get("temporal")
        if current_turn_has_explicit_time
        and isinstance(payload.get("temporal"), dict)
        else None
    )
    proposal_resolution_context: ExecutionProposalResolutionContext = {
        "current_turn_time_proposal": current_turn_time_proposal,
        "current_turn_temporal": current_turn_temporal,
        "current_turn_has_explicit_time": current_turn_has_explicit_time,
        "session_time_proposal_reuse_allowed": not (
            availability_invalidated or bound_datetime_cleared
        ),
        "confirmation_continuation": confirm_booking_continuation,
        "availability_invalidated": availability_invalidated,
        "bound_datetime_cleared": bound_datetime_cleared,
    }

    return ExecutionReadinessEvidence(
        flags=flags,
        executable_actions=tuple(executable_actions),
        availability_resolved=availability_resolved,
        availability_invalidated=availability_invalidated,
        bound_datetime_cleared=bound_datetime_cleared,
        datetime_bound=datetime_bound,
        execution_proposal_context=proposal_resolution_context,
        revision_invalidated_availability=revision_invalidated,
    )
