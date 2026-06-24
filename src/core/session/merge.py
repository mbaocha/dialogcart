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
from core.session.effective_slots import _compute_effective_collected_slots_internal

logger = logging.getLogger(__name__)
turn_logger = logging.getLogger("core.turn_log")
turn_logger.setLevel(logging.INFO)

def merge_luma_with_session(
    luma_response: Dict[str, Any],
    session_state: Dict[str, Any],
    planning_only: bool = False,
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
    user_id = session_state.get("user_id", "unknown") if session_state else "unknown"
    session_slots = session_state.get("slots", {}) if session_state else {}
    session_missing_slots = (
        session_state.get("missing_slots", []) if session_state else []
    )
    logger.info(
        "[SESSION_BEFORE] user_id=%s slots=%s missing_slots=%s",
        user_id,
        json.dumps(session_slots, default=str, ensure_ascii=True),
        session_missing_slots,
    )

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

    session_status = session_state.get("status", "")

    # Extract Luma intent
    luma_intent_obj = merged.get("intent", {})
    luma_intent_name = (
        luma_intent_obj.get("name", "") if isinstance(luma_intent_obj, dict) else ""
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
    from core.orchestration.luma_facts_adapter import (
        facts_to_slots,
        merge_promoted_luma_slots,
    )

    facts_obj_temp = merged.get("facts", {})
    # Use session intent for slot extraction if Luma intent is UNKNOWN (for proper slot extraction)
    # Only use session intent if it's durable
    effective_intent_for_slot_check = luma_intent_name
    if (
        luma_intent_name == "UNKNOWN"
        and session_intent_name
        and is_durable_intent(session_intent_name)
    ):
        effective_intent_for_slot_check = session_intent_name
    luma_slots_temp = (
        facts_to_slots(
            facts_obj_temp,
            intent_name=effective_intent_for_slot_check,
            source_text=merged.get("_source_text"),
        )
        if isinstance(facts_obj_temp, dict)
        else {}
    )
    # Also check for slots in nested facts.facts.slots
    if isinstance(facts_obj_temp, dict) and isinstance(
        facts_obj_temp.get("facts"), dict
    ):
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

    logger.debug(
        "[INTENT_TRACE] entry: merged_intent=%s session_intent=%s status=%s luma_intent=%s",
        merged.get('intent', {}).get('name', '') if isinstance(merged.get('intent'), dict) else 'N/A',
        session_intent_name, session_status, luma_intent_name,
    )

    # Ensure intent dict exists
    if not isinstance(merged.get("intent"), dict):
        merged["intent"] = {}

    existing_intent_name = merged.get("intent", {}).get("name", "")

    logger.debug(
        "[INTENT_TRACE] before assignment: existing=%s session=%s luma=%s durable=%s",
        existing_intent_name, session_intent_name, luma_intent_name,
        is_durable_intent(session_intent_name) if session_intent_name else False,
    )

    # INVARIANT ENFORCEMENT: If session has a durable intent, UNKNOWN/empty/None intents from Luma must be ignored
    if (
        not existing_intent_name or existing_intent_name == "UNKNOWN"
    ) and session_intent_name:
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
            if existing_intent_name != session_intent_name and (
                not luma_intent_name or luma_intent_name == "UNKNOWN"
            ):
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
    from core.orchestration.luma_facts_adapter import (
        facts_to_slots,
        merge_promoted_luma_slots,
    )

    facts_obj = merged.get("facts", {})
    # Get intent for date_range promotion (CREATE_RESERVATION with 2+ dates)
    # CRITICAL: Use effective_intent (computed EARLY after UNKNOWN override) for all operations
    # This ensures effective_intent is used consistently throughout
    effective_intent_for_promotion = merged.get("_effective_intent", luma_intent_name)
    promoted_slots_from_facts = (
        facts_to_slots(
            facts_obj,
            intent_name=effective_intent_for_promotion,
            source_text=merged.get("_source_text"),
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
        nested_slots = merged.get("slots", {})

    # Merge promoted slots; strip date keys when Fix 4 applies (flexible + same-turn service)
    raw_luma_slots = merge_promoted_luma_slots(
        nested_slots,
        promoted_slots_from_facts,
        merged.get("date_constraint"),
        facts_obj if isinstance(facts_obj, dict) else None,
    )

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
            if (
                isinstance(nested_slots, dict)
                and "_canonical_service_id" in nested_slots
            ):
                # Canonical already computed - preserve it
                raw_luma_slots["_canonical_service_id"] = nested_slots[
                    "_canonical_service_id"
                ]
            elif isinstance(nested_slots, dict) and "service_id" in nested_slots:
                nested_service_id = nested_slots["service_id"]
                # If nested value differs from raw, it's likely normalized - store as canonical
                if nested_service_id != raw_service_id_from_facts:
                    raw_luma_slots["_canonical_service_id"] = nested_service_id
            # Also check promoted_slots_from_facts for canonical
            if "_canonical_service_id" not in raw_luma_slots and isinstance(
                promoted_slots_from_facts, dict
            ):
                if "_canonical_service_id" in promoted_slots_from_facts:
                    raw_luma_slots["_canonical_service_id"] = promoted_slots_from_facts[
                        "_canonical_service_id"
                    ]
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
            elif (
                isinstance(promoted_slots_from_facts, dict)
                and "service_id" in promoted_slots_from_facts
            ):
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
    logger.info(
        "[MERGE_RESULT] user_id=%s merged_slots=%s slot_sources=%s",
        user_id,
        json.dumps(raw_luma_slots, default=str, ensure_ascii=True),
        json.dumps(slot_sources, ensure_ascii=True),
    )

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
                    if (
                        isinstance(value, str)
                        and len(value) >= 10
                        and value[4] == "-"
                        and value[7] == "-"
                    ):
                        return value.split("T")[0].split(" ")[0]
                    elif isinstance(value, dict):
                        # Check nested date fields
                        for date_field in [
                            "date",
                            "value",
                            "resolved",
                            "start",
                            "start_date",
                        ]:
                            if date_field in value:
                                date_val = value[date_field]
                                if date_val:
                                    date_str = str(date_val)
                                    if "T" in date_str:
                                        return date_str.split("T")[0]
                                    if " " in date_str:
                                        return date_str.split(" ")[0]
                                    if (
                                        len(date_str) >= 10
                                        and date_str[4] == "-"
                                        and date_str[7] == "-"
                                    ):
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
                        if (
                            len(date_candidate) >= 10
                            and date_candidate[4] == "-"
                            and date_candidate[7] == "-"
                        ):
                            # Extract date part
                            return date_candidate.split("T")[0].split(" ")[0]
                        return date_candidate
                    elif isinstance(date_candidate, dict):
                        # If it's an object, check common date fields
                        for date_field in [
                            "resolved",
                            "date",
                            "value",
                            "start",
                            "start_date",
                        ]:
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
            if "datetime_range" in booking and isinstance(
                booking["datetime_range"], dict
            ):
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
        logger.debug(f"Extracted date using comprehensive helper: {extracted_date}")

    # DEBUG: Log if date extraction failed (for weekday debugging)
    debug_weekday = os.getenv("DEBUG_WEEKDAY", "0") == "1"
    if (
        debug_weekday
        and "date" not in luma_slots
        and session_state
        and session_state.get("status") == "NEEDS_CLARIFICATION"
    ):
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
                logger.debug(f"Extracted date from entities.date: {date_value}")
        # TIME_CONSTRAINT RULE: For CREATE_APPOINTMENT, do NOT extract time from entities
        # time_constraint is authoritative; slots.time is legacy-only and must not drive planning
        if "time" in entities and "time" not in luma_slots:
            # Only extract time for non-CREATE_APPOINTMENT intents
            # For CREATE_APPOINTMENT, time_constraint is authoritative (handled separately)
            if merged_intent_name != "CREATE_APPOINTMENT":
                time_value = entities.get("time")
                if time_value:
                    luma_slots["time"] = time_value
                    logger.debug(f"Extracted time from entities.time: {time_value}")
                else:
                    logger.debug(
                        f"Skipped time extraction from entities for CREATE_APPOINTMENT (time_constraint is authoritative)"
                    )

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
    merged_intent_name = merged.get(
        "_effective_intent",
        (
            merged.get("intent", {}).get("name", "")
            if isinstance(merged.get("intent"), dict)
            else ""
        ),
    )

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
                            f"Extracted start_date from semantic.date_refs with START_DATE role: {date_refs[0]}"
                        )
                    if "END_DATE" in date_roles and "end_date" not in luma_slots:
                        # END_DATE might be in a later position in date_refs
                        if isinstance(date_refs, list):
                            # Find index of END_DATE in date_roles to match with date_refs
                            try:
                                end_date_idx = list(date_roles).index("END_DATE")
                                if end_date_idx < len(date_refs):
                                    luma_slots["end_date"] = date_refs[end_date_idx]
                                    logger.debug(
                                        f"Extracted end_date from semantic.date_refs with END_DATE role: {date_refs[end_date_idx]}"
                                    )
                            except (ValueError, IndexError):
                                # Fallback to last date if END_DATE role exists and we have multiple dates
                                if len(date_refs) > 1:
                                    luma_slots["end_date"] = date_refs[-1]
                                    logger.debug(
                                        f"Extracted end_date from semantic.date_refs (last date, END_DATE role): {date_refs[-1]}"
                                    )
                # FIX: For CREATE_RESERVATION, do NOT extract generic "date" slot when date_roles is missing
                # Only extract role-specific slots (start_date, end_date) when explicitly labeled by date_roles
                # If Luma returns only date without date_roles, keep it as date in context but do NOT satisfy start_date requirement
                # This prevents auto-promotion of generic date to start_date
            elif date_mode == "single_day" or not date_mode:
                # For service appointments, extract date if single_day mode
                if "date" not in luma_slots and "start_date" not in luma_slots:
                    luma_slots["date"] = date_refs[0]
                    logger.debug(
                        f"Extracted date from semantic.date_refs (root/found): {date_refs[0]}"
                    )

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
                                f"Extracted time from semantic.time_constraint.start: {constraint_start} (mode={constraint_mode})"
                            )
                        else:
                            # Fallback: use time_constraint dict as-is if no start
                            luma_slots["time"] = time_constraint
                            logger.debug(
                                f"Extracted time from semantic.time_constraint (dict): {time_constraint}"
                            )
                    else:
                        # time_constraint is a string, use directly
                        luma_slots["time"] = time_constraint
                        logger.debug(
                            f"Extracted time from semantic.time_constraint: {time_constraint}"
                        )
                elif time_refs and isinstance(time_refs, list) and len(time_refs) > 0:
                    luma_slots["time"] = time_refs[0]
                    logger.debug(
                        f"Extracted time from semantic.time_refs: {time_refs[0]}"
                    )
            else:
                # CREATE_APPOINTMENT: Skip time extraction - time_constraint is authoritative
                logger.debug(
                    f"Skipped time extraction from time_constraint/time_refs for CREATE_APPOINTMENT (time_constraint is authoritative)"
                )

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
                if (
                    date_mode == "single_day"
                    and "date" not in luma_slots
                    and "start_date" not in luma_slots
                ):
                    if isinstance(date_refs, list) and len(date_refs) > 0:
                        luma_slots["date"] = date_refs[0]
                # range → slots["date_range"] or start_date/end_date
                elif date_mode == "range":
                    if (
                        "date_range" not in luma_slots
                        and "start_date" not in luma_slots
                    ):
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
                                f"Extracted time from semantic.time_constraint.start (projection): {constraint_start} (mode={constraint_mode})"
                            )
                        else:
                            # Fallback: use time_constraint dict as-is if no start
                            luma_slots["time"] = time_constraint
                            logger.debug(
                                f"Extracted time from semantic.time_constraint (dict, projection): {time_constraint}"
                            )
                    else:
                        # time_constraint is a string, use directly
                        luma_slots["time"] = time_constraint
                        logger.debug(
                            f"Extracted time from semantic.time_constraint (projection): {time_constraint}"
                        )
                elif time_refs and isinstance(time_refs, list) and len(time_refs) > 0:
                    luma_slots["time"] = time_refs[0]
                    logger.debug(
                        f"Extracted time from semantic.time_refs (projection): {time_refs[0]}"
                    )
            else:
                # CREATE_APPOINTMENT: Skip time extraction - time_constraint is authoritative
                logger.debug(
                    f"Skipped time extraction from time_constraint/time_refs (projection) for CREATE_APPOINTMENT (time_constraint is authoritative)"
                )

    # Additional fallback: Check if Luma provided date/time directly in merged response
    # (Sometimes Luma provides date in slots even without semantic data)
    if "date" not in luma_slots:
        # Check if date exists in merged response slots (Luma might have added it)
        direct_date = merged.get("slots", {}).get("date")
        if direct_date:
            luma_slots["date"] = direct_date
            logger.debug(f"Extracted date from merged.slots.date: {direct_date}")

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
                logger.debug(f"Extracted time from merged.slots.time: {direct_time}")
        else:
            # CREATE_APPOINTMENT: Skip time extraction - time_constraint is authoritative
            logger.debug(
                f"Skipped time extraction from merged.slots.time for CREATE_APPOINTMENT (time_constraint is authoritative)"
            )

    # Check booking object for date/time (Luma might provide in booking.datetime_range)
    booking_obj = merged.get("booking")
    if isinstance(booking_obj, dict) and "date" not in luma_slots:
        booking_date = booking_obj.get("date") or (
            booking_obj.get("datetime_range", {}).get("start")
            if isinstance(booking_obj.get("datetime_range"), dict)
            else None
        )
        if booking_date:
            # Extract date part if it's a datetime
            if isinstance(booking_date, str):
                date_part = booking_date.split("T")[0].split(" ")[0]
                luma_slots["date"] = date_part
                logger.debug(f"Extracted date from booking object: {date_part}")

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

    logger.info(
        f"[SLOT_DURABILITY] session.slots before merge: {list(session_slots.keys())} = {session_slots}"
    )

    # Start with session slots (preserve all previously resolved slots)
    # CRITICAL MERGE ORDER: This merge MUST happen BEFORE intent-change filtering (line ~1033)
    # to ensure valid slots from previous turns (e.g., date from UNKNOWN intent) are preserved
    # when transitioning to concrete intents (e.g., UNKNOWN -> CREATE_APPOINTMENT).
    # The merged_slots will be filtered later if intent changes, but only AFTER all slots are merged.
    merged_slots = session_slots.copy()

    # CRITICAL: Preserve raw service_id from session if Luma doesn't provide it
    # This ensures raw tenant value persists across turns
    raw_service_id_from_session = session_slots.get("service_id")
    canonical_service_id_from_session = session_slots.get("_canonical_service_id")

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
                    f"Normalized time slot from dict to start value: {time_start}"
                )
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
            f"Preserved raw service_id from session: {raw_service_id_from_session}"
        )

    # Preserve canonical service_id from session if present
    if (
        canonical_service_id_from_session
        and "_canonical_service_id" not in merged_slots
    ):
        merged_slots["_canonical_service_id"] = canonical_service_id_from_session
        logger.debug(
            f"Preserved canonical service_id from session: {canonical_service_id_from_session}"
        )

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
    merged_intent_name = (
        merged.get("intent", {}).get("name", "")
        if isinstance(merged.get("intent"), dict)
        else ""
    )
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
                f"[FIX77] Extracted date from context.start_date into slots for persistence: {context['start_date']}"
            )

        # Priority 2: Direct date value in context (only if date not already extracted)
        if "date" in context and "date" not in merged_slots:
            merged_slots["date"] = context["date"]
            logger.debug(
                f"[FIX77] Extracted date from context.date into slots for persistence: {context['date']}"
            )

        # Priority 3: Extract from merged_slots.start_date as date (if start_date was extracted from Luma but date wasn't)
        # This handles cases where start_date was extracted from Luma (line 288-291) but date wasn't
        # We need both for persistence (date) and promotion (start_date via date_roles)
        if "start_date" in merged_slots and "date" not in merged_slots:
            # Extract start_date value as date for persistence
            merged_slots["date"] = merged_slots["start_date"]
            logger.debug(
                f"[FIX77] Extracted date from merged_slots.start_date for persistence: {merged_slots['start_date']}"
            )

        # Extract date_range from context if provided (e.g., "next week", "this weekend")
        if "date_range" in context and "date_range" not in merged_slots:
            merged_slots["date_range"] = context["date_range"]
            logger.debug(
                f"Extracted date_range from context.date_range into slots for persistence: {context['date_range']}"
            )

        # Ensure date_roles are preserved in context for derivation layer
        if "date_roles" in context:
            # date_roles are metadata, keep in context (already merged above)
            pass

    # Slots are merged additively - no special routing needed
    # Users can provide missing slots in any order
    # CRITICAL: Use effective_intent (computed EARLY after UNKNOWN override) for all operations
    merged_intent_name = merged.get(
        "_effective_intent",
        (
            merged.get("intent", {}).get("name", "")
            if isinstance(merged.get("intent"), dict)
            else ""
        ),
    )

    # Update merged response with merged slots
    # CRITICAL: Ensure all session slots are preserved (non-destructive merge)
    # Slots are merged additively - no special routing needed
    merged["slots"] = merged_slots

    from core.orchestration.temporal_proposal import (
        extract_nlu_proposals,
        merge_session_proposals,
    )

    _nlu_proposals = extract_nlu_proposals(merged)
    _merged_proposals = merge_session_proposals(
        session_state,
        _nlu_proposals["date_proposal"],
        _nlu_proposals["time_proposal"],
    )
    if _merged_proposals["date_proposal"] is not None:
        merged["date_proposal"] = _merged_proposals["date_proposal"]
    if _merged_proposals["time_proposal"] is not None:
        merged["time_proposal"] = _merged_proposals["time_proposal"]

    # Assertion: All session slots must be preserved in merged slots
    # ARCHITECTURAL INVARIANT: Slots are durable facts - they must never be lost
    if session_slots:
        missing_session_slots = set(session_slots.keys()) - set(merged_slots.keys())
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
            if not booking_services or (
                isinstance(booking_services, list) and len(booking_services) == 0
            ):
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
        promote_slots_for_intent,
    )

    # Import planner for missing_slots computation
    from core.planning.policy.action_policy import load_planning_policy, plan_intent

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

    session_intent_name = (
        session_intent
        if isinstance(session_intent, str)
        else (
            session_intent.get("name", "") if isinstance(session_intent, dict) else ""
        )
    )
    intent_changed = (
        merged_intent_name
        and session_intent_name
        and merged_intent_name != session_intent_name
        and merged_intent_name != "UNKNOWN"
    )

    if intent_changed:
        logger.info(
            f"[INTENT_CHANGE] Intent changed: previous={session_intent_name} -> new={merged_intent_name}"
        )

        # CRITICAL: Ensure all session slots are in merged_slots before filtering
        # This is a defensive check to prevent slot loss during intent transitions
        # (e.g., UNKNOWN -> CREATE_APPOINTMENT where date should be preserved)
        if session_slots:
            missing_from_merge = set(session_slots.keys()) - set(merged_slots.keys())
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

        slots_before_filtering = merged_slots.copy()
        logger.info(
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

        logger.info(
            f"[INTENT_CHANGE] Slots after filtering: {list(merged_slots.keys())} = {merged_slots}"
        )

        dropped_slots = set(slots_before_filtering.keys()) - set(merged_slots.keys())
        if dropped_slots:
            logger.info(f"[INTENT_CHANGE] Dropped slots: {list(dropped_slots)}")

        # Intent changed - no awaiting_slot to reset (removed from design)

        # Clear context.date_roles on intent change (they are intent-specific)
        # Old intent's date_roles should not leak to new intent
        context = merged.get("context", {})
        if isinstance(context, dict) and "date_roles" in context:
            # Remove date_roles on intent change to force fresh derivation
            del context["date_roles"]
            merged["context"] = context
            logger.debug("[INTENT_CHANGE] Cleared date_roles (intent-specific)")

        # Delete stale missing_slots - will be recomputed from NEW intent contract ONLY
        # CRITICAL: Do NOT use old intent's missing_slots
        if "missing_slots" in merged:
            del merged["missing_slots"]
        # Mark for recomputation with new intent contract
        merged["_force_recompute_missing_slots"] = True
        logger.debug(
            "[INTENT_CHANGE] Marked missing_slots for recomputation from new intent contract"
        )

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
        has_time = "time" in raw_luma_slots and raw_luma_slots.get("time") is not None
        has_date = "date" in raw_luma_slots and raw_luma_slots.get("date") is not None

        # Always set modification context for MODIFY_BOOKING (intent-driven)
        # If no slots detected, set default context that will be refined later
        modification_context = {"modifying_time": has_time, "modifying_date": has_date}
        # Persist modification context to merged response (will be persisted to session)
        merged["_modification_context"] = modification_context

    elif merged_intent_name == "MODIFY_RESERVATION":
        # Detect modification type from raw_luma_slots (before promotion)
        # This is intent-driven: we detect MODIFY_RESERVATION intent, then check for signals
        has_start_date = (
            "start_date" in raw_luma_slots
            and raw_luma_slots.get("start_date") is not None
        )
        has_end_date = (
            "end_date" in raw_luma_slots and raw_luma_slots.get("end_date") is not None
        )
        has_date = "date" in raw_luma_slots and raw_luma_slots.get("date") is not None

        # Always set modification context for MODIFY_RESERVATION (intent-driven)
        # If no slots detected, set default context that will be refined later
        modification_context = {
            "modifying_start_date": has_start_date,
            "modifying_end_date": has_end_date,
            "modifying_date": has_date,
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
    core_intents = {
        "CREATE_APPOINTMENT",
        "CREATE_RESERVATION",
        "MODIFY_BOOKING",
        "CANCEL_BOOKING",
    }

    # Check if current turn is informational (non-core intent)
    is_informational_intent = (
        merged_intent_name
        and merged_intent_name not in core_intents
        and merged_intent_name != "UNKNOWN"
    )

    # Check if session has active planning state that should be preserved
    has_active_planning = (
        session_state
        and isinstance(session_state, dict)
        and session_state.get("status") == "NEEDS_CLARIFICATION"
        and session_intent_name
        and session_intent_name in core_intents
    )

    # Check if current turn provides no new slot values (informational behavior)
    # Compare merged_slots (which includes session slots) with session slots
    session_slots_dict = (
        session_state.get("slots", {})
        if (session_state and isinstance(session_state, dict))
        else {}
    )
    current_turn_has_new_slots = bool(
        merged_slots and any(key not in session_slots_dict for key in merged_slots)
    )

    # Informational turn: has active planning AND (informational intent OR no new slots)
    is_informational_turn = has_active_planning and (
        is_informational_intent or not current_turn_has_new_slots
    )

    # For informational turns with no new slots: preserve everything and skip promotion/recomputation
    # CRITICAL: For MODIFY_* intents, disable informational-turn early return
    # Required-slot computation MUST always run, even when has_new_slots=False
    # This ensures modification_context can properly override base planning slots
    is_modify_intent = merged_intent_name in ("MODIFY_BOOKING", "MODIFY_RESERVATION")
    if (
        is_informational_turn
        and not current_turn_has_new_slots
        and not is_modify_intent
    ):
        # LOG: detected informational turn
        logger.info(
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

        # Preserve previous missing_slots by recomputing from session slots + proposals
        previous_missing_slots = []
        if session_state and isinstance(session_state, dict) and session_intent_name:
            from core.orchestration.temporal_proposal import (
                expand_slots_for_planning,
                resolve_session_proposals,
            )
            from core.planning.policy.action_policy import load_planning_policy, plan_intent

            _proposals = resolve_session_proposals(
                previous_session_state=session_state
            )
            if _proposals["date_proposal"] is not None:
                merged["date_proposal"] = _proposals["date_proposal"]
            if _proposals["time_proposal"] is not None:
                merged["time_proposal"] = _proposals["time_proposal"]

            policy = load_planning_policy()
            planning_slots = expand_slots_for_planning(
                session_slots_dict,
                date_proposal=_proposals["date_proposal"],
                time_proposal=_proposals["time_proposal"],
                date_constraint=session_state.get("date_constraint"),
                time_constraint=session_state.get("time_constraint"),
                intent_name=session_intent_name,
            )
            plan = plan_intent(session_intent_name, planning_slots, policy)
            previous_missing_slots = plan["missing_slots"]

        # INVARIANT CHECK: missing_slots must be a list
        assert isinstance(
            previous_missing_slots, list
        ), f"missing_slots must be a list, got {type(previous_missing_slots)}: {previous_missing_slots}"

        # INVARIANT CHECK: missing_slots must never be None
        assert (
            previous_missing_slots is not None
        ), "missing_slots must not be None after computation"

        merged["missing_slots"] = previous_missing_slots

        # LOG: preserved missing_slots
        logger.info(
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

        logger.info(
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
                    f"Merged date_roles from session context: {session_context['date_roles']}"
                )

    # Update merged context with merged context (for downstream use)
    merged["context"] = context

    # CRITICAL: Promotion starts from merged_slots (session slots + luma slots)
    # This ensures all session slots are available for promotion
    # Promotion is additive - it never removes existing slots
    merged_session_slots = merged_slots.copy()
    promoted_slots = promote_slots_for_intent(merged_slots, effective_intent, context)

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
                f"[SESSION_MERGE] Using persisted modification context from session (fallback): {modification_context}"
            )

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
        promoted_slots, effective_intent, planning_only=planning_only
    )

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
    is_intent_change_recomputation = merged.get("_force_recompute_missing_slots", False)

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
            f"Using canonical service_id for planning: {slots_for_planning['service_id']}"
        )

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
            # Only count non-None Luma slots — null values are validly dropped during
            # intent-change filtering and should not trigger the invariant.
            missing_keys = {k for k in raw_luma_slots_keys if raw_luma_slots.get(k) is not None} - merged_slots_keys

            if missing_keys:
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
    from core.orchestration.temporal_proposal import expand_slots_for_planning

    _facts_for_planning = merged.get("facts")
    if not isinstance(_facts_for_planning, dict):
        _facts_for_planning = None
    planning_slots = expand_slots_for_planning(
        slots_for_planning,
        date_proposal=merged.get("date_proposal")
        or (session_state or {}).get("date_proposal")
        or ((session_state or {}).get("facts") or {}).get("date_proposal"),
        time_proposal=merged.get("time_proposal")
        or (session_state or {}).get("time_proposal")
        or ((session_state or {}).get("facts") or {}).get("time_proposal"),
        date_constraint=merged.get("date_constraint"),
        nlu_facts=_facts_for_planning,
        time_constraint=luma_response.get("time_constraint"),
        intent_name=effective_intent,
    )
    plan = plan_intent(effective_intent, planning_slots, policy)
    missing_slots = plan["missing_slots"]

    from core.orchestration.temporal_proposal import apply_time_constraint_to_missing_slots

    missing_slots = apply_time_constraint_to_missing_slots(
        effective_intent, missing_slots, luma_response.get("time_constraint")
    )

    # MISSING_SLOTS_DECISION: Log missing slots computation decision
    from core.planning.orchestration.missing_slots import (
        get_planning_required_slots_for_intent as get_required_slots_for_intent,
    )

    required_slots = get_required_slots_for_intent(effective_intent)
    logger.info(
        "[MISSING_SLOTS_DECISION] user_id=%s intent=%s required_slots=%s slots_used=%s missing_slots=%s",
        user_id,
        effective_intent,
        required_slots,
        list(durable_slots_for_computation.keys()),
        missing_slots,
    )

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

                    missing_slots = sorted(list(set(issues_missing_slots)))
                    logger.info(
                        f"[MODIFY_BOOKING_ISSUES] Derived missing_slots from Luma issues: {missing_slots} "
                        f"(raw_luma_slots was empty, issues={list(issues.keys())})"
                    )

    logger.debug(f"[SESSION_MERGE] After planner: missing_slots={missing_slots}")

    if is_intent_change_recomputation:
        logger.info(
            f"[INTENT_CHANGE] Recomputed missing_slots from NEW intent contract: {missing_slots}"
        )

    # Normalize MODIFY_BOOKING missing_slots (test contract)
    # Import here to avoid circular dependency
    from core.orchestration.nlu.luma_response_processor import (
        _normalize_modify_booking_missing_slots,
    )

    missing_slots = _normalize_modify_booking_missing_slots(missing_slots, merged)

    # INVARIANT CHECK: missing_slots must be a list
    assert isinstance(
        missing_slots, list
    ), f"missing_slots must be a list, got {type(missing_slots)}: {missing_slots}"

    # INVARIANT CHECK: missing_slots must never be None after computation
    assert missing_slots is not None, "missing_slots must not be None after computation"

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
    merged_intent_name = (
        merged_intent.get("name", "") if isinstance(merged_intent, dict) else ""
    )
    if session_intent and session_status != "READY":
        session_intent_str = (
            session_intent
            if isinstance(session_intent, str)
            else session_intent.get("name", "")
        )
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


