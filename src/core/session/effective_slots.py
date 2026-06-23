"""
Session state merge and persistence.

Extracted from core.orchestration.api.session_merge for maintainability.
"""

import json
import logging
import os
from typing import Any, Dict, Optional

from core.orchestration.persistence.durable_intents import (
    filter_slots_for_intent,
    is_durable_intent,
)
from core.session.schema import debug_persistence_enabled

logger = logging.getLogger(__name__)


def _compute_effective_collected_slots_internal(
    promoted_slots: Dict[str, Any], effective_intent: str, planning_only: bool = False
) -> Dict[str, Any]:
    """
    Compute effective collected slots from promoted slots.

    CRITICAL: Domain slot isolation is enforced here:
    - Filter slots by domain BEFORE computing effective_collected_slots
    - Prevent service_id from appearing in reservation slots if not valid

    This is the internal implementation used by both merge_luma_with_session
    and when there's no session (first turn).

    Args:
        promoted_slots: Promoted slots (after promotion rules applied)
        effective_intent: Intent name for determining required slots

    Returns:
        Dictionary of effective collected slots (slots that satisfy required slots)
    """
    from core.orchestration.api.slot_contract import (
        filter_slots_by_domain,
        get_required_slots_for_intent,
    )

    # CRITICAL: Filter slots by domain BEFORE computing effective_collected_slots
    # This prevents cross-domain slot leakage (e.g., service_id in reservation missing_slots)
    # PLANNING-ONLY: Skip domain filtering when planning_only=True
    domain_filtered_slots = filter_slots_by_domain(
        promoted_slots, effective_intent, planning_only=planning_only
    )

    # Slots are treated as an unordered, additive map
    # No special routing needed - users can provide missing slots in any order
    effective_slots_for_filtering = domain_filtered_slots

    required_slots_set = set(get_required_slots_for_intent(effective_intent))

    if debug_persistence_enabled():
        print(
            f"  effective_slots_for_filtering.keys()={list(effective_slots_for_filtering.keys())}"
        )
        print(f"  promoted_slots={promoted_slots}")

    # Filter to only slots that satisfy required slots
    effective_collected_slots = {
        slot_name: slot_value
        for slot_name, slot_value in effective_slots_for_filtering.items()
        if slot_name in required_slots_set and slot_value is not None
    }

    # Also include service_id if present (common across intents)
    # But only if it's valid for the domain (already filtered by filter_slots_by_domain)
    if (
        "service_id" in effective_slots_for_filtering
        and effective_slots_for_filtering["service_id"] is not None
    ):
        effective_collected_slots["service_id"] = effective_slots_for_filtering[
            "service_id"
        ]

    if debug_persistence_enabled():
        print(f"  effective_collected_slots (after filter)={effective_collected_slots}")
        print(
            f"  effective_collected_slots.keys()={list(effective_collected_slots.keys())}"
        )

    return effective_collected_slots


def _compute_effective_collected_slots(
    luma_response: Dict[str, Any], planning_only: bool = False
) -> Dict[str, Any]:
    """
    Compute effective collected slots for a Luma response when there's no session.

    This ensures slots are persisted correctly on the first turn.
    CRITICAL: Also detects and persists modification context for MODIFY_* intents.

    Args:
        luma_response: Luma response (may have slots, intent, context)

    Returns:
        Luma response with _effective_collected_slots added (never None)
    """
    if not luma_response or not isinstance(luma_response, dict):
        # Invalid response - return dict with empty effective slots
        # CRITICAL: Always return a dict, never None
        return {"_effective_collected_slots": {}}

    # Extract intent
    intent_obj = luma_response.get("intent", {})
    intent_name = intent_obj.get("name", "") if isinstance(intent_obj, dict) else ""

    # TRACE 1: Immediately after intent resolution (before any early return)
    # json is already imported at module level (line 10)
    if debug_persistence_enabled():
        print(
            json.dumps(
                {
                    "trace_point": "AFTER_INTENT",
                    "intent": intent_name,
                    "is_first_turn": True,
                    "raw_luma_slots": luma_response.get("slots"),
                    "raw_luma_context": luma_response.get("context"),
                }
            )
        )

    if not intent_name:
        # No intent - no effective slots
        luma_response["_effective_collected_slots"] = {}
        return luma_response

    # FACT-ONLY: Promote facts to slots BEFORE any processing
    # This ensures facts.service_id, facts.times, etc. are available for planning
    from core.orchestration.luma_facts_adapter import (
        facts_to_slots,
        merge_promoted_luma_slots,
    )

    facts_obj = luma_response.get("facts", {})
    # Get intent for date_range promotion (CREATE_RESERVATION with 2+ dates)
    intent_obj = luma_response.get("intent", {})
    intent_name = intent_obj.get("name", "") if isinstance(intent_obj, dict) else ""
    promoted_slots_from_facts = (
        facts_to_slots(
            facts_obj,
            intent_name=intent_name,
            source_text=luma_response.get("_source_text"),
        )
        if isinstance(facts_obj, dict)
        else {}
    )

    # Extract slots from facts.facts.slots if present (nested format)
    # Otherwise fall back to legacy slots field
    nested_slots = {}
    if isinstance(facts_obj, dict) and "slots" in facts_obj:
        # Nested format: facts.facts.slots
        nested_slots = facts_obj.get("slots", {})
    else:
        # Legacy format: slots at top level
        nested_slots = luma_response.get("slots", {})

    # Merge promoted slots (from facts.service_id, facts.times, etc.) with nested slots
    # Promoted slots take precedence (they are the source of truth)
    raw_slots = merge_promoted_luma_slots(
        nested_slots,
        promoted_slots_from_facts,
        luma_response.get("date_constraint"),
        facts_obj if isinstance(facts_obj, dict) else None,
        prefer_nested_service_id=True,
    )
    if not isinstance(raw_slots, dict):
        raw_slots = {}

    # STEP: Detect and persist modification context for MODIFY_* intents (BEFORE promotion)
    # CRITICAL: This must run for first turns (no session) to ensure modification context is persisted
    # Modification context is INTENT-DRIVEN, not slot-driven
    if debug_persistence_enabled():
        print(
            f"[_compute_effective_collected_slots] raw_slots keys={list(raw_slots.keys())}"
        )

    modification_context = None
    if intent_name == "MODIFY_BOOKING":
        # Detect modification type from raw_slots (before promotion)
        has_time = "time" in raw_slots and raw_slots.get("time") is not None
        has_date = "date" in raw_slots and raw_slots.get("date") is not None

        # Always set modification context for MODIFY_BOOKING (intent-driven)
        modification_context = {"modifying_time": has_time, "modifying_date": has_date}
        luma_response["_modification_context"] = modification_context

    elif intent_name == "MODIFY_RESERVATION":
        # Detect modification type from raw_slots (before promotion)
        has_start_date = (
            "start_date" in raw_slots and raw_slots.get("start_date") is not None
        )
        has_end_date = "end_date" in raw_slots and raw_slots.get("end_date") is not None
        has_date = "date" in raw_slots and raw_slots.get("date") is not None

        # Always set modification context for MODIFY_RESERVATION (intent-driven)
        modification_context = {
            "modifying_start_date": has_start_date,
            "modifying_end_date": has_end_date,
            "modifying_date": has_date,
        }
        luma_response["_modification_context"] = modification_context

    # Get context for promotion
    context = luma_response.get("context", {})
    if not isinstance(context, dict):
        context = {}

    # Promote slots
    from core.orchestration.api.slot_contract import promote_slots_for_intent

    promoted_slots = promote_slots_for_intent(raw_slots, intent_name, context)

    # CRITICAL: Promotion MUST write into slots that will be persisted
    # After promotion, merge promoted slots back into luma_response["slots"] so they get persisted
    # This ensures promoted slots (e.g., date_range → start_date, end_date) are durable
    luma_response["slots"] = promoted_slots

    # Compute effective collected slots (for backward compatibility, not used for missing_slots)
    # No session on first turn - planner handles missing_slots computation
    effective_collected_slots = _compute_effective_collected_slots_internal(
        promoted_slots, intent_name, planning_only=planning_only
    )

    # TRACE 2: After modification context detection (both paths)
    # json is already imported at module level (line 10)
    if debug_persistence_enabled():
        print(
            json.dumps(
                {
                    "trace_point": "AFTER_MOD_CONTEXT",
                    "intent": intent_name,
                    "modification_context": luma_response.get("_modification_context"),
                    "promoted_slots": promoted_slots,
                    "effective_collected_slots": effective_collected_slots,
                }
            )
        )

    # CRITICAL: Also compute missing_slots for first turns (no session)
    # ARCHITECTURAL INVARIANT: missing_slots = REQUIRED_SLOTS(intent) - durable_slots.keys()
    # missing_slots must be computed from durable slots (after promotion writes into slots)
    # A slot is satisfied ONLY if it exists in durable_slots under its exact slot name
    # Use planner for missing_slots computation
    from core.planning.policy.action_policy import load_planning_policy, plan_intent

    # BEFORE_REQUIRED_SLOTS: Log right before required-slot computation (first turn)
    before_required_slots_log = {
        "trace": "BEFORE_REQUIRED_SLOTS",
        "intent": intent_name,
        "slots_used": promoted_slots,
        "session_slots": None,  # No session on first turn
        "modification_context": modification_context,
    }
    logger.info(
        "BEFORE_REQUIRED_SLOTS: %s",
        json.dumps(before_required_slots_log, ensure_ascii=False, default=str),
    )
    if debug_persistence_enabled():
        print(
            f"\n[BEFORE_REQUIRED_SLOTS] {json.dumps(before_required_slots_log, ensure_ascii=False, default=str)}"
        )

    # HARD INVARIANT CHECK (test/debug only):
    # This must run at the exact entry point of required-slot computation
    # Note: raw_slots was captured before promotion (line 1679), so use it as raw_luma_slots
    # Note: os is already imported at module level (line 12)
    if os.getenv("PYTEST_CURRENT_TEST") or os.getenv("DEBUG_SLOT_DROP") == "1":
        # Capture variables: raw_luma_slots, merged_slots, session_slots, intent
        # raw_slots is the original Luma slots before promotion (captured at line 1679)
        raw_luma_slots = raw_slots.copy() if isinstance(raw_slots, dict) else {}

        merged_slots = promoted_slots
        if not isinstance(merged_slots, dict):
            merged_slots = {}

        session_slots = {}  # No session on first turn

        intent = intent_name

        # INVARIANT CHECK: If raw_luma_slots is not empty AND merged_slots is empty OR missing any key from raw_luma_slots
        if raw_luma_slots:
            merged_slots_keys = set(merged_slots.keys())
            raw_luma_slots_keys = set(raw_luma_slots.keys())
            missing_keys = raw_luma_slots_keys - merged_slots_keys

            if not merged_slots or missing_keys:
                error_msg = (
                    f"INVARIANT VIOLATION: Luma slots dropped before required-slot computation\n"
                    f"  raw_luma_slots: {raw_luma_slots}\n"
                    f"  merged_slots: {merged_slots}\n"
                    f"  session_slots: {session_slots}\n"
                    f"  intent: {intent}\n"
                    f"  missing_keys: {list(missing_keys) if missing_keys else 'merged_slots is empty'}"
                )
                logger.error(f"[HARD_INVARIANT] {error_msg}")
                print(f"\n[HARD_INVARIANT] {error_msg}")
                # Do NOT swallow this error - let the test crash
                raise Exception(error_msg)

    # Use planner to compute missing_slots
    policy = load_planning_policy()
    from core.orchestration.temporal_proposal import (
        expand_slots_for_planning,
        extract_nlu_proposals,
        merge_session_proposals,
    )

    _nlu_props = extract_nlu_proposals(luma_response)
    _merged_props = merge_session_proposals(None, _nlu_props["date_proposal"], _nlu_props["time_proposal"])
    luma_response["date_proposal"] = _merged_props["date_proposal"]
    luma_response["time_proposal"] = _merged_props["time_proposal"]

    _facts_for_planning = luma_response.get("facts")
    if not isinstance(_facts_for_planning, dict):
        _facts_for_planning = None
    planning_slots = expand_slots_for_planning(
        promoted_slots,
        date_proposal=luma_response.get("date_proposal"),
        time_proposal=luma_response.get("time_proposal"),
        date_constraint=luma_response.get("date_constraint"),
        nlu_facts=_facts_for_planning,
        time_constraint=luma_response.get("time_constraint"),
        intent_name=intent_name,
    )
    plan = plan_intent(intent_name, planning_slots, policy)
    missing_slots = plan["missing_slots"]

    from core.orchestration.temporal_proposal import apply_time_constraint_to_missing_slots

    missing_slots = apply_time_constraint_to_missing_slots(
        intent_name, missing_slots, luma_response.get("time_constraint")
    )

    # Normalize MODIFY_BOOKING missing_slots (test contract)
    from core.orchestration.nlu.luma_response_processor import (
        _normalize_modify_booking_missing_slots,
    )

    missing_slots = _normalize_modify_booking_missing_slots(
        missing_slots, luma_response
    )

    # INVARIANT CHECK: missing_slots must be a list
    assert isinstance(
        missing_slots, list
    ), f"missing_slots must be a list, got {type(missing_slots)}: {missing_slots}"

    # INVARIANT CHECK: missing_slots must never be None after computation
    assert missing_slots is not None, "missing_slots must not be None after computation"

    # Store in response
    luma_response["_effective_collected_slots"] = effective_collected_slots
    luma_response["missing_slots"] = missing_slots

    # LOG: computed missing_slots for first turn
    # Note: logger is already defined at module level (line 14), no need to redefine
    logger.info(
        f"[MISSING_SLOTS] Computed missing_slots for first turn: intent={intent_name}, missing_slots={missing_slots}"
    )
    return luma_response


