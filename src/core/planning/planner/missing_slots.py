"""
Missing Slots Computation

Policy requirement lookup helpers for Planning.
Canonical effective missing_slots are owned by turn_state.finalize_turn_state().
"""

import logging
from typing import Any, Dict, List, Mapping, Optional, Sequence

logger = logging.getLogger(__name__)


def append_business_required_slots(
    platform_slots: Sequence[str],
    entity_schema: Optional[Mapping[str, Any]] = None,
) -> List[str]:
    """Append schema-required business slot keys to a platform required list.

    Platform order is preserved. Business keys follow entity_schema declaration
    order and skip duplicates already present in ``platform_slots``.
    Shared by planning and execution requiredness composition.
    """
    from core.adapters.nlu.entity_schema_builder import (
        required_slot_keys_from_entity_schema,
    )

    platform = [str(slot) for slot in platform_slots]
    business = required_slot_keys_from_entity_schema(entity_schema)
    if not business:
        return platform

    seen = set(platform)
    composed = list(platform)
    for key in business:
        if key in seen:
            continue
        composed.append(key)
        seen.add(key)
    return composed


def compose_planning_required_slots(
    intent_name: str,
    *,
    entity_schema: Optional[Mapping[str, Any]] = None,
) -> List[str]:
    """Compose effective planning required slots for an intent.

    Platform order from intent_policy is preserved. Required business entity
    slot keys from the active entity_schema are appended in declaration order,
    skipping keys already present in the platform list.
    """
    from core.policy.intent_policy import get_planning_required_slots

    platform = list(get_planning_required_slots(intent_name))
    return append_business_required_slots(platform, entity_schema)


def get_planning_required_slots_for_intent(
    intent_name: str,
    collected_slots: Dict[str, Any] = None,
    modification_context: Dict[str, Any] = None,
    *,
    entity_schema: Optional[Mapping[str, Any]] = None,
) -> List[str]:
    """
    Get planning-required slots for an intent.

    Composes platform required_slots from intent_policy.yaml with required
    business slots from the active entity_schema. Callers see one ordered list.
    """
    _ = collected_slots, modification_context
    try:
        required_slots = compose_planning_required_slots(
            intent_name, entity_schema=entity_schema
        )
        logger.debug(
            "[REQUIRED_SLOTS_COMPUTE] intent=%s required_slots=%s "
            "(platform + entity_schema)",
            intent_name,
            required_slots,
        )
        return list(required_slots)
    except (ImportError, KeyError, Exception) as e:
        logger.warning(
            "[REQUIRED_SLOTS_COMPUTE] Failed to get required_slots from intent_policy.yaml for %s: %s. "
            "Falling back to legacy planning config.",
            intent_name,
            e,
        )
        from core.planning.policy.action_policy import load_planning_policy

        policy = load_planning_policy()
        intent_policy = policy.get(intent_name, {})
        required_slots = intent_policy.get("required_slots", [])
        if not isinstance(required_slots, list):
            required_slots = []
        return list(required_slots)


def append_required_availability_criteria_slots(
    platform_slots: Sequence[str],
    entity_schema: Optional[Mapping[str, Any]] = None,
) -> List[str]:
    """Append required schema slots that participate in availability identity.

    Used for exploratory SEARCH gating. ``required`` alone never gates SEARCH;
    only the intersection with effective ``availability_criteria`` does.
    """
    from core.adapters.nlu.entity_schema_builder import (
        required_slot_keys_from_entity_schema,
        search_criteria_slot_keys_from_entity_schema,
    )

    platform = [str(slot) for slot in platform_slots]
    availability_keys = search_criteria_slot_keys_from_entity_schema(entity_schema)
    required_keys = required_slot_keys_from_entity_schema(entity_schema)
    if not availability_keys or not required_keys:
        return platform

    seen = set(platform)
    composed = list(platform)
    for key in required_keys:
        if key not in availability_keys or key in seen:
            continue
        composed.append(key)
        seen.add(key)
    return composed


def compose_execution_step_required_slots(
    *,
    intent_name: str,
    step_required_slots: Sequence[str],
    mode: str,
    entity_schema: Optional[Mapping[str, Any]] = None,
) -> List[str]:
    """Effective required slots for an execution step.

    - Committing steps: same composed planning requiredness as the planner.
    - Exploratory steps: platform step.required_slots plus required schema
      slots whose effective ``availability_criteria`` is true. Non-availability
      business attributes (e.g. registration_number) never gate SEARCH.
    """
    if (mode or "").lower() == "committing":
        return compose_planning_required_slots(
            intent_name, entity_schema=entity_schema
        )
    platform = [str(slot) for slot in (step_required_slots or [])]
    return append_required_availability_criteria_slots(platform, entity_schema)


def normalize_modify_booking_missing_slots(
    missing_slots: List[str],
    *,
    intent_name: str = "",
) -> List[str]:
    """
    Normalize MODIFY_BOOKING missing_slots to the planning contract.

    Preserves planning-required slots (booking_id, date) and filters execution-specific
    temporal names that must not appear in planning missing_slots.
    Output order follows planning.required_slots when possible.
    """
    if intent_name != "MODIFY_BOOKING":
        return missing_slots

    planning_slots = {"booking_id", "date"}
    filtered_execution = {
        "change",
        "time",
        "start_date",
        "end_date",
        "datetime_range",
        "date_range",
    }

    kept: List[str] = []
    for slot in missing_slots:
        if slot in planning_slots:
            kept.append(slot)
        elif slot not in filtered_execution:
            kept.append(slot)

    if not kept:
        return missing_slots

    required = get_planning_required_slots_for_intent(intent_name)
    kept_set = set(kept)
    ordered = [slot for slot in required if slot in kept_set]
    for slot in kept:
        if slot not in ordered:
            ordered.append(slot)
    return ordered


def derive_ask_next(
    missing_slots: List[str],
    promptable_slots: Optional[List[str]] = None,
) -> Optional[str]:
    """Next clarification ask: required missing first, then promptable optional."""
    if missing_slots:
        return str(missing_slots[0])
    if promptable_slots:
        return str(promptable_slots[0])
    return None
