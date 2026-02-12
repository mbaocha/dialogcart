"""
Session Merge Helper

Merges session state with Luma response for follow-up handling.

This module provides pure functions for merging session state without
changing core logic.
"""

import json
import logging
import os
from typing import Dict, Any, Optional

from core.planning.orchestration.missing_slots import compute_missing_slots
from core.orchestration.persistence.durable_intents import (
    is_durable_intent,
    filter_slots_for_intent,
)

logger = logging.getLogger(__name__)
# Dedicated turn-level logger (clean, minimal logs - ONE log per section)
turn_logger = logging.getLogger("core.turn_log")
turn_logger.setLevel(logging.INFO)


def merge_luma_with_session(
    luma_response: Dict[str, Any],
    session_state: Dict[str, Any],
    planning_only: bool = False
) -> Dict[str, Any]:
    """
    Merge Luma response with session state for follow-up handling.

    Merge rules (STRICT):
    1. Session intent is immutable - if Luma intent != session intent, session should be reset (handled in orchestrator)
    2. If luma.intent == UNKNOWN: use session.intent (don't modify session intent)
    3. Extract slots from Luma slots dict AND trace.semantic fields
    4. Start with session slots, merge new entities from Luma (do NOT overwrite existing session values)
    5. Update missing_slots after merge (must shrink on follow-up turns)

    IMPORTANT: This function assumes luma.intent == UNKNOWN or luma.intent == session.intent.
    Intent mismatch should be handled by resetting session BEFORE calling this function.

    Args:
        luma_response: Luma API response (may contain newly extracted entities even if intent=UNKNOWN)
        session_state: Session state from previous turn (status: "NEEDS_CLARIFICATION" or "READY")

    Returns:
        Modified Luma response with merged slots and session intent (ready for process_luma_response)
    """
    # SESSION_DEBUG_RAW: Log full session payload (for Turn-2 debugging)
    logger.error(
        "[SESSION_DEBUG_RAW] full session payload: %s",
        session_state
    )

    # SESSION_DEBUG_KEYS: Log session keys (for Turn-2 debugging)
    logger.error(
        "[SESSION_DEBUG_KEYS] session keys: %s",
        list(session_state.keys()) if isinstance(
            session_state, dict) else type(session_state)
    )

    # SESSION_DEBUG_INTENT_FIELDS: Log both intent fields (for Turn-2 debugging)
    logger.error(
        "[SESSION_DEBUG_INTENT_FIELDS] "
        "session.intent=%s | session.intent_name=%s",
        session_state.get("intent") if isinstance(
            session_state, dict) else None,
        session_state.get("intent_name") if isinstance(
            session_state, dict) else None,
    )

    # SESSION_DEBUG: Log session state before any intent logic (for Turn-2 debugging)
    logger.error(
        "[SESSION_DEBUG] session read before merge",
        extra={
            "session": session_state,
            "session_intent": session_state.get("intent_name") if session_state else None,
            "session_slots": session_state.get("slots") if session_state else None,
        },
    )

    # SESSION_BEFORE: Log session state before merge
    user_id = session_state.get(
        "user_id", "unknown") if session_state else "unknown"
    session_slots = session_state.get("slots", {}) if session_state else {}
    session_missing_slots = session_state.get(
        "missing_slots", []) if session_state else []
    logger.info("[SESSION_BEFORE] user_id=%s slots=%s missing_slots=%s",
                user_id, json.dumps(session_slots, default=str, ensure_ascii=True), session_missing_slots)

    # Create a copy to avoid mutating the original
    merged = luma_response.copy()

    # Preserve debugging fields (e.g., _raw_luma_response) - these must NOT be mutated or normalized
    # _raw_luma_response is attached by orchestrator for debugging and must be preserved through merge

    # STEP 1: Handle intent - Session intent is immutable unless session is reset
    # If luma.intent == UNKNOWN: use session.intent (don't modify session intent)
    # CRITICAL: Read from "intent_name" first (canonical persisted field), fallback to "intent" (legacy/compat)
    # This matches Core's persisted session schema where intent is stored as "intent_name"
    # Handle None explicitly (ephemeral intents are stored as None, not empty string)
    session_intent = session_state.get("intent_name")
    if session_intent is None:
        session_intent = session_state.get("intent")

    # SESSION_DEBUG_RESOLVED: Log resolved session_intent (for Turn-2 debugging)
    logger.error(
        "[SESSION_DEBUG_RESOLVED] resolved session_intent=%s",
        session_intent
    )

    session_status = session_state.get("status", "")

    # Extract Luma intent
    luma_intent_obj = merged.get("intent", {})
    luma_intent_name = luma_intent_obj.get(
        "name", "") if isinstance(luma_intent_obj, dict) else ""

    # SESSION_DEBUG: Log Luma intent immediately after parsing (for Turn-2 debugging)
    logger.error(
        "[SESSION_DEBUG] luma intent before merge",
        extra={
            "luma_intent": luma_intent_name,
        },
    )

    logger.debug(
        f"merge_luma_with_session: luma_intent={luma_intent_name} "
        f"session_intent={session_intent} session_status={session_status}"
    )

    # Extract session intent name for comparison (MUST be done before first use)
    # CRITICAL: Handle both string and dict formats, and handle None/empty strings correctly
    # None means ephemeral (no durable intent persisted)
    session_intent_name = None
    # Explicitly check for None (not just truthy)
    if session_intent is not None:
        if isinstance(session_intent, str):
            # String format: use directly if non-empty
            session_intent_name = session_intent if session_intent else None
        elif isinstance(session_intent, dict):
            # Dict format: extract "name" field
            session_intent_name = session_intent.get("name")
            # Only use if it's a non-empty string
            if not session_intent_name or not isinstance(session_intent_name, str):
                session_intent_name = None
    # If session_intent is None, session_intent_name remains None (ephemeral)

    # STEP 1.5: Extract slots from Luma to check if continuation is valid
    # Extract slots early to determine if UNKNOWN intent should be overridden
    from core.orchestration.luma_facts_adapter import facts_to_slots
    facts_obj_temp = merged.get("facts", {})
    # Use session intent for slot extraction if Luma intent is UNKNOWN (for proper slot extraction)
    # Only use session intent if it's durable
    effective_intent_for_slot_check = luma_intent_name
    if luma_intent_name == "UNKNOWN" and session_intent_name and is_durable_intent(session_intent_name):
        effective_intent_for_slot_check = session_intent_name
    luma_slots_temp = facts_to_slots(
        facts_obj_temp,
        intent_name=effective_intent_for_slot_check,
        source_text=merged.get("_source_text"),
    ) if isinstance(facts_obj_temp, dict) else {}
    # Also check for slots in nested facts.facts.slots
    if isinstance(facts_obj_temp, dict) and isinstance(facts_obj_temp.get("facts"), dict):
        nested_slots = facts_obj_temp.get("facts", {}).get("slots", {})
        if isinstance(nested_slots, dict):
            luma_slots_temp.update(nested_slots)
    # Check for slots in top-level slots field
    top_level_slots = merged.get("slots", {})
    if isinstance(top_level_slots, dict):
        luma_slots_temp.update(top_level_slots)

    # Determine if slots are present (non-empty dict)
    has_extracted_slots = bool(luma_slots_temp)

    # ARCHITECTURAL FIX: Intent is resolved ONCE in orchestrator.py before calling merge_luma_with_session
    # The orchestrator sets effective_response["intent"]["name"] as the SINGLE SOURCE OF TRUTH
    # This function MUST NOT recompute intent - it must preserve the authoritative intent
    #
    # HARD RULE: NEVER overwrite merged["intent"]["name"] if it already exists and is non-empty
    # NEVER write "" or None into merged["intent"]["name"]
    # If orchestrator set an intent, it is authoritative and must be preserved

    # PHASE 1 INSTRUMENTATION: Log entry state
    logger.error(
        "[INTENT_TRACE_SESSION_MERGE] ENTRY: "
        f"merged['intent']['name']={merged.get('intent', {}).get('name', '') if isinstance(merged.get('intent'), dict) else 'N/A'}, "
        f"session_intent_name={session_intent_name}, "
        f"session_status={session_status}, "
        f"luma_intent_name={luma_intent_name}"
    )

    # Ensure intent dict exists
    if not isinstance(merged.get("intent"), dict):
        merged["intent"] = {}

    existing_intent_name = merged.get("intent", {}).get("name", "")

    # PHASE 1 INSTRUMENTATION: Log BEFORE intent assignment
    logger.error(
        "[INTENT_TRACE_SESSION_MERGE] BEFORE intent assignment: "
        f"existing_intent_name={existing_intent_name}, "
        f"session_intent_name={session_intent_name}, "
        f"luma_intent_name={luma_intent_name}, "
        f"session_intent_is_durable={is_durable_intent(session_intent_name) if session_intent_name else False}"
    )

    # INVARIANT ENFORCEMENT: If session has a durable intent, UNKNOWN/empty/None intents from Luma must be ignored
    if (not existing_intent_name or existing_intent_name == "UNKNOWN") and session_intent_name:
        # Check if session intent is durable
        if is_durable_intent(session_intent_name):
            # Assert if code attempts to overwrite a durable intent with UNKNOWN/empty
            if luma_intent_name == "UNKNOWN" or not luma_intent_name:
                # This is expected - orchestrator should have already set the durable intent
                # But if it didn't, we preserve it here as a safety measure
                merged["intent"]["name"] = session_intent_name
                merged["_effective_intent"] = session_intent_name
                logger.info(
                    f"merge_luma_with_session: Preserved durable session intent={session_intent_name} "
                    f"(Luma returned UNKNOWN/empty, orchestrator should have set this)"
                )
            else:
                # Luma has a non-UNKNOWN intent - this should have been set by orchestrator
                # If it wasn't, this is a bug
                raise AssertionError(
                    f"merge_luma_with_session: Orchestrator should have set authoritative intent, but intent['name'] is empty/UNKNOWN. "
                    f"luma_intent={luma_intent_name}, session_intent={session_intent_name}, "
                    f"existing_intent_name={existing_intent_name}"
                )
        else:
            # Session intent is not durable - if orchestrator didn't set an intent, this is expected
            if not existing_intent_name or existing_intent_name == "UNKNOWN":
                # No authoritative intent from orchestrator - this is valid for ephemeral intents
                merged["intent"]["name"] = luma_intent_name or ""
                merged["_effective_intent"] = luma_intent_name or ""
                logger.debug(
                    f"merge_luma_with_session: No durable intent to preserve (ephemeral). "
                    f"luma_intent={luma_intent_name}, session_intent={session_intent_name}"
                )
    elif existing_intent_name and existing_intent_name != "UNKNOWN":
        # Intent already set by orchestrator - preserve it as authoritative
        # Update _effective_intent to match (for consistency)
        merged["_effective_intent"] = existing_intent_name
        logger.debug(
            f"merge_luma_with_session: Preserved authoritative intent from orchestrator: {existing_intent_name}"
        )

        # INVARIANT CHECK: Assert if attempting to overwrite a durable intent
        if session_intent_name and is_durable_intent(session_intent_name):
            if existing_intent_name != session_intent_name and (not luma_intent_name or luma_intent_name == "UNKNOWN"):
                # This should not happen - orchestrator should have preserved durable session intent
                logger.warning(
                    f"merge_luma_with_session: Orchestrator set intent={existing_intent_name} but durable session intent={session_intent_name} exists. "
                    f"This may indicate orchestrator did not properly preserve durable intent."
                )
    else:
        # No intent from orchestrator and no durable session intent - this is valid for first turns
        merged["intent"]["name"] = luma_intent_name or ""
        merged["_effective_intent"] = luma_intent_name or ""
        logger.debug(
            f"merge_luma_with_session: No authoritative intent to preserve (first turn or ephemeral). "
            f"luma_intent={luma_intent_name}"
        )

    # STEP 2: Merge session facts with Luma facts (new facts override old)
    # Facts are a first-class, durable part of session state (same status as slots)
    # This ensures capability facts (e.g., payment_satisfied) persist across turns
    session_facts = session_state.get("facts", {}) if session_state else {}
    if not isinstance(session_facts, dict):
        session_facts = {}

    luma_facts = merged.get("facts", {})
    if not isinstance(luma_facts, dict):
        luma_facts = {}

    # Merge: new facts from Luma override old facts from session
    # This allows capabilities to update facts (e.g., payment_satisfied: True)
    merged_facts = {**session_facts, **luma_facts}

    # Update merged response with merged facts
    merged["facts"] = merged_facts

    # STEP 3: Extract slots from Luma response
    # FACT-ONLY: Promote facts to slots BEFORE merging with session
    # This ensures facts.service_id, facts.times, etc. are available for planning
    from core.orchestration.luma_facts_adapter import facts_to_slots
    facts_obj = merged.get("facts", {})
    # Get intent for date_range promotion (CREATE_RESERVATION with 2+ dates)
    # CRITICAL: Use effective_intent (computed EARLY after UNKNOWN override) for all operations
    # This ensures effective_intent is used consistently throughout
    effective_intent_for_promotion = merged.get(
        "_effective_intent", luma_intent_name)
    promoted_slots_from_facts = facts_to_slots(
        facts_obj,
        intent_name=effective_intent_for_promotion,
        source_text=merged.get("_source_text"),
    ) if isinstance(facts_obj, dict) else {}

    # Extract slots from facts.facts.slots if present (nested format)
    # Otherwise fall back to legacy slots field
    nested_slots = {}
    if isinstance(facts_obj, dict) and "slots" in facts_obj:
        # Nested format: facts.facts.slots
        nested_slots = facts_obj.get("slots", {})
    else:
        # Legacy format: slots at top level
        nested_slots = merged.get("slots", {})

    # Merge promoted slots (from facts.service_id, facts.times, etc.) with nested slots
    # Promoted slots take precedence (they are the source of truth)
    raw_luma_slots = {**nested_slots, **promoted_slots_from_facts}

    # CRITICAL: Preserve raw service_id from raw Luma facts if present
    # If raw Luma facts have service_id, use that as the raw tenant value
    # Store normalized value (if any) as canonical
    raw_luma_response = merged.get("_raw_luma_response", {})
    if isinstance(raw_luma_response, dict):
        raw_luma_facts = raw_luma_response.get("facts", {})
        if isinstance(raw_luma_facts, dict) and "service_id" in raw_luma_facts:
            # Raw Luma facts contain service_id - use as raw tenant value
            raw_service_id_from_facts = raw_luma_facts["service_id"]
            raw_luma_slots["service_id"] = raw_service_id_from_facts

            # Check if nested_slots or promoted_slots has a normalized/canonical value
            # This happens when orchestrator.py normalizes service_id before merge
            if isinstance(nested_slots, dict) and "_canonical_service_id" in nested_slots:
                # Canonical already computed - preserve it
                raw_luma_slots["_canonical_service_id"] = nested_slots["_canonical_service_id"]
            elif isinstance(nested_slots, dict) and "service_id" in nested_slots:
                nested_service_id = nested_slots["service_id"]
                # If nested value differs from raw, it's likely normalized - store as canonical
                if nested_service_id != raw_service_id_from_facts:
                    raw_luma_slots["_canonical_service_id"] = nested_service_id
            # Also check promoted_slots_from_facts for canonical
            if "_canonical_service_id" not in raw_luma_slots and isinstance(promoted_slots_from_facts, dict):
                if "_canonical_service_id" in promoted_slots_from_facts:
                    raw_luma_slots["_canonical_service_id"] = promoted_slots_from_facts["_canonical_service_id"]
                elif "service_id" in promoted_slots_from_facts:
                    promoted_service_id = promoted_slots_from_facts["service_id"]
                    if promoted_service_id != raw_service_id_from_facts:
                        raw_luma_slots["_canonical_service_id"] = promoted_service_id
    else:
        # No raw Luma response - check if we have normalized value that should be canonical
        if isinstance(nested_slots, dict) and "service_id" in nested_slots:
            nested_service_id = nested_slots["service_id"]
            # Check if this looks like a normalized value (contains dots, e.g., "beauty_and_wellness.haircut")
            if "." in str(nested_service_id):
                # Likely normalized - but we don't have raw, so use as both
                raw_luma_slots["service_id"] = nested_service_id
            elif isinstance(promoted_slots_from_facts, dict) and "service_id" in promoted_slots_from_facts:
                # Use promoted as raw, nested as canonical if different
                promoted_service_id = promoted_slots_from_facts["service_id"]
                if promoted_service_id != nested_service_id:
                    raw_luma_slots["service_id"] = promoted_service_id
                    raw_luma_slots["_canonical_service_id"] = nested_service_id
                else:
                    raw_luma_slots["service_id"] = nested_service_id

    if not isinstance(raw_luma_slots, dict):
        raw_luma_slots = {}

    # Store raw_luma_slots for turn outcome snapshot logging
    merged["_raw_luma_slots"] = raw_luma_slots.copy()

    # STEP 2.5: Preserve time_constraint from session state if current turn doesn't have one
    # This ensures time_constraint from previous turns (e.g., "book at 2pm") is preserved
    # when the next turn only provides date (e.g., "tomorrow")
    if session_state:
        session_time_constraint = session_state.get("time_constraint")
        current_time_constraint = merged.get("time_constraint")
        if session_time_constraint is not None and current_time_constraint is None:
            # Preserve time_constraint from session if current turn doesn't override it
            merged["time_constraint"] = session_time_constraint
            logger.debug(
                f"merge_luma_with_session: Preserved time_constraint from session: {session_time_constraint}"
            )

    # MERGE_RESULT: Log merged slots and their sources
    slot_sources = {}
    for key in raw_luma_slots.keys():
        source = []
        if key in nested_slots:
            source.append("nested_slots")
        if key in promoted_slots_from_facts:
            source.append("promoted_from_facts")
        slot_sources[key] = "|".join(source) if source else "unknown"
    logger.info("[MERGE_RESULT] user_id=%s merged_slots=%s slot_sources=%s",
                user_id, json.dumps(raw_luma_slots, default=str, ensure_ascii=True), json.dumps(slot_sources, ensure_ascii=True))

    # Keep luma_slots alias for backward compatibility with existing code
    luma_slots = raw_luma_slots

    # DEBUG: Log Luma response structure for date extraction debugging
    logger.debug(
        f"merge_luma_with_session: Checking for date/time in Luma response. "
        f"slots={list(luma_slots.keys())}, "
        f"has_trace={bool(merged.get('trace'))}, "
        f"has_stages={bool(merged.get('stages'))}, "
        f"has_entities={bool(merged.get('entities'))}"
    )

    # Helper function to extract date from any location in Luma response
    def _extract_date_from_luma_response(luma_resp: Dict[str, Any]) -> Optional[str]:
        """
        Extract date from Luma response, checking all possible locations.

        Returns the first date found, or None if not found.
        """
        # Priority 1: Direct slots.date
        if "slots" in luma_resp and isinstance(luma_resp["slots"], dict):
            if "date" in luma_resp["slots"]:
                date_val = luma_resp["slots"]["date"]
                if date_val:
                    return str(date_val) if not isinstance(date_val, str) else date_val

        # Priority 1.5: Check issues field (sometimes Luma provides date in issues for UNKNOWN intents)
        if "issues" in luma_resp and isinstance(luma_resp["issues"], dict):
            # Check if issues contains date information
            for key, value in luma_resp["issues"].items():
                if "date" in key.lower() and value:
                    if isinstance(value, str) and len(value) >= 10 and value[4] == "-" and value[7] == "-":
                        return value.split("T")[0].split(" ")[0]
                    elif isinstance(value, dict):
                        # Check nested date fields
                        for date_field in ["date", "value", "resolved", "start", "start_date"]:
                            if date_field in value:
                                date_val = value[date_field]
                                if date_val:
                                    date_str = str(date_val)
                                    if "T" in date_str:
                                        return date_str.split("T")[0]
                                    if " " in date_str:
                                        return date_str.split(" ")[0]
                                    if len(date_str) >= 10 and date_str[4] == "-" and date_str[7] == "-":
                                        return date_str

        # Priority 2: Check all semantic locations for date_refs
        semantic_paths = [
            ("semantic", "date_refs"),
            ("semantic", "resolved_booking", "date_refs"),
            ("stages", "semantic", "resolved_booking", "date_refs"),
            ("stages", "semantic", "date_refs"),
            ("trace", "semantic", "date_refs"),
            ("trace", "semantic", "resolved_booking", "date_refs"),
            ("trace", "stages", "semantic", "resolved_booking", "date_refs"),
        ]

        for path in semantic_paths:
            current = luma_resp
            try:
                for key in path:
                    if isinstance(current, dict) and key in current:
                        current = current[key]
                    else:
                        current = None
                        break

                if current and isinstance(current, list) and len(current) > 0:
                    # Get the last date_ref (most recent/resolved)
                    date_candidate = current[-1]
                    if isinstance(date_candidate, str):
                        # If it's a string, check if it's ISO date format
                        if len(date_candidate) >= 10 and date_candidate[4] == "-" and date_candidate[7] == "-":
                            # Extract date part
                            return date_candidate.split("T")[0].split(" ")[0]
                        return date_candidate
                    elif isinstance(date_candidate, dict):
                        # If it's an object, check common date fields
                        for date_field in ["resolved", "date", "value", "start", "start_date"]:
                            if date_field in date_candidate:
                                date_val = date_candidate[date_field]
                                if date_val:
                                    date_str = str(date_val)
                                    # Extract date part if it's datetime
                                    if "T" in date_str:
                                        return date_str.split("T")[0]
                                    if " " in date_str:
                                        return date_str.split(" ")[0]
                                    return date_str
            except (KeyError, TypeError, AttributeError):
                continue

        # Priority 3: Check entities.date
        if "entities" in luma_resp and isinstance(luma_resp["entities"], dict):
            if "date" in luma_resp["entities"]:
                date_val = luma_resp["entities"]["date"]
                if date_val:
                    return str(date_val) if not isinstance(date_val, str) else date_val

        # Priority 4: Check booking.datetime_range.start
        if "booking" in luma_resp and isinstance(luma_resp["booking"], dict):
            booking = luma_resp["booking"]
            if "datetime_range" in booking and isinstance(booking["datetime_range"], dict):
                start = booking["datetime_range"].get("start")
                if start:
                    date_str = str(start)
                    # Extract date part
                    if "T" in date_str:
                        return date_str.split("T")[0]
                    if " " in date_str:
                        return date_str.split(" ")[0]
                    return date_str

        return None

    # Extract date using the helper (checks all possible locations)
    extracted_date = _extract_date_from_luma_response(merged)
    if extracted_date and "date" not in luma_slots:
        luma_slots["date"] = extracted_date
        logger.debug(
            f"Extracted date using comprehensive helper: {extracted_date}")

    # DEBUG: Log if date extraction failed (for weekday debugging)
    debug_weekday = os.getenv("DEBUG_WEEKDAY", "0") == "1"
    if debug_weekday and "date" not in luma_slots and session_state and session_state.get("status") == "NEEDS_CLARIFICATION":
        logger.warning(
            f"DEBUG_WEEKDAY: Date extraction failed for follow-up. "
            f"luma_slots={list(luma_slots.keys())}, "
            f"merged_keys={list(merged.keys())}"
        )

    # Extract semantic fields for slot extraction (when slots is empty/partial)
    # Check multiple locations for date/time information (Luma may provide in different places)
    trace = merged.get("trace", {})
    semantic_data = None

    # Try trace.semantic first
    if isinstance(trace, dict):
        semantic_data = trace.get("semantic", {})

    # Try stages.semantic.resolved_booking as fallback
    if not semantic_data:
        stages = merged.get("stages", {})
        if isinstance(stages, dict):
            semantic_stage = stages.get("semantic", {})
            if isinstance(semantic_stage, dict):
                semantic_data = semantic_stage.get("resolved_booking", {})

    # Also check if semantic data exists directly in stages.semantic (not just resolved_booking)
    if not semantic_data:
        stages = merged.get("stages", {})
        if isinstance(stages, dict):
            semantic_stage = stages.get("semantic", {})
            if isinstance(semantic_stage, dict) and semantic_stage:
                semantic_data = semantic_stage

    # Also check entities for date/time (Luma may provide date directly in entities)
    entities = merged.get("entities", {})
    if isinstance(entities, dict):
        # Check if date is in entities but not yet in slots
        if "date" in entities and "date" not in luma_slots:
            date_value = entities.get("date")
            if date_value:
                luma_slots["date"] = date_value
                logger.debug(
                    f"Extracted date from entities.date: {date_value}")
        # TIME_CONSTRAINT RULE: For CREATE_APPOINTMENT, do NOT extract time from entities
        # time_constraint is authoritative; slots.time is legacy-only and must not drive planning
        if "time" in entities and "time" not in luma_slots:
            # Only extract time for non-CREATE_APPOINTMENT intents
            # For CREATE_APPOINTMENT, time_constraint is authoritative (handled separately)
            if merged_intent_name != "CREATE_APPOINTMENT":
                time_value = entities.get("time")
                if time_value:
                    luma_slots["time"] = time_value
                    logger.debug(
                        f"Extracted time from entities.time: {time_value}")
                else:
                    logger.debug(
                        f"Skipped time extraction from entities for CREATE_APPOINTMENT (time_constraint is authoritative)")

    # Check if semantic data exists but wasn't found in trace/stages (try direct access)
    # Sometimes Luma provides semantic data at root level or in different structure
    if not semantic_data:
        # Try merged.get("semantic") directly
        root_semantic = merged.get("semantic")
        if isinstance(root_semantic, dict):
            semantic_data = root_semantic
            logger.debug("Found semantic data at root level")

    # Extract intent name early for reservation contract enforcement
    # CRITICAL: Use effective_intent (computed EARLY after UNKNOWN override) for all operations
    # This ensures effective_intent is used consistently throughout
    merged_intent_name = merged.get("_effective_intent", merged.get(
        "intent", {}).get("name", "") if isinstance(merged.get("intent"), dict) else "")

    # If we still have semantic_data, process it (this handles the case where we found it in a different location)
    # Process semantic data even if slots.date exists (may need to extract role-specific slots from date_roles)
    if isinstance(semantic_data, dict):
        date_refs = semantic_data.get("date_refs", [])
        date_mode = semantic_data.get("date_mode", "")
        time_constraint = semantic_data.get("time_constraint")
        time_refs = semantic_data.get("time_refs", [])
        date_roles = semantic_data.get("date_roles", [])

        # Process date_refs if found
        # CONTRACT ENFORCEMENT: For CREATE_RESERVATION, extract role-specific slots when date_roles available
        # If no date_roles, extract as date (will be normalized to start_date later)
        if date_refs and isinstance(date_refs, list) and len(date_refs) > 0:
            if merged_intent_name == "CREATE_RESERVATION":
                # For reservations, extract role-specific slots when date_roles explicitly labels them
                if date_roles:
                    if "START_DATE" in date_roles and "start_date" not in luma_slots:
                        luma_slots["start_date"] = date_refs[0]
                        logger.debug(
                            f"Extracted start_date from semantic.date_refs with START_DATE role: {date_refs[0]}")
                    if "END_DATE" in date_roles and "end_date" not in luma_slots:
                        # END_DATE might be in a later position in date_refs
                        if isinstance(date_refs, list):
                            # Find index of END_DATE in date_roles to match with date_refs
                            try:
                                end_date_idx = list(
                                    date_roles).index("END_DATE")
                                if end_date_idx < len(date_refs):
                                    luma_slots["end_date"] = date_refs[end_date_idx]
                                    logger.debug(
                                        f"Extracted end_date from semantic.date_refs with END_DATE role: {date_refs[end_date_idx]}")
                            except (ValueError, IndexError):
                                # Fallback to last date if END_DATE role exists and we have multiple dates
                                if len(date_refs) > 1:
                                    luma_slots["end_date"] = date_refs[-1]
                                    logger.debug(
                                        f"Extracted end_date from semantic.date_refs (last date, END_DATE role): {date_refs[-1]}")
                # FIX: For CREATE_RESERVATION, do NOT extract generic "date" slot when date_roles is missing
                # Only extract role-specific slots (start_date, end_date) when explicitly labeled by date_roles
                # If Luma returns only date without date_roles, keep it as date in context but do NOT satisfy start_date requirement
                # This prevents auto-promotion of generic date to start_date
            elif date_mode == "single_day" or not date_mode:
                # For service appointments, extract date if single_day mode
                if "date" not in luma_slots and "start_date" not in luma_slots:
                    luma_slots["date"] = date_refs[0]
                    logger.debug(
                        f"Extracted date from semantic.date_refs (root/found): {date_refs[0]}")

        # Process time if found
        # TIME_CONSTRAINT RULE: For CREATE_APPOINTMENT, do NOT extract time from time_constraint/time_refs
        # time_constraint is authoritative; slots.time is legacy-only and must not drive planning
        # Only derive slots.time AFTER planning for backward compatibility (done in luma_response_processor.py)
        if (time_refs or time_constraint) and "time" not in luma_slots:
            # Only extract time for non-CREATE_APPOINTMENT intents
            # For CREATE_APPOINTMENT, time_constraint is authoritative (handled separately in planning)
            if merged_intent_name != "CREATE_APPOINTMENT":
                if time_constraint:
                    # If time_constraint is a dict with start/mode, extract start (e.g., "12:00" for "noon")
                    if isinstance(time_constraint, dict):
                        constraint_start = time_constraint.get("start")
                        constraint_mode = time_constraint.get("mode", "")
                        if constraint_start:
                            luma_slots["time"] = constraint_start
                            logger.debug(
                                f"Extracted time from semantic.time_constraint.start: {constraint_start} (mode={constraint_mode})")
                        else:
                            # Fallback: use time_constraint dict as-is if no start
                            luma_slots["time"] = time_constraint
                            logger.debug(
                                f"Extracted time from semantic.time_constraint (dict): {time_constraint}")
                    else:
                        # time_constraint is a string, use directly
                        luma_slots["time"] = time_constraint
                        logger.debug(
                            f"Extracted time from semantic.time_constraint: {time_constraint}")
                elif time_refs and isinstance(time_refs, list) and len(time_refs) > 0:
                    luma_slots["time"] = time_refs[0]
                    logger.debug(
                        f"Extracted time from semantic.time_refs: {time_refs[0]}")
            else:
                # CREATE_APPOINTMENT: Skip time extraction - time_constraint is authoritative
                logger.debug(
                    f"Skipped time extraction from time_constraint/time_refs for CREATE_APPOINTMENT (time_constraint is authoritative)")

    # Project semantic fields into slots for follow-ups
    # Extract from trace.semantic or stages.semantic.resolved_booking
    if isinstance(semantic_data, dict):
        date_refs = semantic_data.get("date_refs", [])
        date_mode = semantic_data.get("date_mode", "")
        time_constraint = semantic_data.get("time_constraint")
        time_refs = semantic_data.get("time_refs", [])
        date_roles = semantic_data.get("date_roles", [])

        # If date_refs exists:
        if date_refs:
            # Check date_roles to determine which slot to fill
            if date_roles:
                if "START_DATE" in date_roles and "start_date" not in luma_slots:
                    if isinstance(date_refs, list) and len(date_refs) > 0:
                        luma_slots["start_date"] = date_refs[0]
                if "END_DATE" in date_roles and "end_date" not in luma_slots:
                    if isinstance(date_refs, list):
                        # Find index of END_DATE in date_roles to match with date_refs
                        try:
                            end_date_idx = list(date_roles).index("END_DATE")
                            if end_date_idx < len(date_refs):
                                luma_slots["end_date"] = date_refs[end_date_idx]
                        except (ValueError, IndexError):
                            # Fallback to last date if END_DATE role exists and we have multiple dates
                            if len(date_refs) > 1:
                                luma_slots["end_date"] = date_refs[-1]
                    # CONTRACT ENFORCEMENT: Do NOT infer end_date from single date
                    # end_date must be explicitly provided or extracted from date_refs with END_DATE role

            # CONTRACT ENFORCEMENT: For CREATE_RESERVATION, do NOT extract generic "date" slot
            # Only extract role-specific slots (start_date, end_date) when explicitly labeled
            if merged_intent_name == "CREATE_RESERVATION":
                # For reservations, only extract if date_roles explicitly provides role labels
                # Do NOT extract generic "date" slot
                if date_roles:
                    # Role-specific extraction is handled above (lines 314-316)
                    pass
                # Do NOT fall through to generic date extraction for reservations
            else:
                # For service appointments (CREATE_APPOINTMENT), extract date slot
                # single_day → slots["date"] (for service appointments)
                if date_mode == "single_day" and "date" not in luma_slots and "start_date" not in luma_slots:
                    if isinstance(date_refs, list) and len(date_refs) > 0:
                        luma_slots["date"] = date_refs[0]
                # range → slots["date_range"] or start_date/end_date
                elif date_mode == "range":
                    if "date_range" not in luma_slots and "start_date" not in luma_slots:
                        if isinstance(date_refs, list):
                            if len(date_refs) >= 2:
                                # Only assign if we have both dates - no inference
                                luma_slots["start_date"] = date_refs[0]
                                luma_slots["end_date"] = date_refs[-1]
                            # CONTRACT ENFORCEMENT: Do NOT infer start_date or end_date from single date in range mode
                            # Both dates must be explicitly provided
                # If no date_mode specified but date_refs exist, assume single_day for service appointments
                elif not date_mode and date_refs:
                    if "date" not in luma_slots and "start_date" not in luma_slots:
                        if isinstance(date_refs, list) and len(date_refs) > 0:
                            luma_slots["date"] = date_refs[0]

        # If time_refs or time_constraint exists → slots["time"]
        # TIME_CONSTRAINT RULE: For CREATE_APPOINTMENT, do NOT extract time from time_constraint/time_refs
        # time_constraint is authoritative; slots.time is legacy-only and must not drive planning
        # Only derive slots.time AFTER planning for backward compatibility (done in luma_response_processor.py)
        if (time_refs or time_constraint) and "time" not in luma_slots:
            # Only extract time for non-CREATE_APPOINTMENT intents
            # For CREATE_APPOINTMENT, time_constraint is authoritative (handled separately in planning)
            if merged_intent_name != "CREATE_APPOINTMENT":
                if time_constraint:
                    # If time_constraint is a dict with start/mode, extract start (e.g., "12:00" for "noon")
                    if isinstance(time_constraint, dict):
                        constraint_start = time_constraint.get("start")
                        constraint_mode = time_constraint.get("mode", "")
                        if constraint_start:
                            luma_slots["time"] = constraint_start
                            logger.debug(
                                f"Extracted time from semantic.time_constraint.start (projection): {constraint_start} (mode={constraint_mode})")
                        else:
                            # Fallback: use time_constraint dict as-is if no start
                            luma_slots["time"] = time_constraint
                            logger.debug(
                                f"Extracted time from semantic.time_constraint (dict, projection): {time_constraint}")
                    else:
                        # time_constraint is a string, use directly
                        luma_slots["time"] = time_constraint
                        logger.debug(
                            f"Extracted time from semantic.time_constraint (projection): {time_constraint}")
                elif time_refs and isinstance(time_refs, list) and len(time_refs) > 0:
                    luma_slots["time"] = time_refs[0]
                    logger.debug(
                        f"Extracted time from semantic.time_refs (projection): {time_refs[0]}")
            else:
                # CREATE_APPOINTMENT: Skip time extraction - time_constraint is authoritative
                logger.debug(
                    f"Skipped time extraction from time_constraint/time_refs (projection) for CREATE_APPOINTMENT (time_constraint is authoritative)")

    # Additional fallback: Check if Luma provided date/time directly in merged response
    # (Sometimes Luma provides date in slots even without semantic data)
    if "date" not in luma_slots:
        # Check if date exists in merged response slots (Luma might have added it)
        direct_date = merged.get("slots", {}).get("date")
        if direct_date:
            luma_slots["date"] = direct_date
            logger.debug(
                f"Extracted date from merged.slots.date: {direct_date}")

    # TIME_CONSTRAINT RULE: For CREATE_APPOINTMENT, do NOT extract time from merged.slots.time
    # time_constraint is authoritative; slots.time is legacy-only and must not drive planning
    if "time" not in luma_slots:
        # Check if time exists in merged response slots
        # Only extract time for non-CREATE_APPOINTMENT intents
        # For CREATE_APPOINTMENT, time_constraint is authoritative (handled separately in planning)
        if merged_intent_name != "CREATE_APPOINTMENT":
            direct_time = merged.get("slots", {}).get("time")
            if direct_time:
                luma_slots["time"] = direct_time
                logger.debug(
                    f"Extracted time from merged.slots.time: {direct_time}")
        else:
            # CREATE_APPOINTMENT: Skip time extraction - time_constraint is authoritative
            logger.debug(
                f"Skipped time extraction from merged.slots.time for CREATE_APPOINTMENT (time_constraint is authoritative)")

    # Check booking object for date/time (Luma might provide in booking.datetime_range)
    booking_obj = merged.get("booking")
    if isinstance(booking_obj, dict) and "date" not in luma_slots:
        booking_date = booking_obj.get("date") or (booking_obj.get("datetime_range", {}).get(
            "start") if isinstance(booking_obj.get("datetime_range"), dict) else None)
        if booking_date:
            # Extract date part if it's a datetime
            if isinstance(booking_date, str):
                date_part = booking_date.split("T")[0].split(" ")[0]
                luma_slots["date"] = date_part
                logger.debug(
                    f"Extracted date from booking object: {date_part}")

    # STEP 3: Merge slots: Start with session slots, then merge new entities from Luma
    # CRITICAL: This must be additive and non-destructive - preserve all existing slots
    # Rule: merged_slots = {**session_slots, **luma_slots}
    # This ensures:
    # 1. All session slots are preserved (non-destructive) - slots are durable facts
    # 2. New Luma slots are added
    # 3. Existing slots can be updated with new values from Luma
    # ARCHITECTURAL INVARIANT: session.slots is the single source of truth for collected slots
    # Slots present in session MUST be preserved across turns unless intent changes
    session_slots = session_state.get("slots", {})
    if not isinstance(session_slots, dict):
        session_slots = {}

    # LOG: session.slots before merge
    logger.info(
        f"[SLOT_DURABILITY] session.slots before merge: {list(session_slots.keys())} = {session_slots}"
    )
    print(
        f"[SLOT_DURABILITY] session.slots before merge: {list(session_slots.keys())} = {session_slots}")

    # Start with session slots (preserve all previously resolved slots)
    # CRITICAL MERGE ORDER: This merge MUST happen BEFORE intent-change filtering (line ~1033)
    # to ensure valid slots from previous turns (e.g., date from UNKNOWN intent) are preserved
    # when transitioning to concrete intents (e.g., UNKNOWN -> CREATE_APPOINTMENT).
    # The merged_slots will be filtered later if intent changes, but only AFTER all slots are merged.
    merged_slots = session_slots.copy()

    # CRITICAL: Preserve raw service_id from session if Luma doesn't provide it
    # This ensures raw tenant value persists across turns
    raw_service_id_from_session = session_slots.get("service_id")
    canonical_service_id_from_session = session_slots.get(
        "_canonical_service_id")

    # Additively merge Luma slots into session slots
    # This is a true additive merge: {**existing, **new}
    # Luma slots are delta updates - they add new information or refine existing slots
    # But never delete slots that exist in session but not in Luma response
    for key, value in luma_slots.items():
        # Merge all non-None values from Luma (allows updates to existing slots)
        # This preserves session slots while allowing Luma to add/update
        # CRITICAL: If time is a dict (from time_constraint), extract start value
        if key == "time" and isinstance(value, dict):
            time_start = value.get("start")
            if time_start:
                merged_slots[key] = time_start
                logger.debug(
                    f"Normalized time slot from dict to start value: {time_start}")
            else:
                # Fallback: use dict as-is if no start
                merged_slots[key] = value
        elif value is not None:  # Only merge non-None values
            merged_slots[key] = value

    # CRITICAL: Preserve raw service_id from session if Luma didn't provide it
    # This ensures raw tenant value persists across turns
    if "service_id" not in luma_slots and raw_service_id_from_session:
        merged_slots["service_id"] = raw_service_id_from_session
        logger.debug(
            f"Preserved raw service_id from session: {raw_service_id_from_session}")

    # Preserve canonical service_id from session if present
    if canonical_service_id_from_session and "_canonical_service_id" not in merged_slots:
        merged_slots["_canonical_service_id"] = canonical_service_id_from_session
        logger.debug(
            f"Preserved canonical service_id from session: {canonical_service_id_from_session}")

    # Log merge for debugging
    session_slot_keys = set(session_slots.keys())
    luma_slot_keys = set(luma_slots.keys())
    merged_slot_keys = set(merged_slots.keys())
    added_slots = merged_slot_keys - session_slot_keys
    preserved_slots = session_slot_keys & merged_slot_keys

    logger.debug(
        f"Slot merge: session={list(session_slot_keys)}, luma={list(luma_slot_keys)}, "
        f"merged={list(merged_slot_keys)}, added={list(added_slots)}, preserved={list(preserved_slots)}"
    )

    # CRITICAL: Verify all session slots are preserved
    if session_slot_keys:
        lost_slots = session_slot_keys - merged_slot_keys
        if lost_slots:
            logger.error(
                f"[SLOT_DURABILITY] VIOLATION: Session slots lost during merge! "
                f"Lost slots: {list(lost_slots)}, "
                f"session_slots={list(session_slot_keys)}, "
                f"merged_slots={list(merged_slot_keys)}"
            )

    # CONTRACT ENFORCEMENT: Keep raw slots as-is (date, time, etc.)
    # DO NOT normalize or promote slots during merge - that happens in promotion layer
    # Raw slots are persisted exactly as provided by user/Luma
    # Promotion happens in-memory before computing missing_slots, never persisted

    # NEW BOOKING REQUEST DETECTION: Clear booking_id when user provides a new booking request
    # If CREATE_APPOINTMENT intent and Luma provides new booking slots (service_id, date, or time),
    # this indicates a new booking request, not a confirmation of the previous booking
    # Clear booking_id and availability_fingerprint to allow fresh booking flow
    merged_intent_name = merged.get("intent", {}).get(
        "name", "") if isinstance(merged.get("intent"), dict) else ""
    if merged_intent_name == "CREATE_APPOINTMENT" and "booking_id" in merged_slots:
        # Check if Luma provided new booking-related slots (indicating a new booking request)
        has_new_booking_slots = any(
            key in luma_slots and luma_slots.get(key) is not None
            for key in ["service_id", "date", "time"]
        )
        if has_new_booking_slots:
            # User provided a new booking request - clear previous booking_id and availability_fingerprint
            del merged_slots["booking_id"]
            # Also clear availability_fingerprint from session_state to force fresh availability search
            if session_state and "availability_fingerprint" in session_state:
                del session_state["availability_fingerprint"]
                logger.info(
                    f"Cleared availability_fingerprint due to new booking request"
                )
            logger.info(
                f"Cleared booking_id due to new booking request: "
                f"intent={merged_intent_name}, new_slots={[k for k in ['service_id', 'date', 'time'] if k in luma_slots]}"
            )

    # CONTRACT ENFORCEMENT: Lift explicit user-provided dates from context into slots
    # If context contains explicit date values (from user input), extract them into slots for persistence
    # This ensures dates don't disappear between turns
    context = merged.get("context", {})
    if isinstance(context, dict):
        # FIX 77: Priority order for date extraction:
        # 1. Extract from context.start_date as date (if date not already in merged_slots)
        # 2. Extract from context.date as date (if date not already in merged_slots)
        # 3. Extract from merged_slots.start_date as date (if start_date exists but date doesn't)
        # This ensures date persists across turns for promotion to start_date

        # Priority 1: Extract start_date from context as date (raw slot) for persistence
        if "start_date" in context and "date" not in merged_slots:
            # If context has start_date but slots don't have date, extract as date (raw slot)
            # Don't promote to start_date here - let derivation layer handle it
            # This ensures date persists across turns for promotion to start_date
            merged_slots["date"] = context["start_date"]
            logger.debug(
                f"[FIX77] Extracted date from context.start_date into slots for persistence: {context['start_date']}")

        # Priority 2: Direct date value in context (only if date not already extracted)
        if "date" in context and "date" not in merged_slots:
            merged_slots["date"] = context["date"]
            logger.debug(
                f"[FIX77] Extracted date from context.date into slots for persistence: {context['date']}")

        # Priority 3: Extract from merged_slots.start_date as date (if start_date was extracted from Luma but date wasn't)
        # This handles cases where start_date was extracted from Luma (line 288-291) but date wasn't
        # We need both for persistence (date) and promotion (start_date via date_roles)
        if "start_date" in merged_slots and "date" not in merged_slots:
            # Extract start_date value as date for persistence
            merged_slots["date"] = merged_slots["start_date"]
            logger.debug(
                f"[FIX77] Extracted date from merged_slots.start_date for persistence: {merged_slots['start_date']}")

        # Extract date_range from context if provided (e.g., "next week", "this weekend")
        if "date_range" in context and "date_range" not in merged_slots:
            merged_slots["date_range"] = context["date_range"]
            logger.debug(
                f"Extracted date_range from context.date_range into slots for persistence: {context['date_range']}")

        # Ensure date_roles are preserved in context for derivation layer
        if "date_roles" in context:
            # date_roles are metadata, keep in context (already merged above)
            pass

    # Slots are merged additively - no special routing needed
    # Users can provide missing slots in any order
    # CRITICAL: Use effective_intent (computed EARLY after UNKNOWN override) for all operations
    merged_intent_name = merged.get("_effective_intent", merged.get(
        "intent", {}).get("name", "") if isinstance(merged.get("intent"), dict) else "")

    # Update merged response with merged slots
    # CRITICAL: Ensure all session slots are preserved (non-destructive merge)
    # Slots are merged additively - no special routing needed
    merged["slots"] = merged_slots

    # Assertion: All session slots must be preserved in merged slots
    # ARCHITECTURAL INVARIANT: Slots are durable facts - they must never be lost
    if session_slots:
        missing_session_slots = set(
            session_slots.keys()) - set(merged_slots.keys())
        if missing_session_slots:
            logger.error(
                f"[SLOT_DURABILITY] VIOLATION: Session slots were lost during merge! "
                f"Missing: {list(missing_session_slots)}, "
                f"session_slots={list(session_slots.keys())}, "
                f"merged_slots={list(merged_slots.keys())}"
            )
            # Restore missing session slots (fail-safe)
            # This should never happen - if it does, it's a bug
            for key in missing_session_slots:
                merged_slots[key] = session_slots[key]
                logger.warning(
                    f"[SLOT_DURABILITY] Restored lost slot: {key} = {session_slots[key]}"
                )
            merged["slots"] = merged_slots

    # STEP 3.5: Re-inject service_id into booking.services for service bookings
    # When Luma returns datetime_range/time updates without repeating service,
    # we must preserve service_id from session and inject it into booking object
    # This ensures execution readiness checks see the service
    if merged_intent_name == "CREATE_APPOINTMENT":
        # Check if we have service_id in merged slots but booking.services is missing/empty
        service_id_in_slots = merged_slots.get("service_id")
        booking_obj = merged.get("booking")

        if service_id_in_slots:
            # Ensure booking object exists
            if not isinstance(booking_obj, dict):
                booking_obj = {}
                merged["booking"] = booking_obj

            # Check if booking.services is missing or empty
            booking_services = booking_obj.get("services")
            if not booking_services or (isinstance(booking_services, list) and len(booking_services) == 0):
                # Re-inject service_id into booking.services from merged slots
                booking_obj["services"] = [{"text": service_id_in_slots}]
                logger.debug(
                    f"Re-injected service_id into booking.services during merge: {service_id_in_slots}"
                )

    # STEP 4: Slot promotion and missing_slots computation
    # missing_slots is a PURE DERIVED VALUE - never persisted or mutated
    # Formula: missing_slots = required_slots(intent) - collected_slots (computed by planner)

    # Import central slot contract functions
    from core.orchestration.api.slot_contract import (
        filter_collected_slots_for_intent,
        promote_slots_for_intent
    )

    # Import planner for missing_slots computation
    from core.planning.policy.action_policy import plan_intent, load_planning_policy

    # STEP 3.6: Handle intent change (hard boundary)
    # ARCHITECTURAL INVARIANT: Intent change is a hard boundary
    # On intent change:
    # - Drop slots not valid for the new intent
    # - Preserve slots that overlap semantically (e.g., service_id if applicable)
    # - Recompute missing_slots from NEW intent contract ONLY
    #
    # CRITICAL MERGE ORDER: Session slots MUST be fully merged into merged_slots BEFORE
    # intent-change filtering is applied. This ensures valid slots from previous turns
    # (e.g., date from UNKNOWN intent) are preserved when transitioning to concrete intents.
    # The merge happens at line 814 (merged_slots = session_slots.copy()) and lines 826-840
    # (additive merge of Luma slots), so merged_slots contains all session slots at this point.

    session_intent_name = session_intent if isinstance(session_intent, str) else (
        session_intent.get("name", "") if isinstance(session_intent, dict) else "")
    intent_changed = (merged_intent_name and session_intent_name and
                      merged_intent_name != session_intent_name and
                      merged_intent_name != "UNKNOWN")

    if intent_changed:
        # LOG: previous intent and new intent
        logger.info(
            f"[INTENT_CHANGE] Intent changed: previous={session_intent_name} -> new={merged_intent_name}"
        )
        print(
            f"[INTENT_CHANGE] Intent changed: previous={session_intent_name} -> new={merged_intent_name}"
        )

        # CRITICAL: Ensure all session slots are in merged_slots before filtering
        # This is a defensive check to prevent slot loss during intent transitions
        # (e.g., UNKNOWN -> CREATE_APPOINTMENT where date should be preserved)
        if session_slots:
            missing_from_merge = set(
                session_slots.keys()) - set(merged_slots.keys())
            if missing_from_merge:
                logger.warning(
                    f"[INTENT_CHANGE] Session slots missing from merged_slots before filtering! "
                    f"Missing: {list(missing_from_merge)}, restoring..."
                )
                # Restore missing session slots before filtering
                for key in missing_from_merge:
                    merged_slots[key] = session_slots[key]
                    logger.info(
                        f"[INTENT_CHANGE] Restored session slot before filtering: {key} = {session_slots[key]}"
                    )

        # LOG: slots before filtering
        slots_before_filtering = merged_slots.copy()
        logger.info(
            f"[INTENT_CHANGE] Slots before filtering: {list(slots_before_filtering.keys())} = {slots_before_filtering}"
        )
        print(
            f"[INTENT_CHANGE] Slots before filtering: {list(slots_before_filtering.keys())} = {slots_before_filtering}"
        )

        # Intent changed - filter collected slots to remove invalid slots for new intent
        # CRITICAL: filter_collected_slots_for_intent must be strict
        # date/time slots from service intent must NOT leak into reservation intent
        # start_date/end_date must NOT satisfy service date implicitly
        # NOTE: merged_slots now contains all session slots (merged at line 814) plus Luma slots,
        # so filtering will preserve valid slots from both sources
        merged_slots = filter_collected_slots_for_intent(
            merged_slots, session_intent_name, merged_intent_name
        )

        # Update merged slots with filtered slots (raw slots only)
        merged["slots"] = merged_slots

        # LOG: slots after filtering
        logger.info(
            f"[INTENT_CHANGE] Slots after filtering: {list(merged_slots.keys())} = {merged_slots}"
        )
        print(
            f"[INTENT_CHANGE] Slots after filtering: {list(merged_slots.keys())} = {merged_slots}"
        )

        # Log dropped slots for debugging
        dropped_slots = set(slots_before_filtering.keys()
                            ) - set(merged_slots.keys())
        if dropped_slots:
            logger.info(
                f"[INTENT_CHANGE] Dropped slots: {list(dropped_slots)}"
            )
            print(
                f"[INTENT_CHANGE] Dropped slots: {list(dropped_slots)}"
            )

        # Intent changed - no awaiting_slot to reset (removed from design)

        # Clear context.date_roles on intent change (they are intent-specific)
        # Old intent's date_roles should not leak to new intent
        context = merged.get("context", {})
        if isinstance(context, dict) and "date_roles" in context:
            # Remove date_roles on intent change to force fresh derivation
            del context["date_roles"]
            merged["context"] = context
            logger.debug(
                "[INTENT_CHANGE] Cleared date_roles (intent-specific)")

        # Delete stale missing_slots - will be recomputed from NEW intent contract ONLY
        # CRITICAL: Do NOT use old intent's missing_slots
        if "missing_slots" in merged:
            del merged["missing_slots"]
        # Mark for recomputation with new intent contract
        merged["_force_recompute_missing_slots"] = True
        logger.debug(
            "[INTENT_CHANGE] Marked missing_slots for recomputation from new intent contract")

    # STEP 3.4.1: Detect and persist modification context for MODIFY_* intents
    # CRITICAL: raw_luma_slots must be available here (set at line 86)
    # CRITICAL: This must run BEFORE informational-turn early return and BEFORE slot promotion
    # This ensures modification context is available even when slots are empty
    # Modification context is INTENT-DRIVEN, not slot-driven
    # It uses raw_luma_slots (available before promotion) to detect modification type
    # If raw_luma_slots are empty, still set default context for MODIFY_* intents

    modification_context = None
    if merged_intent_name == "MODIFY_BOOKING":
        # Detect modification type from raw_luma_slots (before promotion)
        # This is intent-driven: we detect MODIFY_BOOKING intent, then check for signals
        has_time = "time" in raw_luma_slots and raw_luma_slots.get(
            "time") is not None
        has_date = "date" in raw_luma_slots and raw_luma_slots.get(
            "date") is not None

        # Always set modification context for MODIFY_BOOKING (intent-driven)
        # If no slots detected, set default context that will be refined later
        modification_context = {
            "modifying_time": has_time,
            "modifying_date": has_date
        }
        # Persist modification context to merged response (will be persisted to session)
        merged["_modification_context"] = modification_context

    elif merged_intent_name == "MODIFY_RESERVATION":
        # Detect modification type from raw_luma_slots (before promotion)
        # This is intent-driven: we detect MODIFY_RESERVATION intent, then check for signals
        has_start_date = "start_date" in raw_luma_slots and raw_luma_slots.get(
            "start_date") is not None
        has_end_date = "end_date" in raw_luma_slots and raw_luma_slots.get(
            "end_date") is not None
        has_date = "date" in raw_luma_slots and raw_luma_slots.get(
            "date") is not None

        # Always set modification context for MODIFY_RESERVATION (intent-driven)
        # If no slots detected, set default context that will be refined later
        modification_context = {
            "modifying_start_date": has_start_date,
            "modifying_end_date": has_end_date,
            "modifying_date": has_date
        }
        # Persist modification context to merged response (will be persisted to session)
        merged["_modification_context"] = modification_context

    # If no modification context detected in current turn, check session for persisted context
    if not modification_context and session_state:
        persisted_context = session_state.get("_modification_context")
        if persisted_context:
            modification_context = persisted_context
            merged["_modification_context"] = modification_context

    # TRACE 2 will be added after promoted_slots and effective_collected_slots are computed

    # STEP 3.5: Detect informational turns explicitly
    # ARCHITECTURAL INVARIANT: Informational turns must NEVER mutate slots or recompute missing_slots
    # If no new slots are provided, preserve previous session.slots and missing_slots
    core_intents = {"CREATE_APPOINTMENT", "CREATE_RESERVATION",
                    "MODIFY_BOOKING", "CANCEL_BOOKING"}

    # Check if current turn is informational (non-core intent)
    is_informational_intent = (
        merged_intent_name and
        merged_intent_name not in core_intents and
        merged_intent_name != "UNKNOWN"
    )

    # Check if session has active planning state that should be preserved
    has_active_planning = (
        session_state and
        isinstance(session_state, dict) and
        session_state.get("status") == "NEEDS_CLARIFICATION" and
        session_intent_name and
        session_intent_name in core_intents
    )

    # Check if current turn provides no new slot values (informational behavior)
    # Compare merged_slots (which includes session slots) with session slots
    session_slots_dict = session_state.get("slots", {}) if (
        session_state and isinstance(session_state, dict)) else {}
    current_turn_has_new_slots = bool(
        merged_slots and
        any(key not in session_slots_dict for key in merged_slots)
    )

    # Informational turn: has active planning AND (informational intent OR no new slots)
    is_informational_turn = (
        has_active_planning and
        (is_informational_intent or not current_turn_has_new_slots)
    )

    # For informational turns with no new slots: preserve everything and skip promotion/recomputation
    # CRITICAL: For MODIFY_* intents, disable informational-turn early return
    # Required-slot computation MUST always run, even when has_new_slots=False
    # This ensures modification_context can properly override base planning slots
    is_modify_intent = merged_intent_name in (
        "MODIFY_BOOKING", "MODIFY_RESERVATION")
    if is_informational_turn and not current_turn_has_new_slots and not is_modify_intent:
        # LOG: detected informational turn
        logger.info(
            f"[INFORMATIONAL_TURN] Detected informational turn: "
            f"luma_intent={merged_intent_name}, session_intent={session_intent_name}, "
            f"has_new_slots=False"
        )
        print(
            f"[INFORMATIONAL_TURN] Detected informational turn: "
            f"luma_intent={merged_intent_name}, session_intent={session_intent_name}, "
            f"has_new_slots=False"
        )

        # Preserve previous session.slots (do NOT mutate)
        # merged_slots already contains session slots from earlier merge, but ensure it's complete
        if session_state and isinstance(session_state, dict):
            session_slots_to_preserve = session_state.get("slots", {})
            if isinstance(session_slots_to_preserve, dict):
                # Ensure all session slots are in merged_slots (defensive)
                for slot_name, slot_value in session_slots_to_preserve.items():
                    if slot_name not in merged_slots:
                        merged_slots[slot_name] = slot_value
                merged["slots"] = merged_slots

                # LOG: preserved slots
                logger.info(
                    f"[INFORMATIONAL_TURN] Preserved slots: {list(session_slots_to_preserve.keys())}"
                )
                print(
                    f"[INFORMATIONAL_TURN] Preserved slots: {list(session_slots_to_preserve.keys())}"
                )

        # Preserve previous missing_slots by recomputing from session slots only
        # CRITICAL: Do NOT use Luma slots or promotion for informational turns
        # missing_slots must remain unchanged unless new slots are added
        # Since missing_slots are not persisted, we recompute from session slots only
        # This effectively preserves the previous state since no new slots were added
        # CRITICAL: Use modification context from merged response (detected earlier) or session
        previous_missing_slots = []
        if session_state and isinstance(session_state, dict) and session_intent_name:
            # Compute missing_slots from session slots only (no promotion, no Luma slots)
            # This preserves the previous missing_slots state
            # Use modification context from merged response (detected before informational turn check) or session
            modification_context = merged.get("_modification_context")
            if not modification_context:
                modification_context = session_state.get(
                    "_modification_context")
            previous_missing_slots = compute_missing_slots(
                session_intent_name,
                session_slots_dict,
                modification_context,
                session_state
            )

        # INVARIANT CHECK: missing_slots must be a list
        assert isinstance(previous_missing_slots, list), (
            f"missing_slots must be a list, got {type(previous_missing_slots)}: {previous_missing_slots}"
        )

        # INVARIANT CHECK: missing_slots must never be None
        assert previous_missing_slots is not None, (
            "missing_slots must not be None after computation"
        )

        merged["missing_slots"] = previous_missing_slots

        # LOG: preserved missing_slots
        logger.info(
            f"[INFORMATIONAL_TURN] Preserved missing_slots: {previous_missing_slots}"
        )
        print(
            f"[INFORMATIONAL_TURN] Preserved missing_slots: {previous_missing_slots}"
        )

        # Skip promotion and recomputation - return early with preserved state
        # Store effective collected slots for consistency (from session slots only)
        effective_collected_slots = {
            slot_name: slot_value
            for slot_name, slot_value in session_slots_dict.items()
            if slot_value is not None
        }
        merged["_effective_collected_slots"] = effective_collected_slots

        return merged

    # Use effective_intent computed EARLY (after UNKNOWN override)
    # This ensures effective_intent is resolved BEFORE planning/slot computation
    effective_intent = merged.get("_effective_intent")
    if not effective_intent:
        # Fallback: read from intent["name"] (which should be set by early computation)
        intent_obj = merged.get("intent", {})
        if isinstance(intent_obj, dict):
            effective_intent = intent_obj.get("name", "")
        else:
            effective_intent = merged_intent_name
        if not effective_intent:
            logger.error(
                f"merge_luma_with_session: CRITICAL - effective_intent is empty! "
                f"luma_intent={luma_intent_name}, session_intent={session_intent_name}, "
                f"merged_intent_name={merged_intent_name}"
            )
        else:
            logger.warning(
                f"merge_luma_with_session: _effective_intent not set, using intent['name']={effective_intent}"
            )

    # For informational turns WITH new slots: use session intent but still process normally
    if is_informational_turn and current_turn_has_new_slots:
        # Informational turn but with new slots - use session intent for missing_slots computation
        # but still go through promotion and recomputation with new slots
        effective_intent = session_intent_name

        # LOG: detected informational turn with new slots
        logger.info(
            f"[INFORMATIONAL_TURN] Detected informational turn with new slots: "
            f"luma_intent={merged_intent_name}, session_intent={session_intent_name}, "
            f"new_slots={[k for k in merged_slots.keys() if k not in session_slots_dict]}"
        )
        print(
            f"[INFORMATIONAL_TURN] Detected informational turn with new slots: "
            f"luma_intent={merged_intent_name}, session_intent={session_intent_name}, "
            f"new_slots={[k for k in merged_slots.keys() if k not in session_slots_dict]}"
        )

    # Update merged with final effective_intent for downstream use
    merged["_effective_intent"] = effective_intent

    # STEP 4.1: Promote slots (in-memory, non-persistent)
    # Promotion happens BEFORE computing missing_slots but is NEVER persisted
    # ARCHITECTURAL INVARIANT: Promotion must start from merged session slots, not raw Luma slots
    # Promotion must NEVER remove an existing slot - it is additive only
    # FIX 3: Merge session context (including date_roles) into merged context for derivation
    # This ensures date_roles persist across turns for correct derivation
    context = merged.get("context", {})
    if not isinstance(context, dict):
        context = {}

    # CRITICAL: Merge session context (including date_roles) into merged context for derivation
    # This ensures date_roles persist across turns for correct derivation
    if session_state and isinstance(session_state, dict):
        session_context = session_state.get("context", {})
        if isinstance(session_context, dict):
            # Merge all context from session (date_roles, etc.)
            # This ensures metadata persists across turns
            for key, value in session_context.items():
                # Preserve session context values if not overridden by current turn
                # This is especially important for date_roles
                if key not in context or not context.get(key):
                    context[key] = value
            if "date_roles" in session_context:
                logger.debug(
                    f"Merged date_roles from session context: {session_context['date_roles']}")

    # Update merged context with merged context (for downstream use)
    merged["context"] = context

    # CRITICAL: Promotion starts from merged_slots (session slots + luma slots)
    # This ensures all session slots are available for promotion
    # Promotion is additive - it never removes existing slots
    merged_session_slots = merged_slots.copy()
    promoted_slots = promote_slots_for_intent(
        merged_slots, effective_intent, context)

    # CRITICAL: Verify promotion didn't remove any existing slots
    merged_slot_keys = set(merged_slots.keys())
    promoted_slot_keys = set(promoted_slots.keys())
    if merged_slot_keys:
        lost_in_promotion = merged_slot_keys - promoted_slot_keys
        if lost_in_promotion:
            logger.error(
                f"[SLOT_DURABILITY] VIOLATION: Slots lost during promotion! "
                f"Lost slots: {list(lost_in_promotion)}, "
                f"merged_slots={list(merged_slot_keys)}, "
                f"promoted_slots={list(promoted_slot_keys)}"
            )

    # STEP 4.1.1: Modification context already detected earlier (before informational turn check)
    # Use the modification context that was detected and persisted to merged["_modification_context"]
    # This ensures modification context is available for persistence (planner doesn't need it)
    modification_context = merged.get("_modification_context")
    if not modification_context and session_state:
        # Fallback: check session for persisted context (shouldn't be needed if detection worked)
        modification_context = session_state.get("_modification_context")
        if modification_context:
            merged["_modification_context"] = modification_context
            logger.debug(
                f"[SESSION_MERGE] Using persisted modification context from session (fallback): {modification_context}")

    # CRITICAL: Promotion MUST write into session.slots
    # After promotion, merge promoted slots back into merged["slots"] so they get persisted
    # This ensures promoted slots (e.g., date_range → start_date, end_date) are durable
    # Promotion is additive - it adds derived slots but never removes existing ones
    merged["slots"] = promoted_slots
    merged_slots = promoted_slots  # Update merged_slots to include promoted slots

    # STEP 4.1.5: Apply domain slot filtering BEFORE required-slot computation
    # CRITICAL: Domain filtering must happen BEFORE:
    #   - required-slot computation
    #   - missing-slot computation
    #   - role inference
    # This prevents cross-domain slot leakage (e.g., service_id in reservation missing_slots)
    # PLANNING-ONLY: Skip domain filtering when planning_only=True
    # Still in slot_contract
    from core.orchestration.api.slot_contract import filter_slots_by_domain
    domain_filtered_slots = filter_slots_by_domain(
        promoted_slots, effective_intent, planning_only=planning_only)

    # Slots are treated as an unordered, additive map
    # No special routing needed - users can provide missing slots in any order
    effective_slots_for_computation = domain_filtered_slots.copy()

    # STEP 4.2: Compute missing_slots ONCE per turn (pure derived value)
    # ARCHITECTURAL INVARIANT: missing_slots = REQUIRED_SLOTS(intent) - effective_slots.keys()
    # missing_slots is computed exactly once per turn and MUST NOT be recomputed later
    # missing_slots = [] is VALID and means all required slots are satisfied
    # On intent change: recompute missing_slots from NEW intent contract ONLY
    # CRITICAL: missing_slots is computed from effective_slots (domain-filtered, date-stripped for reservations)
    # A slot is satisfied ONLY if it exists in effective_slots under its exact slot name
    # - time does NOT satisfy date
    # - date does NOT satisfy time
    # - start_date does NOT satisfy end_date
    # - date_range satisfies NOTHING unless explicitly promoted
    # - generic 'date' does NOT satisfy start_date/end_date for CREATE_RESERVATION

    # Check if this is an intent change (force recomputation from new intent)
    is_intent_change_recomputation = merged.get(
        "_force_recompute_missing_slots", False)

    # Use effective_slots_for_computation (domain-filtered, date-stripped for reservations)
    # This is the current-turn effective slot view: merge(session.slots, promoted_current_turn_slots)
    # after domain filtering and reservation date stripping
    durable_slots_for_computation = effective_slots_for_computation

    # Compute missing_slots from durable slots (session.slots after promotion)
    # Formula: missing_slots = REQUIRED_SLOTS(intent) - durable_slots.keys()
    # CRITICAL: A slot is satisfied ONLY if it exists in durable_slots under its exact slot name
    # No inference, no type-based satisfaction, no sibling slot satisfaction
    # CRITICAL: On intent change, this uses the NEW intent contract (effective_intent = new intent)
    #
    # CRITICAL: For MODIFY_* intents, modification_context (detected from current turn or persisted)
    # Planner handles missing_slots computation (no modification_context needed)

    # Use planner to compute missing_slots
    # CRITICAL: For planning, use canonical service_id if present, otherwise use raw service_id
    # Planning logic (required_slots, availability, confirmation) MUST use canonical value
    slots_for_planning = durable_slots_for_computation.copy()
    if "_canonical_service_id" in slots_for_planning:
        # Use canonical for planning, but keep raw in slots_for_planning for outcome
        # Replace service_id with canonical for planning computation only
        slots_for_planning["service_id"] = slots_for_planning["_canonical_service_id"]
        logger.debug(
            f"Using canonical service_id for planning: {slots_for_planning['service_id']}")

    logger.debug(
        f"[SESSION_MERGE] Computing missing_slots with planner: "
        f"effective_intent={effective_intent}, "
        f"durable_slots_keys={list(durable_slots_for_computation.keys()) if durable_slots_for_computation else []}, "
        f"slots_for_planning_keys={list(slots_for_planning.keys()) if slots_for_planning else []}"
    )

    # HARD INVARIANT CHECK (test/debug only): Capture variables and check if Luma slots are dropped
    # This must run at the exact entry point of required-slot computation
    # Note: raw_luma_slots was captured at line 87 (original Luma slots before extraction/modification)
    # Note: os is already imported at module level (line 12)
    if os.getenv("PYTEST_CURRENT_TEST") or os.getenv("DEBUG_SLOT_DROP") == "1":
        # Capture variables: raw_luma_slots, merged_slots, session_slots, intent
        # Use _raw_luma_slots from merged (captured at line 87, original Luma output)
        raw_luma_slots = merged.get("_raw_luma_slots", {})
        if not isinstance(raw_luma_slots, dict):
            raw_luma_slots = {}

        merged_slots = durable_slots_for_computation
        if not isinstance(merged_slots, dict):
            merged_slots = {}

        session_slots = session_state.get("slots", {}) if session_state else {}
        if not isinstance(session_slots, dict):
            session_slots = {}

        intent = effective_intent

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
    # Slots are treated as an unordered, additive map
    policy = load_planning_policy()
    plan = plan_intent(effective_intent, durable_slots_for_computation, policy)
    missing_slots = plan["missing_slots"]

    # APPOINTMENT INTENT RULE: time_constraint satisfies the time requirement
    # APPOINTMENT INTENT RULE: Only exact time_constraint satisfies the time requirement
    # mode=exact → satisfies time, mode=fuzzy/window → does NOT satisfy time
    time_constraint = luma_response.get("time_constraint")
    if effective_intent == "CREATE_APPOINTMENT" and time_constraint is not None:
        # Check if time_constraint mode is exact (only exact satisfies time requirement)
        time_constraint_mode = None
        if isinstance(time_constraint, dict):
            time_constraint_mode = time_constraint.get("mode")

        # Only remove "time" from missing_slots if mode is exact
        if time_constraint_mode == "exact":
            if "time" in missing_slots:
                missing_slots = [s for s in missing_slots if s != "time"]
                logger.info(
                    f"[MISSING_SLOTS] time_constraint (mode=exact) satisfies time for CREATE_APPOINTMENT - removed 'time' from missing_slots"
                )
        else:
            # Fuzzy/window time_constraint does NOT satisfy time requirement
            logger.debug(
                f"[MISSING_SLOTS] time_constraint (mode={time_constraint_mode}) does NOT satisfy time for CREATE_APPOINTMENT - keeping 'time' in missing_slots"
            )

    # MISSING_SLOTS_DECISION: Log missing slots computation decision
    from core.planning.orchestration.missing_slots import get_planning_required_slots_for_intent as get_required_slots_for_intent
    required_slots = get_required_slots_for_intent(effective_intent)
    logger.info("[MISSING_SLOTS_DECISION] user_id=%s intent=%s required_slots=%s slots_used=%s missing_slots=%s",
                user_id, effective_intent, required_slots, list(durable_slots_for_computation.keys()), missing_slots)

    # FIX: MODIFY_BOOKING: recompute missing_slots using Luma issues when extracted slots are empty
    # When intent is MODIFY_BOOKING and raw_luma_slots is empty/null:
    # derive missing_slots from merged_luma_response.issues keys (normalized), not from modification_context
    if effective_intent == "MODIFY_BOOKING":
        raw_luma_slots_for_check = merged.get("_raw_luma_slots", {})
        if not raw_luma_slots_for_check or len(raw_luma_slots_for_check) == 0:
            # raw_luma_slots is empty - check if Luma provided issues
            issues = merged.get("issues", {})
            if isinstance(issues, dict) and issues:
                # Derive missing_slots from issues keys (normalized)
                # Issues keys like "time: missing" should map to "time" in missing_slots
                issues_missing_slots = []
                for key in issues.keys():
                    # Normalize issue key to slot name
                    # Handle formats like "time: missing", "date: missing", or just "time", "date"
                    normalized_key = key.split(":")[0].strip().lower()
                    if normalized_key in ["date", "time", "booking_id"]:
                        issues_missing_slots.append(normalized_key)

                if issues_missing_slots:
                    # Ensure booking_id is always included for MODIFY_BOOKING
                    if "booking_id" not in issues_missing_slots:
                        issues_missing_slots.append("booking_id")

                    # Use issues-derived missing_slots instead of computed ones
                    missing_slots = sorted(list(set(issues_missing_slots)))
                    logger.info(
                        f"[MODIFY_BOOKING_ISSUES] Derived missing_slots from Luma issues: {missing_slots} "
                        f"(raw_luma_slots was empty, issues={list(issues.keys())})"
                    )
                    print(
                        f"[MODIFY_BOOKING_ISSUES] Derived missing_slots from Luma issues: {missing_slots} "
                        f"(raw_luma_slots was empty, issues={list(issues.keys())})"
                    )

    logger.debug(
        f"[SESSION_MERGE] After planner: missing_slots={missing_slots}"
    )

    # LOG: recomputed missing_slots (especially important for intent changes)
    if is_intent_change_recomputation:
        logger.info(
            f"[INTENT_CHANGE] Recomputed missing_slots from NEW intent contract: {missing_slots}"
        )
        print(
            f"[INTENT_CHANGE] Recomputed missing_slots from NEW intent contract: {missing_slots}"
        )

    # Normalize MODIFY_BOOKING missing_slots (test contract)
    # Import here to avoid circular dependency
    from core.orchestration.nlu.luma_response_processor import _normalize_modify_booking_missing_slots
    missing_slots = _normalize_modify_booking_missing_slots(
        missing_slots, merged)

    # INVARIANT CHECK: missing_slots must be a list
    assert isinstance(missing_slots, list), (
        f"missing_slots must be a list, got {type(missing_slots)}: {missing_slots}"
    )

    # INVARIANT CHECK: missing_slots must never be None after computation
    assert missing_slots is not None, (
        "missing_slots must not be None after computation"
    )

    # INVARIANT CHECK: If a slot was satisfied in a previous turn and is in session.slots,
    # it MUST NOT reappear in missing_slots
    if session_state and isinstance(session_state, dict):
        previous_slots = session_state.get("slots", {})
        if isinstance(previous_slots, dict):
            previous_slot_keys = set(previous_slots.keys())
            missing_slots_set = set(missing_slots)
            satisfied_but_missing = previous_slot_keys & missing_slots_set
            if satisfied_but_missing:
                logger.error(
                    f"[SLOT_SATISFACTION] VIOLATION: Previously satisfied slots reappeared in missing_slots! "
                    f"satisfied_but_missing={list(satisfied_but_missing)}, "
                    f"previous_slots={list(previous_slot_keys)}, "
                    f"durable_slots={list(durable_slots_for_computation.keys())}, "
                    f"missing_slots={missing_slots}"
                )
                # This is a critical invariant violation - fail fast
                assert False, (
                    f"Previously satisfied slots reappeared in missing_slots: {list(satisfied_but_missing)}. "
                    f"This violates the slot durability invariant."
                )

    # Set missing_slots in merged response (for plan building)
    # Set missing_slots in merged response (for plan building)
    # NOTE: missing_slots computed here is for planning purposes
    # It will be recomputed from persisted slots in build_session_state_from_outcome
    # to ensure it reflects what's actually persisted, not pre-persistence state
    # The recomputed missing_slots will then be persisted to session_state
    merged["missing_slots"] = missing_slots

    logger.debug(
        f"[SESSION_MERGE] After setting missing_slots: "
        f"merged['missing_slots']={merged.get('missing_slots')}, "
        f"merged['slots'].keys()={list(merged.get('slots', {}).keys())}"
    )

    # Remove force recompute flag (no longer needed after computation)
    if "_force_recompute_missing_slots" in merged:
        del merged["_force_recompute_missing_slots"]

    # ARCHITECTURAL FIX: Store effective collected slots (post-promotion) for persistence
    # These are the slots that actually satisfy required slots after promotion
    # This ensures slots explicitly satisfied in a turn are persisted so they're not re-computed as missing
    effective_collected_slots = _compute_effective_collected_slots_internal(
        promoted_slots, effective_intent, planning_only=planning_only
    )
    merged["_effective_collected_slots"] = effective_collected_slots

    # CONTRACT ENFORCEMENT: missing_slots are computed fresh from intent contract
    # When intent is CREATE_RESERVATION, required slots are ["service_id", "start_date", "end_date"]
    # When intent changes, collected slots are filtered to prevent cross-domain leakage
    # missing_slots = required_slots - collected_slots (computed fresh every turn)

    # Assertion: session.intent determines planner path exclusively
    # Verify that merged intent matches session intent (when session exists and not reset)
    # CRITICAL: UNKNOWN is a placeholder intent and must be allowed to materialize into concrete intents
    # Only enforce equality for concrete session intents (not UNKNOWN)
    merged_intent = merged.get("intent", {})
    merged_intent_name = merged_intent.get(
        "name", "") if isinstance(merged_intent, dict) else ""
    if session_intent and session_status != "READY":
        session_intent_str = session_intent if isinstance(
            session_intent, str) else session_intent.get("name", "")
        # Relax assertion for UNKNOWN → concrete intent materialization
        # UNKNOWN is a placeholder and must be allowed to upgrade to concrete intents
        # Only enforce equality when session intent is concrete (safety check for concrete→concrete mismatches)
        if session_intent_str != "UNKNOWN":
            assert merged_intent_name == session_intent_str, (
                f"Session intent mismatch: session.intent={session_intent_str}, "
                f"merged.intent={merged_intent_name}. Session intent must determine planner path exclusively."
            )

    return merged


# Backward compatibility alias
merge_session_with_luma_response = merge_luma_with_session


def _compute_effective_collected_slots_internal(
    promoted_slots: Dict[str, Any],
    effective_intent: str,
    planning_only: bool = False
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
        get_required_slots_for_intent,
        filter_slots_by_domain
    )

    # CRITICAL: Filter slots by domain BEFORE computing effective_collected_slots
    # This prevents cross-domain slot leakage (e.g., service_id in reservation missing_slots)
    # PLANNING-ONLY: Skip domain filtering when planning_only=True
    domain_filtered_slots = filter_slots_by_domain(
        promoted_slots, effective_intent, planning_only=planning_only)

    # Slots are treated as an unordered, additive map
    # No special routing needed - users can provide missing slots in any order
    effective_slots_for_filtering = domain_filtered_slots

    required_slots_set = set(get_required_slots_for_intent(effective_intent))

    print(
        f"  effective_slots_for_filtering.keys()={list(effective_slots_for_filtering.keys())}")
    print(f"  promoted_slots={promoted_slots}")

    # Filter to only slots that satisfy required slots
    effective_collected_slots = {
        slot_name: slot_value
        for slot_name, slot_value in effective_slots_for_filtering.items()
        if slot_name in required_slots_set and slot_value is not None
    }

    # Also include service_id if present (common across intents)
    # But only if it's valid for the domain (already filtered by filter_slots_by_domain)
    if "service_id" in effective_slots_for_filtering and effective_slots_for_filtering["service_id"] is not None:
        effective_collected_slots["service_id"] = effective_slots_for_filtering["service_id"]

    print(
        f"  effective_collected_slots (after filter)={effective_collected_slots}")
    print(
        f"  effective_collected_slots.keys()={list(effective_collected_slots.keys())}")

    return effective_collected_slots


def _compute_effective_collected_slots(luma_response: Dict[str, Any], planning_only: bool = False) -> Dict[str, Any]:
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
    intent_name = intent_obj.get(
        "name", "") if isinstance(intent_obj, dict) else ""

    # TRACE 1: Immediately after intent resolution (before any early return)
    # json is already imported at module level (line 10)
    print(json.dumps({
        "trace_point": "AFTER_INTENT",
        "intent": intent_name,
        "is_first_turn": True,
        "raw_luma_slots": luma_response.get("slots"),
        "raw_luma_context": luma_response.get("context"),
    }))

    if not intent_name:
        # No intent - no effective slots
        luma_response["_effective_collected_slots"] = {}
        return luma_response

    # FACT-ONLY: Promote facts to slots BEFORE any processing
    # This ensures facts.service_id, facts.times, etc. are available for planning
    from core.orchestration.luma_facts_adapter import facts_to_slots
    facts_obj = luma_response.get("facts", {})
    # Get intent for date_range promotion (CREATE_RESERVATION with 2+ dates)
    intent_obj = luma_response.get("intent", {})
    intent_name = intent_obj.get(
        "name", "") if isinstance(intent_obj, dict) else ""
    promoted_slots_from_facts = facts_to_slots(
        facts_obj,
        intent_name=intent_name,
        source_text=luma_response.get("_source_text"),
    ) if isinstance(facts_obj, dict) else {}

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
    raw_slots = {**nested_slots, **promoted_slots_from_facts}
    if isinstance(nested_slots, dict) and "service_id" in nested_slots and "service_id" in promoted_slots_from_facts:
        raw_slots["service_id"] = nested_slots["service_id"]
    if not isinstance(raw_slots, dict):
        raw_slots = {}

    # STEP: Detect and persist modification context for MODIFY_* intents (BEFORE promotion)
    # CRITICAL: This must run for first turns (no session) to ensure modification context is persisted
    # Modification context is INTENT-DRIVEN, not slot-driven
    print(
        f"[_compute_effective_collected_slots] raw_slots keys={list(raw_slots.keys())}")

    modification_context = None
    if intent_name == "MODIFY_BOOKING":
        # Detect modification type from raw_slots (before promotion)
        has_time = "time" in raw_slots and raw_slots.get("time") is not None
        has_date = "date" in raw_slots and raw_slots.get("date") is not None

        # Always set modification context for MODIFY_BOOKING (intent-driven)
        modification_context = {
            "modifying_time": has_time,
            "modifying_date": has_date
        }
        luma_response["_modification_context"] = modification_context

    elif intent_name == "MODIFY_RESERVATION":
        # Detect modification type from raw_slots (before promotion)
        has_start_date = "start_date" in raw_slots and raw_slots.get(
            "start_date") is not None
        has_end_date = "end_date" in raw_slots and raw_slots.get(
            "end_date") is not None
        has_date = "date" in raw_slots and raw_slots.get("date") is not None

        # Always set modification context for MODIFY_RESERVATION (intent-driven)
        modification_context = {
            "modifying_start_date": has_start_date,
            "modifying_end_date": has_end_date,
            "modifying_date": has_date
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
    print(json.dumps({
        "trace_point": "AFTER_MOD_CONTEXT",
        "intent": intent_name,
        "modification_context": luma_response.get("_modification_context"),
        "promoted_slots": promoted_slots,
        "effective_collected_slots": effective_collected_slots,
    }))

    # CRITICAL: Also compute missing_slots for first turns (no session)
    # ARCHITECTURAL INVARIANT: missing_slots = REQUIRED_SLOTS(intent) - durable_slots.keys()
    # missing_slots must be computed from durable slots (after promotion writes into slots)
    # A slot is satisfied ONLY if it exists in durable_slots under its exact slot name
    # Use planner for missing_slots computation
    from core.planning.policy.action_policy import plan_intent, load_planning_policy

    # BEFORE_REQUIRED_SLOTS: Log right before required-slot computation (first turn)
    before_required_slots_log = {
        "trace": "BEFORE_REQUIRED_SLOTS",
        "intent": intent_name,
        "slots_used": promoted_slots,
        "session_slots": None,  # No session on first turn
        "modification_context": modification_context
    }
    logger.info("BEFORE_REQUIRED_SLOTS: %s", json.dumps(
        before_required_slots_log, ensure_ascii=False, default=str))
    print(
        f"\n[BEFORE_REQUIRED_SLOTS] {json.dumps(before_required_slots_log, ensure_ascii=False, default=str)}")

    # HARD INVARIANT CHECK (test/debug only): Capture variables and check if Luma slots are dropped
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
    plan = plan_intent(intent_name, promoted_slots, policy)
    missing_slots = plan["missing_slots"]

    # APPOINTMENT INTENT RULE: time_constraint satisfies the time requirement
    # APPOINTMENT INTENT RULE: Only exact time_constraint satisfies the time requirement
    # mode=exact → satisfies time, mode=fuzzy/window → does NOT satisfy time
    # This must happen BEFORE plan status is set and BEFORE session persistence
    time_constraint = luma_response.get("time_constraint")
    if intent_name == "CREATE_APPOINTMENT" and time_constraint is not None:
        # Check if time_constraint mode is exact (only exact satisfies time requirement)
        time_constraint_mode = None
        if isinstance(time_constraint, dict):
            time_constraint_mode = time_constraint.get("mode")

        # Only remove "time" from missing_slots if mode is exact
        if time_constraint_mode == "exact":
            if "time" in missing_slots:
                missing_slots = [s for s in missing_slots if s != "time"]
                logger.info(
                    f"[MISSING_SLOTS] time_constraint (mode=exact) satisfies time for CREATE_APPOINTMENT - removed 'time' from missing_slots"
                )
        else:
            # Fuzzy/window time_constraint does NOT satisfy time requirement
            logger.debug(
                f"[MISSING_SLOTS] time_constraint (mode={time_constraint_mode}) does NOT satisfy time for CREATE_APPOINTMENT - keeping 'time' in missing_slots"
            )

    # Normalize MODIFY_BOOKING missing_slots (test contract)
    from core.orchestration.nlu.luma_response_processor import _normalize_modify_booking_missing_slots
    missing_slots = _normalize_modify_booking_missing_slots(
        missing_slots, luma_response)

    # INVARIANT CHECK: missing_slots must be a list
    assert isinstance(missing_slots, list), (
        f"missing_slots must be a list, got {type(missing_slots)}: {missing_slots}"
    )

    # INVARIANT CHECK: missing_slots must never be None after computation
    assert missing_slots is not None, (
        "missing_slots must not be None after computation"
    )

    # Store in response
    luma_response["_effective_collected_slots"] = effective_collected_slots
    luma_response["missing_slots"] = missing_slots

    # LOG: computed missing_slots for first turn
    # Note: logger is already defined at module level (line 14), no need to redefine
    logger.info(
        f"[MISSING_SLOTS] Computed missing_slots for first turn: intent={intent_name}, missing_slots={missing_slots}"
    )
    print(
        f"[MISSING_SLOTS] Computed missing_slots for first turn: intent={intent_name}, missing_slots={missing_slots}"
    )

    return luma_response


def build_session_state_from_outcome(
    outcome: Dict[str, Any],
    outcome_status: str,
    merged_luma_response: Optional[Dict[str, Any]] = None,
    previous_session_state: Optional[Dict[str, Any]] = None,
    user_id: Optional[str] = None,
    session_store: Optional[Any] = None
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
            outcome_intent = outcome.get(
                "intent_name") or outcome.get("intent")
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
            f"[ERROR] build_session_state_from_outcome: outcome is None or not a dict: {outcome}")
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
            f"[ERROR] build_session_state_from_outcome: outcome has no intent_name or intent: {outcome}")
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
                effective_slots = merged_luma_response.get("_effective_collected_slots", {})
                if isinstance(effective_slots, dict) and effective_slots:
                    slots = effective_slots
                    logger.info(
                        f"[SESSION_MERGE] Using _effective_collected_slots as fallback (slots was empty): {list(slots.keys())}"
                    )
    except Exception as e:
        print(
            f"[ERROR] build_session_state_from_outcome: Exception accessing merged_luma_response: {e}")
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
                f"[SESSION_MERGE] Using outcome.slots as fallback (merged_luma_response is None): {list(slots.keys())}")

    # Extract intent - HARD RULE: Explicit precedence order for intent during persistence
    # DURABLE INTENTS RULE: Only durable intents may be persisted
    # INVARIANT: A durable intent MUST NEVER be wiped once established
    #
    # Explicit precedence order:
    # 1. outcome.intent_name (if truthy and durable)
    # 2. plan.intent_name (if available and durable)
    # 3. previous_session_state.intent_name (if durable)
    # 4. Otherwise: raise/assert (invalid state)
    intent_name = ""

    # Priority 1: Use outcome.intent_name (if truthy and durable)
    if outcome:
        outcome_intent = outcome.get("intent_name") or outcome.get("intent")
        if outcome_intent and outcome_intent not in ("", "UNKNOWN"):
            # Only use if durable
            if is_durable_intent(outcome_intent):
                intent_name = outcome_intent
                logger.debug(
                    f"[build_session_state_from_outcome] Using outcome.intent_name={intent_name} (durable)"
                )
            else:
                logger.debug(
                    f"[build_session_state_from_outcome] Skipping ephemeral outcome.intent_name={outcome_intent}"
                )

    # Priority 2: Use plan.intent_name (if available and durable)
    if not intent_name and outcome:
        plan_obj = outcome.get("plan", {})
        if isinstance(plan_obj, dict):
            plan_intent_name = plan_obj.get(
                "intent_name") or plan_obj.get("intent")
            if plan_intent_name and plan_intent_name not in ("", "UNKNOWN"):
                # Only use if durable
                if is_durable_intent(plan_intent_name):
                    intent_name = plan_intent_name
                    logger.debug(
                        f"[build_session_state_from_outcome] Using plan.intent_name={intent_name} (durable)"
                    )
                else:
                    logger.debug(
                        f"[build_session_state_from_outcome] Skipping ephemeral plan.intent_name={plan_intent_name}"
                    )

    # Priority 3: Preserve previous session intent if no outcome/plan intent found (only if durable)
    # CRITICAL: If outcome.intent_name is falsy AND previous_session_state.intent_name exists AND is durable
    # → Preserve the previous session intent instead of writing None
    # This check happens BEFORE session clearing to ensure durable intents are preserved
    if not intent_name and previous_session_state:
        previous_intent = previous_session_state.get(
            "intent_name") or previous_session_state.get("intent")
        if previous_intent and previous_intent not in ("", "UNKNOWN"):
            # Only preserve if durable
            if is_durable_intent(previous_intent):
                intent_name = previous_intent
                logger.info(
                    f"[build_session_state_from_outcome] Preserving durable previous session intent={intent_name} "
                    f"(outcome.intent_name was falsy/empty, status={outcome_status})"
                )
            else:
                logger.debug(
                    f"[build_session_state_from_outcome] Not preserving ephemeral previous session intent={previous_intent}"
                )

    # DURABLE INTENT CONTRACT: Durable intents (as defined in intent_policy.yaml) preserve sessions on READY
    # This allows follow-up modifications (e.g., "make it 4pm" after booking is ready)
    # Ephemeral intents clear sessions on READY (terminal state)
    # TEMPORARY DEBUG LOG: Show intent_name, outcome_status, and whether session is cleared or preserved
    session_cleared = False
    if outcome_status == "READY" and intent_name:
        is_durable = is_durable_intent(intent_name)
        if not is_durable:
            # Ephemeral intent: clear session on READY
            logger.error(
                f"[SESSION_CLEAR_DEBUG] intent_name={intent_name}, outcome_status={outcome_status}, "
                f"is_durable={is_durable}, action=CLEARED"
            )
            print(
                f"[SESSION_CLEAR_DEBUG] intent_name={intent_name}, outcome_status={outcome_status}, "
                f"is_durable={is_durable}, action=CLEARED"
            )
            return None
        else:
            # Durable intent: preserve session on READY
            session_cleared = False
            logger.error(
                f"[SESSION_CLEAR_DEBUG] intent_name={intent_name}, outcome_status={outcome_status}, "
                f"is_durable={is_durable}, action=PRESERVED"
            )
            print(
                f"[SESSION_CLEAR_DEBUG] intent_name={intent_name}, outcome_status={outcome_status}, "
                f"is_durable={is_durable}, action=PRESERVED"
            )
    elif outcome_status == "READY" and not intent_name:
        # No intent: clear session
        logger.error(
            f"[SESSION_CLEAR_DEBUG] intent_name=None, outcome_status={outcome_status}, "
            f"is_durable=False, action=CLEARED (no intent)"
        )
        print(
            f"[SESSION_CLEAR_DEBUG] intent_name=None, outcome_status={outcome_status}, "
            f"is_durable=False, action=CLEARED (no intent)"
        )
        return None

    # CRITICAL: Recompute missing_slots from persisted slots AFTER persistence
    # ARCHITECTURAL INVARIANT: missing_slots = REQUIRED_SLOTS(intent) - session.slots.keys()
    # missing_slots must be computed ONLY from durable session.slots after merging and persisting
    # This is the ONLY source of truth - DO NOT use raw_missing_slots or filtered missing_slots
    # Do NOT carry forward missing_slots computed before merge/persistence
    # This ensures missing_slots accurately reflects what's actually persisted
    recomputed_missing_slots = None
    if intent_name:
        # compute_missing_slots is already imported at module level (line 15)
        # Use modification context from merged_luma_response (detected earlier) or previous_session_state
        modification_context = None
        if merged_luma_response:
            modification_context = merged_luma_response.get(
                "_modification_context")
        if not modification_context and previous_session_state:
            modification_context = previous_session_state.get(
                "_modification_context")
        # Use planner to recompute missing_slots
        from core.planning.policy.action_policy import plan_intent, load_planning_policy
        policy = load_planning_policy()
        plan = plan_intent(intent_name, slots, policy)
        recomputed_missing_slots = plan["missing_slots"]

        # APPOINTMENT INTENT RULE: Only exact time_constraint satisfies the time requirement
        # mode=exact → satisfies time, mode=fuzzy/window → does NOT satisfy time
        # This must happen BEFORE plan status is set and BEFORE session persistence
        merged_response = merged_luma_response or {}
        time_constraint = merged_response.get("time_constraint")
        if intent_name == "CREATE_APPOINTMENT" and time_constraint is not None:
            # Check if time_constraint mode is exact (only exact satisfies time requirement)
            time_constraint_mode = None
            if isinstance(time_constraint, dict):
                time_constraint_mode = time_constraint.get("mode")

            # Only remove "time" from missing_slots if mode is exact
            if time_constraint_mode == "exact":
                if "time" in recomputed_missing_slots:
                    recomputed_missing_slots = [
                        s for s in recomputed_missing_slots if s != "time"]
                    logger.info(
                        f"[MISSING_SLOTS] time_constraint (mode=exact) satisfies time for CREATE_APPOINTMENT - removed 'time' from recomputed_missing_slots"
                    )
            else:
                # Fuzzy/window time_constraint does NOT satisfy time requirement
                logger.debug(
                    f"[MISSING_SLOTS] time_constraint (mode={time_constraint_mode}) does NOT satisfy time for CREATE_APPOINTMENT - keeping 'time' in recomputed_missing_slots"
                )

        # Normalize MODIFY_BOOKING missing_slots (test contract)
        from core.orchestration.nlu.luma_response_processor import _normalize_modify_booking_missing_slots
        recomputed_missing_slots = _normalize_modify_booking_missing_slots(
            recomputed_missing_slots, merged_response
        )

        # INVARIANT CHECK: missing_slots must be a list
        assert isinstance(recomputed_missing_slots, list), (
            f"missing_slots must be a list, got {type(recomputed_missing_slots)}: {recomputed_missing_slots}"
        )

        # INVARIANT CHECK: Ensure time never appears in missing_slots if session.slots contains time
        if "time" in slots and "time" in recomputed_missing_slots:
            logger.error(
                f"[SLOT_SATISFACTION] VIOLATION: time is in session.slots but also in missing_slots! "
                f"session.slots={list(slots.keys())}, missing_slots={recomputed_missing_slots}"
            )
            # Remove time from missing_slots if it's in slots
            recomputed_missing_slots = [
                s for s in recomputed_missing_slots if s != "time"]
            logger.warning(
                f"[SLOT_SATISFACTION] Fixed: removed 'time' from missing_slots because it's in session.slots"
            )

        # CRITICAL: Update outcome's facts with recomputed missing_slots (ONLY source of truth)
        # This ensures missing_slots in outcome reflects persisted slots, not pre-persistence state
        # build_plan and outcome facts must use ONLY the recomputed missing_slots
        if "facts" in outcome:
            outcome["facts"]["missing_slots"] = recomputed_missing_slots
            logger.info(
                f"[MISSING_SLOTS] Updated outcome facts with recomputed missing_slots: "
                f"intent={intent_name}, persisted_slots={list(slots.keys())}, missing_slots={recomputed_missing_slots}"
            )
            print(
                f"[MISSING_SLOTS] Updated outcome facts with recomputed missing_slots: "
                f"intent={intent_name}, persisted_slots={list(slots.keys())}, missing_slots={recomputed_missing_slots}"
            )

        # CRITICAL: Ensure merged_luma_response["missing_slots"] equals recomputed missing_slots
        # This ensures consistency: merged_luma_response returned to decision/plan uses the same
        # missing_slots as outcome facts and session persistence
        # After recomputation, do not overwrite missing_slots via any raw/filter path
        if merged_luma_response and isinstance(merged_luma_response, dict):
            merged_luma_response["missing_slots"] = recomputed_missing_slots
            logger.info(
                f"[MISSING_SLOTS] Updated merged_luma_response with recomputed missing_slots: "
                f"intent={intent_name}, missing_slots={recomputed_missing_slots}"
            )
        else:
            logger.warning(
                f"[MISSING_SLOTS] Outcome has no 'facts' key, cannot update missing_slots"
            )
    else:
        logger.warning(
            f"[MISSING_SLOTS] Cannot recompute missing_slots: intent_name is empty"
        )

    # Determine status
    status = "NEEDS_CLARIFICATION" if outcome_status in (
        "NEEDS_CLARIFICATION", "AWAITING_CONFIRMATION", "AWAITING_CAPABILITY") else "READY"

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
    missing_slots_to_persist = recomputed_missing_slots if recomputed_missing_slots is not None else []

    # --- Detect last_filled_slot (pure metadata, no behavioral impact) ---
    last_filled_slot = None

    if merged_luma_response and isinstance(merged_luma_response, dict):
        raw_luma_slots = merged_luma_response.get("_raw_luma_slots", {})
        if isinstance(raw_luma_slots, dict) and raw_luma_slots:
            previous_slots = previous_session_state.get("slots", {}) if previous_session_state else {}
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
        outcome_slots = outcome.get("slots", {}) if outcome and isinstance(outcome, dict) else {}
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
    if slot_attempts and isinstance(slot_attempts, dict) and isinstance(missing_slots_to_persist, list):
        slots_to_clear = [
            slot_name for slot_name in slot_attempts.keys()
            if slot_name not in missing_slots_to_persist
        ]
        for slot_name in slots_to_clear:
            slot_attempts.pop(slot_name, None)
            logger.debug(
                f"[SLOT_ATTEMPTS] Cleared retry counter for slot '{slot_name}' (no longer missing)"
            )

    # CRITICAL: Final intent determination with hard assertion
    # Use the resolved intent_name from the precedence order above
    final_intent_name = intent_name if intent_name and intent_name not in (
        "", "UNKNOWN") else None

    # HARD ASSERTION: If a durable intent exists in the session, persistence must never write intent_name=None
    # Check if previous session has a durable intent that should be preserved
    if not final_intent_name and previous_session_state:
        previous_intent = previous_session_state.get(
            "intent_name") or previous_session_state.get("intent")
        if previous_intent and previous_intent not in ("", "UNKNOWN"):
            if is_durable_intent(previous_intent):
                # CRITICAL: Preserve durable session intent - never wipe it
                final_intent_name = previous_intent
                logger.info(
                    f"[build_session_state_from_outcome] Preserving durable session intent={final_intent_name} "
                    f"(outcome.intent_name was falsy/empty, status={outcome_status})"
                )

    # HARD ASSERTION: If a durable intent exists, it must never be wiped
    if previous_session_state:
        previous_intent = previous_session_state.get(
            "intent_name") or previous_session_state.get("intent")
        if previous_intent and previous_intent not in ("", "UNKNOWN"):
            if is_durable_intent(previous_intent):
                # Assert that we're not wiping a durable intent
                assert final_intent_name == previous_intent or final_intent_name, (
                    f"Invariant violation: durable intent '{previous_intent}' lost during session persistence. "
                    f"final_intent_name={final_intent_name}, outcome.intent_name={outcome.get('intent_name') if outcome else None}, "
                    f"plan.intent_name={outcome.get('plan', {}).get('intent_name') if outcome and isinstance(outcome.get('plan'), dict) else None}, "
                    f"outcome_status={outcome_status}"
                )
                # If final_intent_name is empty but we have a durable previous intent, use it
                if not final_intent_name:
                    final_intent_name = previous_intent
                    logger.error(
                        "[SESSION_PERSISTENCE] CRITICAL: Recovered durable intent=%s (was about to be wiped)",
                        final_intent_name
                    )

    # Final assertion: If we still don't have an intent, this is an invalid state
    # (unless this is a first turn with no session, which is valid)
    if not final_intent_name and previous_session_state:
        # This should never happen if the above logic worked correctly
        logger.error(
            "[SESSION_PERSISTENCE] ERROR: intent_name is empty during persistence with existing session! "
            "outcome_status=%s, outcome_keys=%s, plan_keys=%s, previous_session_intent=%s",
            outcome_status,
            list(outcome.keys()) if outcome else None,
            list(outcome.get("plan", {}).keys()) if outcome and isinstance(
                outcome.get("plan"), dict) else None,
            previous_session_state.get(
                "intent_name") if previous_session_state else None
        )
        # Last resort: try to preserve any previous intent (even if not explicitly durable-checked)
        previous_intent = previous_session_state.get(
            "intent_name") or previous_session_state.get("intent")
        if previous_intent and previous_intent not in ("", "UNKNOWN"):
            final_intent_name = previous_intent
            logger.warning(
                "[SESSION_PERSISTENCE] Last resort: Preserving previous session intent=%s",
                final_intent_name
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

    # CRITICAL: Ensure facts are JSON-serializable before persistence
    # Facts must be a dict with JSON-serializable values (no objects, no functions)
    # Filter out any non-serializable values to prevent session save failures
    # INVARIANT: serializable_facts must ALWAYS be a dict (never None, never omitted)
    serializable_facts = {}
    if merged_facts and isinstance(merged_facts, dict):
        try:
            # Test JSON serialization
            json.dumps(merged_facts, default=str)
            serializable_facts = merged_facts
        except (TypeError, ValueError) as e:
            logger.warning(
                f"[FACTS_PERSISTENCE] Facts contain non-serializable values, filtering: {e}. "
                f"Original facts keys: {list(merged_facts.keys())}"
            )
            # Filter to only JSON-serializable values
            for key, value in merged_facts.items():
                try:
                    json.dumps(value, default=str)
                    serializable_facts[key] = value
                except (TypeError, ValueError):
                    logger.warning(
                        f"[FACTS_PERSISTENCE] Skipping non-serializable fact key: {key}"
                    )

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

    # HARD GUARD: Ensure session_state["facts"] is always present as a dict
    # This invariant must hold for all sessions, especially those with active_capability
    # Session facts are first-class and must never be omitted, even if empty
    if "facts" not in session_state:
        logger.warning(
            f"[SESSION_PERSISTENCE] Missing 'facts' key in session_state, adding empty dict. "
            f"session_state keys: {list(session_state.keys())}"
        )
        session_state["facts"] = {}
    elif session_state["facts"] is None:
        logger.warning(
            f"[SESSION_PERSISTENCE] session_state['facts'] is None, replacing with empty dict"
        )
        session_state["facts"] = {}
    elif not isinstance(session_state["facts"], dict):
        logger.warning(
            f"[SESSION_PERSISTENCE] session_state['facts'] is not a dict (type: {type(session_state['facts'])}), "
            f"replacing with empty dict"
        )
        session_state["facts"] = {}

    # Hard assertion: facts must be a dict
    assert isinstance(session_state["facts"], dict), (
        f"CRITICAL: session_state['facts'] must be a dict before persistence. "
        f"Got type: {type(session_state['facts'])}, value: {session_state.get('facts')}"
    )

    # HARD GUARD: Ensure session_state["slot_attempts"] is always present as a dict
    # This invariant ensures consistent session shape and safe retry tracking
    # slot_attempts defaults to empty dict if not present or invalid
    # Defensive guarantee: always ensure slot_attempts exists before validation
    
    # DEBUG: Log state before guard block
    logger.debug(
        f"[SLOT_ATTEMPTS_DEBUG] BEFORE guard block: "
        f"session_state['slot_attempts']={session_state.get('slot_attempts')}, "
        f"id={id(session_state.get('slot_attempts'))}, "
        f"type={type(session_state.get('slot_attempts'))}"
    )
    
    session_state.setdefault("slot_attempts", {})
    if "slot_attempts" not in session_state:
        logger.warning(
            f"[SLOT_ATTEMPTS_DEBUG] OVERWRITE: slot_attempts not in session_state, setting to {{}}"
        )
        session_state["slot_attempts"] = {}
    elif session_state["slot_attempts"] is None:
        logger.warning(
            f"[SLOT_ATTEMPTS_DEBUG] OVERWRITE: session_state['slot_attempts'] is None, replacing with empty dict. "
            f"Previous id was {id(session_state.get('slot_attempts'))}"
        )
        session_state["slot_attempts"] = {}
    elif not isinstance(session_state["slot_attempts"], dict):
        logger.warning(
            f"[SLOT_ATTEMPTS_DEBUG] OVERWRITE: session_state['slot_attempts'] is not a dict (type: {type(session_state['slot_attempts'])}), "
            f"replacing with empty dict. Previous value: {session_state.get('slot_attempts')}, id={id(session_state.get('slot_attempts'))}"
        )
        session_state["slot_attempts"] = {}
    
    # DEBUG: Log state after guard block
    logger.debug(
        f"[SLOT_ATTEMPTS_DEBUG] AFTER guard block: "
        f"session_state['slot_attempts']={session_state.get('slot_attempts')}, "
        f"id={id(session_state.get('slot_attempts'))}"
    )

    # Hard assertion: slot_attempts must be a dict
    assert isinstance(session_state["slot_attempts"], dict), (
        f"CRITICAL: session_state['slot_attempts'] must be a dict before persistence. "
        f"Got type: {type(session_state.get('slot_attempts'))}, value: {session_state.get('slot_attempts')}"
    )

    # SESSION_SAVE_DEBUG: Guard logging before session save
    logger.error(
        "[SESSION_SAVE_DEBUG] intent_name=%r status=%r stage=%r action=%r",
        session_state.get("intent_name"),
        session_state.get("status"),
        session_state.get("stage"),
        session_state.get("action"),
    )

    # LOG 4 — SESSION WRITE (immediately after persistence)
    turn_logger.info(
        json.dumps({
            "turn": "SESSION_WRITE",
            "session": session_state
        }, ensure_ascii=True, default=str)
    )

    # Persist time_constraint to session state for multi-turn scenarios
    # This ensures time_constraint from previous turns (e.g., "book at 2pm") is preserved
    # when the next turn only provides date (e.g., "tomorrow")
    if merged_luma_response and isinstance(merged_luma_response, dict):
        time_constraint = merged_luma_response.get("time_constraint")
        if time_constraint is not None and final_intent_name == "CREATE_APPOINTMENT":
            session_state["time_constraint"] = time_constraint
            logger.debug(
                f"[TIME_CONSTRAINT] Persisting time_constraint to session_state: {time_constraint}"
            )

    # Persist availability_planned flag for CREATE_APPOINTMENT
    # This tracks when SEARCH_AVAILABILITY was planned (even if execution was blocked)
    # Separate from availability_executed (last_execution_result.type == "availability")
    if final_intent_name == "CREATE_APPOINTMENT":
        # Check if plan has _availability_planned flag (set by orchestrator override)
        plan_obj = outcome.get("plan", {})
        if isinstance(plan_obj, dict) and plan_obj.get("_availability_planned"):
            session_state["availability_planned"] = True
            logger.debug(
                f"[AVAILABILITY_PLANNED] Persisting availability_planned=true to session_state"
            )
        # Preserve availability_planned from previous session if it exists
        elif previous_session_state and previous_session_state.get("availability_planned"):
            session_state["availability_planned"] = True
            logger.debug(
                f"[AVAILABILITY_PLANNED] Preserving availability_planned=true from previous session"
            )
        # Preserve last_execution_result from previous session if it exists
        if previous_session_state and previous_session_state.get("last_execution_result"):
            session_state["last_execution_result"] = previous_session_state.get(
                "last_execution_result")
            logger.debug(
                f"[AVAILABILITY_EXECUTED] Preserving last_execution_result from previous session"
            )
        # Preserve availability_fingerprint from multiple sources (priority order):
        # 1. From outcome's execution result (if SEARCH_AVAILABILITY executed this turn)
        # 2. From previous_session_state (cross-turn preservation)
        # 3. From session_store (if available, for same-turn persistence)
        # 4. From session functions (fallback)
        availability_fingerprint_to_preserve = None

        # Priority 1: Extract from outcome's result/plan if SEARCH_AVAILABILITY executed this turn
        # When execution happens, the fingerprint can be in:
        # - outcome.result (execution_result with type="availability")
        # - outcome.plan (plan dict with fingerprint attached by orchestrator)
        # - outcome itself (if outcome is the execution_result directly)
        if outcome and isinstance(outcome, dict):
            # Check if outcome.result exists (full result structure)
            result_obj = outcome.get("result")
            if (isinstance(result_obj, dict)
                    and result_obj.get("type") == "availability"
                    and result_obj.get("status") == "success"
                    and result_obj.get("availability_fingerprint")):
                availability_fingerprint_to_preserve = result_obj.get(
                    "availability_fingerprint")
                logger.debug(
                    f"[AVAILABILITY_FINGERPRINT] Extracted from outcome.result: {availability_fingerprint_to_preserve}"
                )
            # Check if outcome.plan has fingerprint (attached by orchestrator)
            elif outcome.get("plan") and isinstance(outcome.get("plan"), dict):
                plan_obj = outcome.get("plan")
                if plan_obj.get("availability_fingerprint"):
                    availability_fingerprint_to_preserve = plan_obj.get(
                        "availability_fingerprint")
                    logger.debug(
                        f"[AVAILABILITY_FINGERPRINT] Extracted from outcome.plan: {availability_fingerprint_to_preserve}"
                    )
            # Check if outcome itself is the execution_result (direct structure)
            elif (outcome.get("type") == "availability"
                    and outcome.get("status") == "success"
                    and outcome.get("availability_fingerprint")):
                availability_fingerprint_to_preserve = outcome.get(
                    "availability_fingerprint")
                logger.debug(
                    f"[AVAILABILITY_FINGERPRINT] Extracted from outcome (direct): {availability_fingerprint_to_preserve}"
                )

        # Priority 2: From previous_session_state (cross-turn preservation)
        if not availability_fingerprint_to_preserve:
            if previous_session_state and previous_session_state.get("availability_fingerprint"):
                availability_fingerprint_to_preserve = previous_session_state.get(
                    "availability_fingerprint")
                logger.debug(
                    f"[AVAILABILITY_FINGERPRINT] Extracted from previous_session_state: {availability_fingerprint_to_preserve}"
                )

        # Priority 3: From session_store (if available, for same-turn persistence)
        if not availability_fingerprint_to_preserve and session_store and user_id:
            try:
                if hasattr(session_store, 'get_session'):
                    latest_session = session_store.get_session(user_id)
                elif callable(session_store):
                    latest_session = session_store(user_id)
                else:
                    latest_session = None
                if latest_session and isinstance(latest_session, dict):
                    availability_fingerprint_to_preserve = latest_session.get(
                        "availability_fingerprint")
                    if availability_fingerprint_to_preserve:
                        logger.debug(
                            f"[AVAILABILITY_FINGERPRINT] Extracted from session_store: {availability_fingerprint_to_preserve}"
                        )
            except Exception as e:
                logger.debug(
                    f"Failed to read session from session_store for fingerprint preservation: {e}")

        # Priority 4: Fallback to session functions
        if not availability_fingerprint_to_preserve and user_id:
            try:
                from core.orchestration.session import get_session
                latest_session = get_session(user_id)
                if latest_session and isinstance(latest_session, dict):
                    availability_fingerprint_to_preserve = latest_session.get(
                        "availability_fingerprint")
                    if availability_fingerprint_to_preserve:
                        logger.debug(
                            f"[AVAILABILITY_FINGERPRINT] Extracted from get_session: {availability_fingerprint_to_preserve}"
                        )
            except Exception as e:
                logger.debug(
                    f"Failed to read session using get_session for fingerprint preservation: {e}")

        # Preserve fingerprint in session_state
        if availability_fingerprint_to_preserve:
            session_state["availability_fingerprint"] = availability_fingerprint_to_preserve
            logger.debug(
                f"[AVAILABILITY_FINGERPRINT] Preserved in session_state: {availability_fingerprint_to_preserve}"
            )

    # TRACE_MERGE 5: Before persisting session
    # CRITICAL: Use user_id parameter, not from session_state (session_state is being built, not previous session)
    effective_user_id = user_id if user_id else (session_state.get(
        "user_id") if isinstance(session_state, dict) else None) or "unknown"
    durable_slots_list = list(slots.keys()) if isinstance(slots, dict) else []
    raw_service_id = slots.get(
        "service_id") if isinstance(slots, dict) else None
    canonical_service_id = slots.get(
        "_canonical_service_id") if isinstance(slots, dict) else None
    # DEBUG: Log service_id preservation (guarded by DEBUG flag)
    if os.getenv("DEBUG_SERVICE_ID", "0") == "1":
        logger.error("[DEBUG_SERVICE_ID] user_id=%s point=BEFORE_PERSIST raw_service_id=%s canonical_service_id=%s",
                     effective_user_id, raw_service_id, canonical_service_id)
    logger.error("[TRACE_MERGE] user_id=%s point=BEFORE_PERSIST session_after_candidate=%s durable_slots=%s slots_to_write=%s raw_service_id=%s canonical_service_id=%s",
                 effective_user_id, json.dumps(session_state, default=str, ensure_ascii=True), durable_slots_list, durable_slots_list, raw_service_id, canonical_service_id)

    # Persist modification context for MODIFY_* intents (allows context-aware required slot derivation)
    # This is persisted separately from slots to enable required slot inference even when slots are empty
    modification_context = merged_luma_response.get(
        "_modification_context") if merged_luma_response else None
    if modification_context:
        session_state["_modification_context"] = modification_context
        print(
            f"[SESSION_MERGE] Persisting modification_context to session: {modification_context}")

    # Store context metadata if needed (for next turn's derivation layer)
    if context_with_roles:
        session_state["context"] = context_with_roles

    # awaiting_slot removed - slots are treated as unordered, additive map

    # CRITICAL: ALWAYS persist missing_slots when status=NEEDS_CLARIFICATION
    # missing_slots is recomputed from effective_collected_slots (post-promotion) and is the ONLY source of truth
    # It MUST be persisted to ensure follow-up turns and tests see the same missing_slots Core used
    # to decide NEEDS_CLARIFICATION
    # Remove/avoid any branch that builds session state without persisting missing_slots when status=NEEDS_CLARIFICATION
    missing_slots_to_persist = None
    if status == "NEEDS_CLARIFICATION":
        # When status=NEEDS_CLARIFICATION, missing_slots MUST always be persisted
        if "recomputed_missing_slots" in locals():
            # Use the recomputed missing_slots from above (computed from persisted slots)
            missing_slots_to_persist = recomputed_missing_slots
        elif intent_name:
            # Fallback: recompute missing_slots using planner if not already computed
            from core.planning.policy.action_policy import plan_intent, load_planning_policy
            policy = load_planning_policy()
            plan = plan_intent(intent_name, slots, policy)
            missing_slots_to_persist = plan["missing_slots"]

            # APPOINTMENT INTENT RULE: Only exact time_constraint satisfies the time requirement
            # mode=exact → satisfies time, mode=fuzzy/window → does NOT satisfy time
            # This must happen BEFORE plan status is set and BEFORE session persistence
            merged_response = merged_luma_response or {}
            time_constraint = merged_response.get("time_constraint")
            if intent_name == "CREATE_APPOINTMENT" and time_constraint is not None:
                # Check if time_constraint mode is exact (only exact satisfies time requirement)
                time_constraint_mode = None
                if isinstance(time_constraint, dict):
                    time_constraint_mode = time_constraint.get("mode")

                # Only remove "time" from missing_slots if mode is exact
                if time_constraint_mode == "exact":
                    if "time" in missing_slots_to_persist:
                        missing_slots_to_persist = [
                            s for s in missing_slots_to_persist if s != "time"]
                        logger.info(
                            f"[MISSING_SLOTS] time_constraint (mode=exact) satisfies time for CREATE_APPOINTMENT - removed 'time' from missing_slots_to_persist"
                        )
                else:
                    # Fuzzy/window time_constraint does NOT satisfy time requirement
                    logger.debug(
                        f"[MISSING_SLOTS] time_constraint (mode={time_constraint_mode}) does NOT satisfy time for CREATE_APPOINTMENT - keeping 'time' in missing_slots_to_persist"
                    )

            # Normalize MODIFY_BOOKING missing_slots (test contract)
            from core.orchestration.nlu.luma_response_processor import _normalize_modify_booking_missing_slots
            missing_slots_to_persist = _normalize_modify_booking_missing_slots(
                missing_slots_to_persist, merged_response
            )
        elif "facts" in outcome and isinstance(outcome["facts"], dict):
            # Last resort: try to get missing_slots from outcome facts
            facts_missing_slots = outcome["facts"].get("missing_slots")
            if isinstance(facts_missing_slots, list):
                missing_slots_to_persist = facts_missing_slots
            else:
                # Fallback to empty list if no missing_slots available
                missing_slots_to_persist = []

        # CRITICAL: Always persist missing_slots when status=NEEDS_CLARIFICATION
        # This ensures consistency: follow-up turns and tests see the same missing_slots
        # that Core used to decide NEEDS_CLARIFICATION
        if missing_slots_to_persist is not None:
            session_state["missing_slots"] = missing_slots_to_persist
            logger.info(
                f"[MISSING_SLOTS] Persisted missing_slots to session_state (status=NEEDS_CLARIFICATION): {missing_slots_to_persist}"
            )
            print(
                f"[MISSING_SLOTS] Persisted missing_slots to session_state (status=NEEDS_CLARIFICATION): {missing_slots_to_persist}"
            )
        else:
            # This should never happen - log error if it does
            logger.error(
                f"[MISSING_SLOTS] VIOLATION: status=NEEDS_CLARIFICATION but missing_slots could not be computed! "
                f"intent_name={intent_name}, slots_keys={list(slots.keys())}"
            )
            # Fallback: use empty list (but this indicates a bug)
            session_state["missing_slots"] = []
    elif intent_name:
        # For other statuses, persist missing_slots if available (for debugging/continuity)
        # But this is optional - only REQUIRED when status=NEEDS_CLARIFICATION
        if "recomputed_missing_slots" in locals():
            session_state["missing_slots"] = recomputed_missing_slots
        elif "facts" in outcome and isinstance(outcome["facts"], dict):
            facts_missing_slots = outcome["facts"].get("missing_slots")
            if isinstance(facts_missing_slots, list):
                session_state["missing_slots"] = facts_missing_slots

    print(
        f"[BUILD_SESSION] Built session state: "
        f"intent={intent_name}, slots={list(slots.keys())}, status={status}, "
        f"missing_slots={session_state.get('missing_slots', [])}")

    # CRITICAL: Structured snapshot log + invariant checks at exact point of session persistence
    # This runs after plan/outcome is finalized, before returning response
    # Only runs in test/debug mode
    # json is already imported at module level (line 10)
    if os.getenv("DEBUG_SESSION_PERSISTENCE") == "1" or os.getenv("PYTEST_CURRENT_TEST"):
        # Get all required data for snapshot
        final_intent = intent_name
        final_status = status
        final_missing_slots = session_state.get("missing_slots", [])

        # Compute effective_collected_slots from persisted slots
        effective_collected_slots = {}
        if final_intent:
            from core.planning.orchestration.missing_slots import get_planning_required_slots_for_intent as get_required_slots_for_intent
            required_slots_set = set(
                get_required_slots_for_intent(final_intent))
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
            from core.planning.orchestration.missing_slots import get_planning_required_slots_for_intent as get_required_slots_for_intent
            required_slots_list = get_required_slots_for_intent(final_intent)

        # STRUCTURED SNAPSHOT LOG
        persistence_snapshot = {
            "intent": final_intent,
            "status": final_status,
            "missing_slots": final_missing_slots,
            "effective_collected_slots": {
                "keys": list(effective_collected_slots.keys()),
                "values": {k: str(v)[:50] for k, v in effective_collected_slots.items()}
            },
            "required_slots": required_slots_list
        }

        logger.info(
            f"[SESSION_PERSISTENCE_SNAPSHOT] Final session state before persistence: "
            f"intent={final_intent}, status={final_status}, "
            f"missing_slots={final_missing_slots}"
        )
        print(
            f"[SESSION_PERSISTENCE_SNAPSHOT] {json.dumps(persistence_snapshot, indent=2)}")

        # INVARIANT CHECKS (only in test/debug mode)
        invariant_violations = []

        # 2) any(slot in missing_slots for slot in effective_collected_slots.keys()) -> INVALID
        overlapping_slots = [
            slot for slot in final_missing_slots
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
                logger.error(
                    f"[SESSION_PERSISTENCE_INVARIANT] {violation_msg}")
                print(f"[SESSION_PERSISTENCE_INVARIANT] {violation_msg}")

        # Raise error if any violations found (only in test/debug mode)
        if invariant_violations and os.getenv("PYTEST_CURRENT_TEST"):
            # In test mode, raise assertion to fail the test
            raise AssertionError(
                f"Session persistence invariant violations:\n" +
                "\n".join(f"  - {v}" for v in invariant_violations)
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
    
    if (status == "NEEDS_CLARIFICATION" and 
        isinstance(missing_slots_final, list) and 
        len(missing_slots_final) > 0):
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
        session_state["slot_attempts"][first_missing] = session_state["slot_attempts"].get(first_missing, 0) + 1
        
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
            session_state["facts"]["slot_attempts"] = session_state["slot_attempts"].copy()
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
    
    # INSTRUMENTATION: Print object IDs for persistence tracking
    print(
        f"[PERSISTENCE_TRACE] build_session_state_from_outcome RETURN: "
        f"session_state id={id(session_state)}, "
        f"session_state['facts'] id={id(session_state.get('facts'))}, "
        f"session_state['slot_attempts'] id={id(session_state.get('slot_attempts'))}, "
        f"session_state['slot_attempts']={session_state.get('slot_attempts')}, "
        f"outcome.facts id={id(outcome.get('facts') if outcome and isinstance(outcome, dict) else None)}, "
        f"outcome.facts['slot_attempts']={outcome.get('facts', {}).get('slot_attempts') if outcome and isinstance(outcome.get('facts'), dict) else None}"
    )
    logger.error(
        f"[PERSISTENCE_TRACE] build_session_state_from_outcome RETURN: "
        f"session_state id={id(session_state)}, "
        f"session_state['facts'] id={id(session_state.get('facts'))}, "
        f"session_state['slot_attempts'] id={id(session_state.get('slot_attempts'))}, "
        f"session_state['slot_attempts']={session_state.get('slot_attempts')}, "
        f"outcome.facts id={id(outcome.get('facts') if outcome and isinstance(outcome, dict) else None)}, "
        f"outcome.facts['slot_attempts']={outcome.get('facts', {}).get('slot_attempts') if outcome and isinstance(outcome.get('facts'), dict) else None}"
    )

    return session_state
