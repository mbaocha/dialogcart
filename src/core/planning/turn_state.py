"""
Turn State Resolution

Centralizes the logic for computing effective_collected_slots, missing_slots, and status
from intent and merged_session_slots using the planner.

This is the single source of truth for turn state resolution, ensuring consistency
across all callers. It applies the invariant:
- READY only if missing_slots empty
"""

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from core.planning.policy.action_policy import load_planning_policy, plan_intent

logger = logging.getLogger(__name__)

# Load planning policy once at module level
_planning_policy = None


def _get_planning_policy() -> Dict[str, Any]:
    """Get planning policy, loading it once if needed."""
    global _planning_policy
    if _planning_policy is None:
        _planning_policy = load_planning_policy()
    return _planning_policy


class DecisionReason(str, Enum):
    """Enumeration of reasons for turn status decision."""

    MISSING_REQUIRED_SLOTS = "MISSING_REQUIRED_SLOTS"
    READY_ALL_SATISFIED = "READY_ALL_SATISFIED"
    NEEDS_CONFIRMATION = "NEEDS_CONFIRMATION"
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"


@dataclass
class TurnState:
    """
    Turn State object capturing all turn processing state.

    Built ONLY at the end of turn processing, containing all slot states,
    status, and decision reasoning. This is the single source of truth for
    what happened in a turn and why.
    """

    intent: str
    raw_luma_slots: Dict[str, Any] = field(default_factory=dict)
    merged_session_slots: Dict[str, Any] = field(default_factory=dict)
    promoted_slots: Dict[str, Any] = field(default_factory=dict)
    effective_collected_slots: Dict[str, Any] = field(default_factory=dict)
    required_slots: List[str] = field(default_factory=list)
    missing_slots: List[str] = field(default_factory=list)
    status: str = ""
    decision_reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Convert TurnState to dictionary for JSON serialization."""
        return {
            "intent": self.intent,
            "raw_luma_slots": {
                "keys": (
                    list(self.raw_luma_slots.keys())
                    if isinstance(self.raw_luma_slots, dict)
                    else []
                ),
                "values": (
                    {k: str(v)[:50] for k, v in self.raw_luma_slots.items()}
                    if isinstance(self.raw_luma_slots, dict)
                    else {}
                ),
            },
            "merged_session_slots": {
                "keys": (
                    list(self.merged_session_slots.keys())
                    if isinstance(self.merged_session_slots, dict)
                    else []
                ),
                "values": (
                    {k: str(v)[:50] for k, v in self.merged_session_slots.items()}
                    if isinstance(self.merged_session_slots, dict)
                    else {}
                ),
            },
            "promoted_slots": {
                "keys": (
                    list(self.promoted_slots.keys())
                    if isinstance(self.promoted_slots, dict)
                    else []
                ),
                "values": (
                    {k: str(v)[:50] for k, v in self.promoted_slots.items()}
                    if isinstance(self.promoted_slots, dict)
                    else {}
                ),
            },
            "effective_collected_slots": {
                "keys": (
                    list(self.effective_collected_slots.keys())
                    if isinstance(self.effective_collected_slots, dict)
                    else []
                ),
                "values": (
                    {k: str(v)[:50] for k, v in self.effective_collected_slots.items()}
                    if isinstance(self.effective_collected_slots, dict)
                    else {}
                ),
            },
            "required_slots": self.required_slots,
            "missing_slots": self.missing_slots,
            "status": self.status,
            "decision_reason": self.decision_reason,
        }


def _slots_for_planning(merged_session_slots: Dict[str, Any]) -> Dict[str, Any]:
    """Copy durable slots and substitute canonical service_id when present."""
    slots = dict(merged_session_slots or {})
    canonical = slots.get("_canonical_service_id")
    if canonical is not None:
        slots["service_id"] = canonical
    return slots


def _apply_appointment_temporal_time_satisfaction(
    *,
    intent_name: str,
    missing_slots: List[str],
    durable_slots: Dict[str, Any],
    temporal: Any,
    time_proposal: Any = None,
) -> List[str]:
    """Apply CREATE_APPOINTMENT Temporal/time_proposal semantics to missing_slots.

    Preserves historical processor behavior:
    - exact start_time satisfies time
    - bounded fuzzy/window satisfies time
    - unbounded fuzzy/window forces time missing even if a prior virtual view cleared it
    """
    if intent_name != "CREATE_APPOINTMENT":
        return missing_slots

    mode = None
    start = None
    end = None
    if isinstance(time_proposal, dict):
        mode = time_proposal.get("mode")
        if mode == "exact":
            start = time_proposal.get("value")
        else:
            start = time_proposal.get("start")
            end = time_proposal.get("end")
    elif isinstance(temporal, dict):
        if temporal.get("start_time"):
            mode = "exact"
            start = temporal.get("start_time")
            end = temporal.get("end_time")
        else:
            label = (temporal.get("start_time_expression") or "").strip().lower()
            if label in ("morning", "afternoon", "evening", "night"):
                mode = "fuzzy"
                start = temporal.get("start_time")
                end = temporal.get("end_time")

    if mode is None:
        return missing_slots

    has_start = start is not None and str(start).strip() != ""
    has_end = end is not None and str(end).strip() != ""
    is_bounded = has_start and has_end
    has_concrete_time_slot = (
        durable_slots.get("time") is not None if isinstance(durable_slots, dict) else False
    )
    is_exact_mode = mode == "exact"

    if mode in ("fuzzy", "window"):
        can_remove_time = has_concrete_time_slot or is_bounded
    else:
        can_remove_time = has_concrete_time_slot or is_exact_mode

    result = list(missing_slots)

    if is_bounded:
        if "time" in result and can_remove_time:
            result = [s for s in result if s != "time"]
            logger.info(
                "[FINALIZE_TURN_STATE] removed time (mode=%s bounded=%s concrete=%s)",
                mode,
                True,
                has_concrete_time_slot,
            )
    elif mode == "exact" and has_start:
        if "time" in result:
            result = [s for s in result if s != "time"]
            logger.info(
                "[FINALIZE_TURN_STATE] removed time (mode=exact start=%s)",
                start,
            )
    elif mode in ("fuzzy", "window") and "time" not in result:
        result.append("time")
        logger.info(
            "[FINALIZE_TURN_STATE] added time (mode=%s unbounded)",
            mode,
        )

    return result


def _apply_modify_booking_issues_override(
    *,
    intent_name: str,
    missing_slots: List[str],
    planning_context: Dict[str, Any],
) -> List[str]:
    """When MODIFY_BOOKING has empty raw slots, derive missing from Luma issues."""
    if intent_name != "MODIFY_BOOKING":
        return missing_slots

    raw_luma_slots = planning_context.get("raw_luma_slots") or {}
    if isinstance(raw_luma_slots, dict) and raw_luma_slots:
        return missing_slots

    issues = planning_context.get("issues")
    if not isinstance(issues, dict) or not issues:
        return missing_slots

    issues_missing_slots: List[str] = []
    for key in issues.keys():
        normalized_key = key.split(":")[0].strip().lower()
        if normalized_key in ("date", "time", "booking_id"):
            issues_missing_slots.append(normalized_key)

    if not issues_missing_slots:
        return missing_slots

    if "booking_id" not in issues_missing_slots:
        issues_missing_slots.append("booking_id")

    result = sorted(set(issues_missing_slots))
    logger.info(
        "[FINALIZE_TURN_STATE] MODIFY_BOOKING derived missing_slots from issues: %s",
        result,
    )
    return result


def _prioritize_awaiting_slot(
    missing_slots: List[str], awaiting_slot: Optional[str]
) -> List[str]:
    """Move awaiting_slot to index 0 when present in missing_slots (presentation order)."""
    if awaiting_slot is None or awaiting_slot not in missing_slots:
        return missing_slots
    reordered = [s for s in missing_slots if s != awaiting_slot]
    reordered.insert(0, awaiting_slot)
    logger.debug(
        "[FINALIZE_TURN_STATE] prioritized awaiting_slot=%s",
        awaiting_slot,
    )
    return reordered


def finalize_turn_state(
    intent_name: str,
    merged_session_slots: Dict[str, Any],
    existing_missing_slots: Optional[List[str]] = None,
    planning_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Finalize turn state by computing effective_collected_slots, missing_slots, and status.

    Canonical owner of effective missing_slots for Planning. Callers must not recompute
    or mutate the returned missing_slots list for semantic reasons.

    Status returned here is the slot-completeness base status only. Confirmation and
    capability gating may override status downstream without rewriting missing_slots.
    """
    if not intent_name:
        return {
            "effective_slots": {},
            "missing_slots": [],
            "status": "NEEDS_CLARIFICATION",
        }

    from core.planning.planner.missing_slots import (
        get_planning_required_slots_for_intent,
        normalize_modify_booking_missing_slots,
    )
    from core.planning.temporal_proposal import (
        expand_slots_for_planning,
        proposal_satisfies_planning_time,
    )

    pc = planning_context or {}
    policy = _get_planning_policy()
    durable_slots = dict(merged_session_slots or {})
    slots_for_planning = _slots_for_planning(durable_slots)

    planning_slots = expand_slots_for_planning(
        slots_for_planning,
        date_proposal=pc.get("date_proposal"),
        time_proposal=pc.get("time_proposal"),
        nlu_facts=pc.get("nlu_facts"),
        intent_name=intent_name,
        temporal=pc.get("temporal"),
    )
    plan = plan_intent(intent_name, planning_slots, policy)

    collected_slot_names = set(plan["collected_slots"])
    missing_slots = list(plan["missing_slots"])

    if intent_name == "CREATE_APPOINTMENT":
        if (
            proposal_satisfies_planning_time(pc.get("time_proposal"))
            and "time" in missing_slots
        ):
            missing_slots = [s for s in missing_slots if s != "time"]

        missing_slots = _apply_appointment_temporal_time_satisfaction(
            intent_name=intent_name,
            missing_slots=missing_slots,
            durable_slots=durable_slots,
            temporal=pc.get("temporal"),
            time_proposal=pc.get("time_proposal"),
        )

    missing_slots = _apply_modify_booking_issues_override(
        intent_name=intent_name,
        missing_slots=missing_slots,
        planning_context=pc,
    )
    missing_slots = normalize_modify_booking_missing_slots(
        missing_slots,
        intent_name=intent_name,
    )
    missing_slots = _prioritize_awaiting_slot(missing_slots, pc.get("awaiting_slot"))

    if existing_missing_slots is not None and existing_missing_slots != missing_slots:
        logger.debug(
            "[FINALIZE_TURN_STATE] recomputed missing_slots from current slots "
            "(intent=%s stale=%s computed=%s)",
            intent_name,
            existing_missing_slots,
            missing_slots,
        )

    try:
        required_slots = get_planning_required_slots_for_intent(intent_name)
    except Exception:
        required_slots = []

    logger.debug(
        "[FINALIZE_TURN_STATE] intent=%s required=%s collected=%s missing=%s",
        intent_name,
        required_slots,
        sorted(collected_slot_names),
        missing_slots,
    )

    effective_collected_slots = {
        slot_name: slot_value
        for slot_name, slot_value in durable_slots.items()
        if slot_value is not None
    }

    if intent_name == "UNKNOWN":
        status = "NEEDS_CLARIFICATION"
        logger.info(
            "[FINALIZE_TURN_STATE] UNKNOWN intent - forcing NEEDS_CLARIFICATION regardless of missing_slots"
        )
    elif len(missing_slots) > 0:
        status = "NEEDS_CLARIFICATION"
    else:
        status = "READY"

    logger.info(
        "[FINALIZE_TURN_STATE] intent=%s collected_slots=%s effective_slots=%s missing_slots=%s status=%s",
        intent_name,
        sorted(collected_slot_names),
        sorted(effective_collected_slots.keys()),
        missing_slots,
        status,
    )

    return {
        "effective_slots": effective_collected_slots,
        "missing_slots": missing_slots,
        "status": status,
    }
