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
from core.session.schema import (
    debug_log,
    debug_persistence_enabled,
    filter_serializable_facts,
    normalize_session_guards,
)

logger = logging.getLogger(__name__)
turn_logger = logging.getLogger("core.turn_log")
turn_logger.setLevel(logging.INFO)


from core.session.appointment_extensions import apply_create_appointment_extensions
from core.session.intent_persist import (
    resolve_durable_intent_for_session,
    resolve_final_intent_name,
    should_clear_session_on_ready,
)
from core.session.missing_slots import resolve_missing_slots_for_persist

def build_session_state_from_outcome(
    outcome: Dict[str, Any],
    outcome_status: str,
    merged_luma_response: Optional[Dict[str, Any]] = None,
    previous_session_state: Optional[Dict[str, Any]] = None,
    user_id: Optional[str] = None,
    session_store: Optional[Any] = None,
) -> Optional[Dict[str, Any]]:
    """
    Build session state from outcome and merged Luma response.

    CONTRACT: Persist conversation state, not just slots:
    - Slots are treated as unordered, additive map (awaiting_slot removed)
    - Always save session.missing_slots (recomputed from effective_collected_slots) for continuity/debugging

    CRITICAL: missing_slots is recomputed from effective_collected_slots (post-promotion)
    and is the ONLY source of truth. NO later code must overwrite it with raw/filtered missing slots.

    Args:
        outcome: Outcome dictionary from handle_message
        outcome_status: Outcome status ("READY" | "NEEDS_CLARIFICATION" | "AWAITING_CONFIRMATION")
        merged_luma_response: Optional merged Luma response (for extracting intent)
        previous_session_state: Optional previous session state (unused, for compatibility)

    Returns:
        Session state dictionary (WITH missing_slots if present) or None if status is READY
    """
    # SESSION LIFECYCLE RULE: Clear session only on EXECUTED status
    # EXECUTED means the intent was successfully executed and the session should be cleared
    # READY status does NOT clear session - it means slots are ready but execution hasn't happened yet
    # Empty/error Luma responses should preserve session state regardless of status

    # EXECUTED status always clears session
    if outcome_status == "EXECUTED":
        return None

    # If merged_luma_response is None (empty/error Luma response), preserve previous session state
    # This handles cases where Luma API errors or empty responses occur
    # The orchestrator should have already handled this case, but we need to ensure session is preserved
    if merged_luma_response is None and previous_session_state:
        # Preserve session state from previous turn - don't clear on empty/error responses
        # Return the previous session state as-is (or build new state from outcome if it has intent)
        # Check if outcome has a valid intent - if so, use it; otherwise preserve previous
        if outcome and isinstance(outcome, dict):
            outcome_intent = outcome.get("intent_name") or outcome.get("intent")
            if outcome_intent and outcome_intent not in ("", "UNKNOWN"):
                # Outcome has valid intent - continue building session state normally
                pass
            else:
                # Outcome has no valid intent - preserve previous session state
                # Return previous session state to preserve it
                return previous_session_state

    # Guard: outcome must be a dict
    if not outcome or not isinstance(outcome, dict):
        print(
            f"[ERROR] build_session_state_from_outcome: outcome is None or not a dict: {outcome}"
        )
        return None

    # DIAGNOSTIC: Log what's in outcome and merged_luma_response at entry
    logger.debug(
        f"[SESSION_MERGE] build_session_state_from_outcome entry: "
        f"outcome_keys={list(outcome.keys())}, "
        f"outcome.slots_keys={list(outcome.get('slots', {}).keys()) if isinstance(outcome.get('slots'), dict) else 'N/A'}, "
        f"outcome.facts.slots_keys={list(outcome.get('facts', {}).get('slots', {}).keys()) if isinstance(outcome.get('facts'), dict) and isinstance(outcome.get('facts', {}).get('slots'), dict) else 'N/A'}, "
        f"merged_luma_response_keys={list(merged_luma_response.keys()) if merged_luma_response and isinstance(merged_luma_response, dict) else 'N/A'}, "
        f"merged_luma_response.slots_keys={list(merged_luma_response.get('slots', {}).keys()) if merged_luma_response and isinstance(merged_luma_response, dict) and isinstance(merged_luma_response.get('slots'), dict) else 'N/A'}, "
        f"merged_luma_response._effective_collected_slots_keys={list(merged_luma_response.get('_effective_collected_slots', {}).keys()) if merged_luma_response and isinstance(merged_luma_response, dict) and isinstance(merged_luma_response.get('_effective_collected_slots'), dict) else 'N/A'}"
    )

    # Guard: outcome must have intent_name or intent (required for session state)
    # Check early to prevent building invalid session state
    if "intent_name" not in outcome and "intent" not in outcome:
        print(
            f"[ERROR] build_session_state_from_outcome: outcome has no intent_name or intent: {outcome}"
        )
        return None

    # Extract facts (contains slots, missing_slots, and capability facts like payment_satisfied)
    # Facts are a first-class, durable part of session state (same status as slots)
    outcome_facts = outcome.get("facts", {})
    if not isinstance(outcome_facts, dict):
        outcome_facts = {}

    # Merge with previous session facts (new facts override old keys)
    # This ensures capability facts (e.g., payment_satisfied) persist across turns
    previous_facts = {}
    if previous_session_state and isinstance(previous_session_state, dict):
        previous_facts = previous_session_state.get("facts", {})
        if not isinstance(previous_facts, dict):
            previous_facts = {}

    # Merge: new facts from outcome override old facts from session
    # This allows capabilities to update facts (e.g., payment_satisfied: True)
    merged_facts = {**previous_facts, **outcome_facts}

    # Extract facts for backward compatibility (used for slots extraction below)
    facts = merged_facts

    # ARCHITECTURAL INVARIANT: Persist ALL collected slots, not only effective or required ones
    # session.slots is the single source of truth for collected slots
    # Slots present in session MUST be preserved across turns unless intent changes
    # Do NOT filter slots using REQUIRED_SLOTS during persistence
    # Do NOT drop slots because they were not mentioned this turn

    # Get ALL merged slots from merged_luma_response (session slots + luma slots)
    # These are the durable facts that must be preserved
    # FALLBACK: For empty/null Luma responses, use outcome.slots (session state reused as-is)
    slots = {}
    try:
        if merged_luma_response and isinstance(merged_luma_response, dict):
            slots = merged_luma_response.get("slots", {})
            if not isinstance(slots, dict):
                slots = {}

            # DIAGNOSTIC: Log what's available in merged_luma_response
            logger.debug(
                f"[SESSION_MERGE] merged_luma_response keys: {list(merged_luma_response.keys())}, "
                f"slots keys: {list(slots.keys()) if isinstance(slots, dict) else 'N/A'}, "
                f"_effective_collected_slots keys: {list(merged_luma_response.get('_effective_collected_slots', {}).keys()) if isinstance(merged_luma_response.get('_effective_collected_slots'), dict) else 'N/A'}"
            )

            # FALLBACK: If slots is empty but _effective_collected_slots has data, use it
            # This handles cases where slots weren't populated but effective_collected_slots exists
            if not slots and merged_luma_response.get("_effective_collected_slots"):
                effective_slots = merged_luma_response.get(
                    "_effective_collected_slots", {}
                )
                if isinstance(effective_slots, dict) and effective_slots:
                    slots = effective_slots
                    logger.info(
                        f"[SESSION_MERGE] Using _effective_collected_slots as fallback (slots was empty): {list(slots.keys())}"
                    )
    except Exception as e:
        print(
            f"[ERROR] build_session_state_from_outcome: Exception accessing merged_luma_response: {e}"
        )
        print(f"  merged_luma_response type: {type(merged_luma_response)}")
        print(f"  merged_luma_response: {merged_luma_response}")
        slots = {}

    # FALLBACK: If merged_luma_response is None (empty/null Luma response), use outcome.slots
    # This handles the case where empty response handler reuses session state as-is
    if not slots and outcome and isinstance(outcome, dict):
        outcome_slots = outcome.get("slots", {})
        if isinstance(outcome_slots, dict) and outcome_slots:
            slots = outcome_slots
            print(
                f"[SESSION_MERGE] Using outcome.slots as fallback (merged_luma_response is None): {list(slots.keys())}"
            )

    intent_name = resolve_durable_intent_for_session(
        outcome, previous_session_state, outcome_status
    )

    should_clear, _clear_reason = should_clear_session_on_ready(outcome_status, intent_name)
    if should_clear:
        return None

    # Resolve missing_slots (single source of truth)
    recomputed_missing_slots = resolve_missing_slots_for_persist(
        outcome,
        intent_name,
        slots,
        merged_luma_response,
        previous_session_state,
    )


    # Determine status
    status = (
        "NEEDS_CLARIFICATION"
        if outcome_status
        in ("NEEDS_CLARIFICATION", "AWAITING_CONFIRMATION", "AWAITING_CAPABILITY")
        else "READY"
    )

    # Build session state WITH missing_slots (for conversation continuity)
    # missing_slots are recomputed from effective_collected_slots (post-promotion) and persisted
    # awaiting_slot removed - slots are treated as unordered, additive map
    # slots already filtered to raw slots above

    # Persist date_roles in context if available (for derivation layer next turn)
    # This ensures date → start_date derivation works correctly across turns when date_roles is present
    context_with_roles = {}

    # First check merged_luma_response for date_roles (current turn)
    if merged_luma_response:
        merged_context = merged_luma_response.get("context", {})
        if isinstance(merged_context, dict) and "date_roles" in merged_context:
            context_with_roles["date_roles"] = merged_context["date_roles"]

    # Also preserve date_roles from previous session if present (for persistence across turns)
    if previous_session_state:
        prev_context = previous_session_state.get("context", {})
        if isinstance(prev_context, dict) and "date_roles" in prev_context:
            # Merge previous date_roles (they persist until explicitly changed)
            if not context_with_roles:
                context_with_roles = {}
            context_with_roles["date_roles"] = prev_context["date_roles"]

    # Handle awaiting_slot persistence (optional, backward compatible)
    # Read awaiting_slot from previous_session_state if present (before computing missing_slots)
    awaiting_slot = None
    if previous_session_state and isinstance(previous_session_state, dict):
        awaiting_slot = previous_session_state.get("awaiting_slot")

    # Handle slot_attempts persistence (optional, backward compatible)
    # session_state["slot_attempts"] is the ONLY source of truth (set by orchestrator)
    # Read slot_attempts ONLY from previous_session_state (never from outcome.facts)
    # Always ensure slot_attempts is a dict (defaults to empty dict)
    slot_attempts = {}
    if previous_session_state and isinstance(previous_session_state, dict):
        slot_attempts = previous_session_state.get("slot_attempts", {})
        if not isinstance(slot_attempts, dict):
            slot_attempts = {}

    # Ensure slot_attempts is always a dict (shape safety)
    if not isinstance(slot_attempts, dict):
        slot_attempts = {}

    # Determine missing_slots to persist (recomputed from effective_collected_slots)
    missing_slots_to_persist = (
        recomputed_missing_slots if recomputed_missing_slots is not None else []
    )

    # --- Detect last_filled_slot (pure metadata, no behavioral impact) ---
    last_filled_slot = None

    if merged_luma_response and isinstance(merged_luma_response, dict):
        raw_luma_slots = merged_luma_response.get("_raw_luma_slots", {})
        if isinstance(raw_luma_slots, dict) and raw_luma_slots:
            previous_slots = (
                previous_session_state.get("slots", {})
                if previous_session_state
                else {}
            )
            if isinstance(previous_slots, dict):
                new_or_changed = [
                    slot_name
                    for slot_name, slot_value in raw_luma_slots.items()
                    if slot_name not in previous_slots
                    or previous_slots.get(slot_name) != slot_value
                ]
                if new_or_changed:
                    # Python 3.7+ preserves insertion order
                    last_filled_slot = new_or_changed[-1]

    # Clear awaiting_slot if it is no longer missing or if it appears in outcome slot sources
    # Check multiple authoritative slot sources used by planning/turn_state
    if awaiting_slot:
        slot_filled = False
        source_used = None

        # Check 1: outcome.slots (top-level slots)
        outcome_slots = (
            outcome.get("slots", {}) if outcome and isinstance(outcome, dict) else {}
        )
        if isinstance(outcome_slots, dict) and awaiting_slot in outcome_slots:
            slot_filled = True
            source_used = "outcome.slots"

        # Check 2: outcome.facts.slots (facts container slots)
        if not slot_filled and outcome and isinstance(outcome, dict):
            outcome_facts = outcome.get("facts", {})
            if isinstance(outcome_facts, dict):
                facts_slots = outcome_facts.get("slots", {})
                if isinstance(facts_slots, dict) and awaiting_slot in facts_slots:
                    slot_filled = True
                    source_used = "outcome.facts.slots"

        # Check 3: outcome.facts directly (some facts store top-level slot values)
        if not slot_filled and outcome and isinstance(outcome, dict):
            outcome_facts = outcome.get("facts", {})
            if isinstance(outcome_facts, dict) and awaiting_slot in outcome_facts:
                slot_filled = True
                source_used = "outcome.facts"

        # Check 4: missing_slots_to_persist (slot is no longer missing)
        if not slot_filled and awaiting_slot not in missing_slots_to_persist:
            slot_filled = True
            source_used = "missing_slots_to_persist (no longer missing)"

        # Clear awaiting_slot if filled from any source
        if slot_filled:
            logger.info(
                f"[AWAITING_SLOT] Clearing awaiting_slot='{awaiting_slot}' "
                f"because slot is filled (detected in {source_used})"
            )
            awaiting_slot = None
            # Also clear slot_attempts counter for this slot (retry tracking reset)
            if slot_attempts and isinstance(slot_attempts, dict):
                slot_attempts.pop(awaiting_slot, None)
                logger.debug(
                    f"[SLOT_ATTEMPTS] Cleared retry counter for slot '{awaiting_slot}' (slot filled)"
                )

    # Clear slot_attempts for any slots that are no longer missing
    # This ensures retry counters reset when slots are satisfied
    if (
        slot_attempts
        and isinstance(slot_attempts, dict)
        and isinstance(missing_slots_to_persist, list)
    ):
        slots_to_clear = [
            slot_name
            for slot_name in slot_attempts.keys()
            if slot_name not in missing_slots_to_persist
        ]
        for slot_name in slots_to_clear:
            slot_attempts.pop(slot_name, None)
            logger.debug(
                f"[SLOT_ATTEMPTS] Cleared retry counter for slot '{slot_name}' (no longer missing)"
            )

    final_intent_name = resolve_final_intent_name(
        intent_name, outcome, previous_session_state, outcome_status
    )

    # Extract active_capability from previous session only (optional passthrough)
    # Core must never read from its own output (outcome)
    active_capability = None
    if previous_session_state:
        active_capability = previous_session_state.get("active_capability")

    # CRITICAL: Clear active_capability when payment is satisfied
    # This prevents "paid" sessions from re-entering payment capability
    # Check merged_facts for payment_satisfied (capability facts are in merged_facts)
    if active_capability == "payment" and merged_facts:
        payment_satisfied = merged_facts.get("payment_satisfied", False)
        if payment_satisfied:
            logger.info(
                f"[SESSION_PERSISTENCE] Clearing active_capability='payment' because payment_satisfied=True"
            )
            active_capability = None

    serializable_facts = filter_serializable_facts(merged_facts)

    # DURABLE INTENTS RULE: Gate slot persistence by durable intent
    # Only durable intents may be persisted in session
    if final_intent_name and is_durable_intent(final_intent_name):
        # Durable intent: persist intent and filter slots
        filtered_slots = filter_slots_for_intent(final_intent_name, slots)

        # DEBUG: Log slot_attempts before session_state construction
        logger.debug(
            f"[SLOT_ATTEMPTS_DEBUG] BEFORE session_state construction (durable): "
            f"slot_attempts={slot_attempts}, id={id(slot_attempts)}, type={type(slot_attempts)}"
        )

        session_state = {
            "intent_name": final_intent_name,
            "missing_slots": missing_slots_to_persist,
            "slots": filtered_slots,  # Only durable slots for this intent
            # First-class, durable facts (same status as slots)
            "facts": serializable_facts,
            "status": status,
            "active_capability": active_capability,  # optional passthrough
            "awaiting_slot": awaiting_slot,
            "slot_attempts": slot_attempts,
            "last_filled_slot": last_filled_slot,
        }

        # DEBUG: Log session_state after construction
        logger.debug(
            f"[SLOT_ATTEMPTS_DEBUG] AFTER session_state construction (durable): "
            f"session_state['slot_attempts']={session_state.get('slot_attempts')}, "
            f"id={id(session_state.get('slot_attempts'))}, "
            f"same_as_local={id(session_state.get('slot_attempts')) == id(slot_attempts)}"
        )
        logger.debug(
            f"[build_session_state_from_outcome] Persisting durable intent={final_intent_name} "
            f"with {len(filtered_slots)} filtered slots and {len(serializable_facts)} facts"
        )
    else:
        # Ephemeral intent: do NOT persist intent or slots
        # But still persist facts (capability facts may be set even for ephemeral intents)

        # DEBUG: Log slot_attempts before session_state construction
        logger.debug(
            f"[SLOT_ATTEMPTS_DEBUG] BEFORE session_state construction (ephemeral): "
            f"slot_attempts={slot_attempts}, id={id(slot_attempts)}, type={type(slot_attempts)}"
        )

        session_state = {
            "intent_name": None,  # Explicitly None for ephemeral
            "missing_slots": [],
            "slots": {},  # No slot persistence for ephemeral intents
            "facts": serializable_facts,  # Facts persist even for ephemeral intents
            "status": status,
            "active_capability": active_capability,  # optional passthrough
            "awaiting_slot": awaiting_slot,
            "slot_attempts": slot_attempts,
            "last_filled_slot": last_filled_slot,
        }

        # DEBUG: Log session_state after construction
        logger.debug(
            f"[SLOT_ATTEMPTS_DEBUG] AFTER session_state construction (ephemeral): "
            f"session_state['slot_attempts']={session_state.get('slot_attempts')}, "
            f"id={id(session_state.get('slot_attempts'))}, "
            f"same_as_local={id(session_state.get('slot_attempts')) == id(slot_attempts)}"
        )
        logger.debug(
            f"[build_session_state_from_outcome] Ephemeral intent={final_intent_name} - "
            f"NOT persisting intent or slots, but persisting {len(serializable_facts)} facts"
        )

    from core.orchestration.temporal_proposal import resolve_session_proposals

    _persisted_proposals = resolve_session_proposals(
        merged_luma_response=merged_luma_response,
        outcome=outcome,
        previous_session_state=previous_session_state,
    )
    if _persisted_proposals["date_proposal"] is not None:
        session_state["date_proposal"] = _persisted_proposals["date_proposal"]
    if _persisted_proposals["time_proposal"] is not None:
        session_state["time_proposal"] = _persisted_proposals["time_proposal"]
    if merged_luma_response and isinstance(merged_luma_response, dict):
        date_constraint = merged_luma_response.get("date_constraint")
        if date_constraint is not None:
            session_state["date_constraint"] = date_constraint

    normalize_session_guards(session_state)

    logger.debug(
        "[SESSION_WRITE] intent_name=%r status=%r stage=%r action=%r",
        session_state.get("intent_name"),
        session_state.get("status"),
        session_state.get("stage"),
        session_state.get("action"),
    )

    # LOG 4 — SESSION WRITE (immediately after persistence)
    turn_logger.info(
        json.dumps(
            {"turn": "SESSION_WRITE", "session": session_state},
            ensure_ascii=True,
            default=str,
        )
    )

    if merged_luma_response and isinstance(merged_luma_response, dict):
        date_constraint = merged_luma_response.get("date_constraint")
        if date_constraint is not None:
            session_state["date_constraint"] = date_constraint

    apply_create_appointment_extensions(
        session_state,
        final_intent_name,
        outcome,
        merged_luma_response,
        previous_session_state,
        session_store,
        user_id,
    )

    # Before persisting session
    # CRITICAL: Use user_id parameter, not from session_state (session_state is being built, not previous session)
    effective_user_id = (
        user_id
        if user_id
        else (session_state.get("user_id") if isinstance(session_state, dict) else None)
        or "unknown"
    )
    durable_slots_list = list(slots.keys()) if isinstance(slots, dict) else []
    raw_service_id = slots.get("service_id") if isinstance(slots, dict) else None
    canonical_service_id = (
        slots.get("_canonical_service_id") if isinstance(slots, dict) else None
    )
    if os.getenv("DEBUG_SERVICE_ID", "0") == "1":
        logger.debug(
            "[DEBUG_SERVICE_ID] user_id=%s point=BEFORE_PERSIST raw_service_id=%s canonical_service_id=%s",
            effective_user_id,
            raw_service_id,
            canonical_service_id,
        )
    logger.debug(
        "[TRACE_MERGE] user_id=%s point=BEFORE_PERSIST durable_slots=%s raw_service_id=%s canonical_service_id=%s",
        effective_user_id,
        durable_slots_list,
        raw_service_id,
        canonical_service_id,
    )

    # Persist modification context for MODIFY_* intents (allows context-aware required slot derivation)
    # This is persisted separately from slots to enable required slot inference even when slots are empty
    modification_context = (
        merged_luma_response.get("_modification_context")
        if merged_luma_response
        else None
    )
    if modification_context:
        session_state["_modification_context"] = modification_context

    # Store context metadata if needed (for next turn's derivation layer)
    if context_with_roles:
        session_state["context"] = context_with_roles

    # awaiting_slot removed - slots are treated as unordered, additive map

    # CRITICAL: Structured snapshot log + invariant checks at exact point of session persistence
    # This runs after plan/outcome is finalized, before returning response
    # Only runs in test/debug mode
    # json is already imported at module level (line 10)
    if os.getenv("DEBUG_SESSION_PERSISTENCE") == "1" or os.getenv(
        "PYTEST_CURRENT_TEST"
    ):
        # Get all required data for snapshot
        final_intent = intent_name
        final_status = status
        final_missing_slots = session_state.get("missing_slots", [])

        # Compute effective_collected_slots from persisted slots
        effective_collected_slots = {}
        if final_intent:
            from core.planning.orchestration.missing_slots import (
                get_planning_required_slots_for_intent as get_required_slots_for_intent,
            )

            required_slots_set = set(get_required_slots_for_intent(final_intent))
            effective_collected_slots = {
                slot_name: slot_value
                for slot_name, slot_value in slots.items()
                if slot_name in required_slots_set and slot_value is not None
            }
            # Also include service_id if present (common across intents)
            if "service_id" in slots and slots["service_id"] is not None:
                effective_collected_slots["service_id"] = slots["service_id"]

        # Get required_slots for intent
        required_slots_list = []
        if final_intent:
            from core.planning.orchestration.missing_slots import (
                get_planning_required_slots_for_intent as get_required_slots_for_intent,
            )

            required_slots_list = get_required_slots_for_intent(final_intent)

        # STRUCTURED SNAPSHOT LOG
        persistence_snapshot = {
            "intent": final_intent,
            "status": final_status,
            "missing_slots": final_missing_slots,
            "effective_collected_slots": {
                "keys": list(effective_collected_slots.keys()),
                "values": {
                    k: str(v)[:50] for k, v in effective_collected_slots.items()
                },
            },
            "required_slots": required_slots_list,
        }

        logger.info(
            f"[SESSION_PERSISTENCE_SNAPSHOT] Final session state before persistence: "
            f"intent={final_intent}, status={final_status}, "
            f"missing_slots={final_missing_slots}"
        )
        print(
            f"[SESSION_PERSISTENCE_SNAPSHOT] {json.dumps(persistence_snapshot, indent=2)}"
        )

        # INVARIANT CHECKS (only in test/debug mode)
        invariant_violations = []

        # 2) any(slot in missing_slots for slot in effective_collected_slots.keys()) -> INVALID
        overlapping_slots = [
            slot
            for slot in final_missing_slots
            if slot in effective_collected_slots.keys()
        ]
        if overlapping_slots:
            violation_msg = (
                f"INVARIANT VIOLATION 2: Slots present in both missing_slots and effective_collected_slots: "
                f"{overlapping_slots}. Slots in effective_collected_slots must not be in missing_slots."
            )
            invariant_violations.append(violation_msg)
            logger.error(f"[SESSION_PERSISTENCE_INVARIANT] {violation_msg}")
            print(f"[SESSION_PERSISTENCE_INVARIANT] {violation_msg}")

        # awaiting_slot removed - no longer needed for invariant checks

        # 5) persisted.missing_slots != recomputed missing_slots -> INVALID
        # Get recomputed_missing_slots from outcome facts (the authoritative source)
        facts_missing_slots = None
        if "facts" in outcome and isinstance(outcome["facts"], dict):
            facts_missing_slots = outcome["facts"].get("missing_slots")
        elif "recomputed_missing_slots" in locals():
            facts_missing_slots = recomputed_missing_slots

        if facts_missing_slots is not None and isinstance(facts_missing_slots, list):
            if set(final_missing_slots) != set(facts_missing_slots):
                violation_msg = (
                    f"INVARIANT VIOLATION 5: persisted.missing_slots={final_missing_slots} "
                    f"does not match recomputed missing_slots={facts_missing_slots}. "
                    f"They must be equal (the same list used by plan/outcome)."
                )
                invariant_violations.append(violation_msg)
                logger.error(f"[SESSION_PERSISTENCE_INVARIANT] {violation_msg}")
                print(f"[SESSION_PERSISTENCE_INVARIANT] {violation_msg}")

        # Raise error if any violations found (only in test/debug mode)
        if invariant_violations and os.getenv("PYTEST_CURRENT_TEST"):
            # In test mode, raise assertion to fail the test
            raise AssertionError(
                f"Session persistence invariant violations:\n"
                + "\n".join(f"  - {v}" for v in invariant_violations)
            )
        elif invariant_violations:
            # In debug mode, just log the error
            logger.error(
                f"[SESSION_PERSISTENCE_INVARIANT] {len(invariant_violations)} invariant violations detected "
                f"(not raising in non-test mode)"
            )

    # SLOT RETRY TRACKING: Increment retry counter for first missing slot
    # This happens AFTER all session_state construction and modifications are complete
    # and RIGHT BEFORE returning, to ensure the increment is never overwritten
    # session_state["slot_attempts"] is the ONLY source of truth
    # Increment based on status (derived from outcome_status) and session_state["missing_slots"] (final persisted value)
    # CRITICAL: This must run AFTER all session_state modifications to ensure increment persists
    # CRITICAL: Use status (not outcome_status) to match the condition used for setting missing_slots at line 2894
    missing_slots_final = session_state.get("missing_slots", [])

    # DEBUG: Log state before increment block
    logger.debug(
        f"[SLOT_ATTEMPTS_DEBUG] BEFORE increment block: "
        f"status={status}, missing_slots_final={missing_slots_final}, "
        f"session_state['slot_attempts']={session_state.get('slot_attempts')}, "
        f"session_state['slot_attempts'] id={id(session_state.get('slot_attempts'))}"
    )

    if (
        status == "NEEDS_CLARIFICATION"
        and isinstance(missing_slots_final, list)
        and len(missing_slots_final) > 0
    ):
        first_missing = missing_slots_final[0]

        # DEBUG: Log state before setdefault
        logger.debug(
            f"[SLOT_ATTEMPTS_DEBUG] BEFORE setdefault: "
            f"first_missing={first_missing}, "
            f"session_state['slot_attempts']={session_state.get('slot_attempts')}, "
            f"id={id(session_state.get('slot_attempts'))}"
        )

        # Ensure slot_attempts exists and is a dict (defensive guarantee)
        session_state.setdefault("slot_attempts", {})

        # DEBUG: Log state after setdefault
        logger.debug(
            f"[SLOT_ATTEMPTS_DEBUG] AFTER setdefault: "
            f"session_state['slot_attempts']={session_state.get('slot_attempts')}, "
            f"id={id(session_state.get('slot_attempts'))}, "
            f"isinstance={isinstance(session_state.get('slot_attempts'), dict)}"
        )

        if not isinstance(session_state["slot_attempts"], dict):
            logger.warning(
                f"[SLOT_ATTEMPTS_DEBUG] OVERWRITING slot_attempts: "
                f"was={session_state.get('slot_attempts')}, type={type(session_state.get('slot_attempts'))}"
            )
            session_state["slot_attempts"] = {}

        # DEBUG: Log state before increment
        logger.debug(
            f"[SLOT_ATTEMPTS_DEBUG] BEFORE increment: "
            f"session_state['slot_attempts']={session_state.get('slot_attempts')}, "
            f"id={id(session_state.get('slot_attempts'))}, "
            f"current_value={session_state['slot_attempts'].get(first_missing, 0)}"
        )

        # Increment session_state["slot_attempts"] directly (authoritative source)
        # This works on first turn (slot_attempts defaults to {}) and subsequent turns (reads from previous_session_state)
        session_state["slot_attempts"][first_missing] = (
            session_state["slot_attempts"].get(first_missing, 0) + 1
        )

        # DEBUG: Log state after increment
        logger.debug(
            f"[SLOT_ATTEMPTS_DEBUG] AFTER increment: "
            f"session_state['slot_attempts']={session_state.get('slot_attempts')}, "
            f"id={id(session_state.get('slot_attempts'))}, "
            f"incremented_value={session_state['slot_attempts'][first_missing]}"
        )

        logger.debug(
            f"[SLOT_ATTEMPTS] Incremented retry counter for slot '{first_missing}': {session_state['slot_attempts'][first_missing]}"
        )

        # Mirror session_state["slot_attempts"] into outcome.facts (for renderer access)
        # This ensures decision.facts has access to slot_attempts via outcome
        if outcome and isinstance(outcome, dict):
            outcome_facts = outcome.get("facts", {})
            if not isinstance(outcome_facts, dict):
                outcome_facts = {}
            outcome_facts["slot_attempts"] = session_state["slot_attempts"].copy()
            outcome["facts"] = outcome_facts

            # DEBUG: Log outcome.facts copy
            logger.debug(
                f"[SLOT_ATTEMPTS_DEBUG] Copied to outcome.facts: "
                f"outcome.facts['slot_attempts']={outcome_facts.get('slot_attempts')}, "
                f"session_state['slot_attempts']={session_state.get('slot_attempts')}"
            )

        # CRITICAL: Also update session_state["facts"]["slot_attempts"] to match
        # session_state["facts"] was set earlier from serializable_facts (before increment)
        # We need to ensure it reflects the incremented value
        if "facts" in session_state and isinstance(session_state["facts"], dict):
            session_state["facts"]["slot_attempts"] = session_state[
                "slot_attempts"
            ].copy()
            logger.debug(
                f"[SLOT_ATTEMPTS_DEBUG] Updated session_state['facts']['slot_attempts']: "
                f"{session_state['facts']['slot_attempts']}"
            )

    # DEBUG: Log final state before return
    logger.debug(
        f"[SLOT_ATTEMPTS_DEBUG] BEFORE return: "
        f"session_state['slot_attempts']={session_state.get('slot_attempts')}, "
        f"id={id(session_state.get('slot_attempts'))}"
    )


    # Carry conversation memory forward so NLU receives it on the next turn.
    # _conversation is attached to merged_luma_response by orchestrator after the Luma call.
    if merged_luma_response and isinstance(merged_luma_response, dict):
        conv = merged_luma_response.get("_conversation")
        if conv and isinstance(conv, dict):
            session_state["conversation"] = conv

    return session_state
