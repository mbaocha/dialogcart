"""
Slot Contract - Planning Integration

DEPRECATED: This module is being phased out in favor of the new planning modules.
All slot requirements MUST come from intent_planning.yaml via the planner.

GUARD: This module should NOT compute missing slots directly.
Use core.planning.policy.action_policy.plan_intent() instead.

PLANNING BOUNDARY:
- Planning = intent_planning.yaml + planner code (core.planning.policy.action_policy)
- Dialog = dialog_policy.yaml (consumes planner output, does NOT compute missing slots) - available via core.planning
- Execution = intent_execution.yaml (dumb routing only, does NOT reference slots)

This module provides backward-compatible wrappers that delegate to the new modules.
"""

from typing import Any, Dict

# Re-export from new location for backward compatibility
from core.planning.orchestration.missing_slots import (
    compute_missing_slots,
    get_planning_required_slots_for_intent,
)


# Keep old function for backward compatibility
def get_required_slots_for_intent(intent_name: str):
    """
    DEPRECATED: Get required slots from planner instead.

    This function is kept for backward compatibility but delegates to the planner.
    New code should use core.planning.policy.action_policy.plan_intent() directly.
    """
    from core.planning.policy.action_policy import load_planning_policy

    policy = load_planning_policy()
    intent_policy = policy.get(intent_name, {})
    required_slots = intent_policy.get("required_slots", [])
    return required_slots if isinstance(required_slots, list) else []


def filter_slots_by_domain(
    slots: Dict[str, Any], intent_name: str, planning_only: bool = False
) -> Dict[str, Any]:
    """
    Filter slots to only include those valid for the intent's domain.

    ARCHITECTURAL INVARIANT: Domain slot isolation
    - Service domain (CREATE_APPOINTMENT, MODIFY_BOOKING): date, time, service_id
    - Reservation domain (CREATE_RESERVATION, MODIFY_RESERVATION): start_date, end_date, service_id
    - service_id is valid in both domains
    - date/time from service must NOT leak into reservation
    - start_date/end_date from reservation must NOT leak into service
    - Generic 'date' must NOT satisfy start_date/end_date for CREATE_RESERVATION

    This must be called BEFORE computing effective_collected_slots to prevent
    cross-domain slot leakage.

    PLANNING-ONLY MODE: When planning_only=True, skip domain filtering.
    Planning must proceed with all slots, even if they're "wrong" for the domain.
    Validation happens in execution layer, not planning.

    Args:
        slots: Slots dictionary to filter
        intent_name: Intent name to determine domain
        planning_only: If True, skip domain filtering and return all slots

    Returns:
        Filtered slots dictionary (only slots valid for intent domain, or all slots if planning_only=True)
    """
    import logging

    logger = logging.getLogger(__name__)

    # PLANNING-ONLY: Skip domain filtering - planning must proceed with all slots
    if planning_only:
        logger.debug(
            f"[DOMAIN_FILTER] PLANNING-ONLY: Skipping domain filter, returning all slots"
        )
        return slots.copy() if slots else {}

    if not intent_name or not slots:
        return slots.copy() if slots else {}

    # Define domain-specific valid slots
    service_domain_intents = {"CREATE_APPOINTMENT", "MODIFY_BOOKING"}
    reservation_domain_intents = {"CREATE_RESERVATION", "MODIFY_RESERVATION"}

    # Determine domain
    if intent_name in service_domain_intents:
        # Service domain: date, time, service_id, has_datetime, date_range, booking_id
        valid_slots = {
            "date",
            "time",
            "service_id",
            "has_datetime",
            "date_range",
            "booking_id",
        }

        # MODIFY_BOOKING delta slots: preserve all slots valid for EITHER domain OR declared delta slots
        if intent_name == "MODIFY_BOOKING":
            delta_slots = {"start_date", "end_date", "duration"}
            valid_slots = valid_slots | delta_slots
    elif intent_name in reservation_domain_intents:
        # Reservation domain: start_date, end_date, service_id, date_range (NOT date, time)
        valid_slots = {
            "start_date",
            "end_date",
            "service_id",
            "date_range",
            "booking_id",
        }
    else:
        # Unknown intent - keep all slots (let other filters handle it)
        return slots.copy()

    # Filter slots to only valid ones for domain
    filtered = {}
    dropped = []

    for slot_name, slot_value in slots.items():
        if slot_name in valid_slots:
            filtered[slot_name] = slot_value
        else:
            dropped.append(slot_name)
            logger.debug(
                f"[DOMAIN_FILTER] Dropping slot '{slot_name}' (not valid for {intent_name} domain, "
                f"valid_slots={valid_slots})"
            )

    if dropped:
        logger.info(
            f"[DOMAIN_FILTER] filter_slots_by_domain: "
            f"intent={intent_name}, "
            f"dropped_slots={dropped}, preserved_slots={list(filtered.keys())}"
        )

    return filtered


def filter_collected_slots_for_intent(
    collected_slots: Dict[str, Any], old_intent: str, new_intent: str
) -> Dict[str, Any]:
    """
    Filter collected slots when intent changes.

    ARCHITECTURAL INVARIANT: Intent change is a hard boundary
    - On intent change, drop slots that are not valid for the new intent
    - Preserve slots that overlap semantically (e.g., service_id if applicable)
    - Do NOT reuse old slot names (e.g. date/time must NOT leak into reservation)
    - start_date/end_date must NOT satisfy service date implicitly
    - Only keep slots that are in the new intent's slot universe

    This function must be STRICT to prevent cross-domain slot leakage.

    Args:
        collected_slots: Previously collected slots (effective_slots from session)
        old_intent: Previous intent name
        new_intent: New intent name

    Returns:
        Filtered collected slots (only slots valid for new intent)
    """
    import logging

    logger = logging.getLogger(__name__)

    if old_intent == new_intent:
        # Same intent - keep all slots
        return collected_slots.copy() if collected_slots else {}

    # Intent changed - only keep slots that are valid for new intent
    # CRITICAL: This must be strict to prevent slot leakage

    # Define valid slot universe for each intent (STRICT)
    valid_slots_by_intent = {
        "CREATE_APPOINTMENT": {
            "date",
            "time",
            "service_id",
            "has_datetime",
            "date_range",
        },
        "CREATE_RESERVATION": {"start_date", "end_date", "service_id", "date_range"},
        "MODIFY_BOOKING": {"booking_id"},
        "CANCEL_BOOKING": {"booking_id"},
    }

    valid_slots_new = valid_slots_by_intent.get(new_intent, set())

    # CRITICAL: Strict filtering - only keep slots in valid universe
    filtered = {}
    dropped = []

    for slot_name, slot_value in (collected_slots or {}).items():
        if slot_name in valid_slots_new:
            filtered[slot_name] = slot_value
        else:
            dropped.append(slot_name)
            logger.debug(
                f"[INTENT_CHANGE] Dropping slot '{slot_name}' (not valid for {new_intent}, "
                f"valid_slots={valid_slots_new})"
            )

    # Log filtering results
    if dropped:
        logger.info(
            f"[INTENT_CHANGE] filter_collected_slots_for_intent: "
            f"old_intent={old_intent}, new_intent={new_intent}, "
            f"dropped_slots={dropped}, preserved_slots={list(filtered.keys())}"
        )

    return filtered


def promote_slots_for_intent(
    raw_slots: Dict[str, Any], intent_name: str, context: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Promote raw slots to intent-specific slots (in-memory, non-persistent).

    ARCHITECTURAL INVARIANT: Promotion must be IDEMPOTENT and ADDITIVE.
    - Promotion starts with ALL existing merged slots (session.slots + luma slots)
    - Promotion may ADD derived slots (e.g., date → start_date)
    - Promotion must NEVER remove or overwrite existing slots
    - Promotion must NOT depend on current-turn presence alone
    - If start_date exists in merged slots, it must remain even if no date_roles appear this turn
    - date_roles may ADD meaning but must not be required to PRESERVE slots
    - Promotion logic must be safe to run repeatedly with no side effects

    This runs BEFORE computing missing_slots but is NEVER persisted.
    Promotion rules are intent-scoped and role-aware.

    Args:
        raw_slots: Merged slots (session slots + luma slots) - durable facts that must be preserved
        intent_name: Intent name for promotion rules
        context: Context from Luma response (for date_roles, etc.)

    Returns:
        Promoted slots dict (raw_slots + promoted slots, non-persistent)
        All input slots are preserved - promotion is additive only
    """
    import logging

    logger = logging.getLogger(__name__)

    # CRITICAL: Start with copy of ALL existing merged slots - promotion is additive, never destructive
    promoted = raw_slots.copy() if raw_slots else {}

    if intent_name == "CREATE_RESERVATION":
        # Promotion rules for reservations
        date_roles = context.get("date_roles", []) if context else []

        # date_range → start_date + end_date (ADD only if both present AND slots don't already exist)
        date_range = raw_slots.get("date_range")
        if isinstance(date_range, dict):
            range_start = date_range.get("start")
            range_end = date_range.get("end")
            if range_start and range_end:
                if "start_date" not in promoted:
                    promoted["start_date"] = range_start
                if "end_date" not in promoted:
                    promoted["end_date"] = range_end

        # date → start_date (ADD only if date_roles explicitly indicates START_DATE AND start_date doesn't exist)
        if "date" in raw_slots and "START_DATE" in date_roles:
            if "start_date" not in promoted:
                promoted["start_date"] = raw_slots["date"]

        # date → end_date (ADD only if date_roles explicitly indicates END_DATE AND end_date doesn't exist)
        if "date" in raw_slots and "END_DATE" in date_roles:
            if "end_date" not in promoted:
                promoted["end_date"] = raw_slots["date"]

    elif intent_name == "CREATE_APPOINTMENT":
        # Promotion rules for service appointments
        # date_range → date (ADD only if date not already present)
        if "date_range" in raw_slots and "date" not in promoted:
            date_range = raw_slots.get("date_range")
            if isinstance(date_range, dict):
                promoted["date"] = (
                    date_range.get("start") or date_range.get("value") or date_range
                )
            else:
                promoted["date"] = date_range

        # date + time → has_datetime (for execution readiness)
        if "has_datetime" not in promoted:
            if ("date" in promoted and "time" in raw_slots) or (
                "date" in raw_slots and "time" in raw_slots
            ):
                promoted["has_datetime"] = True

    # CRITICAL: Verify all input slots are preserved
    input_slot_keys = set(raw_slots.keys()) if raw_slots else set()
    promoted_slot_keys = set(promoted.keys())
    if input_slot_keys:
        lost_slots = input_slot_keys - promoted_slot_keys
        if lost_slots:
            logger.error(
                f"[PROMOTION] VIOLATION: Slots lost during promotion! "
                f"Lost slots: {list(lost_slots)}, "
                f"input_slots={list(input_slot_keys)}, "
                f"promoted_slots={list(promoted_slot_keys)}"
            )
            # Restore lost slots (fail-safe)
            for key in lost_slots:
                promoted[key] = raw_slots[key]

    return promoted


__all__ = [
    "compute_missing_slots",
    "get_planning_required_slots_for_intent",
    "get_required_slots_for_intent",
    "filter_slots_by_domain",
    "filter_collected_slots_for_intent",
    "promote_slots_for_intent",
]
