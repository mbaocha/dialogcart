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

logger = logging.getLogger(__name__)


def merge_luma_with_session(
    luma_response: Dict[str, Any],
    session_state: Dict[str, Any]
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
    # Create a copy to avoid mutating the original
    merged = luma_response.copy()

    # Preserve debugging fields (e.g., _raw_luma_response) - these must NOT be mutated or normalized
    # _raw_luma_response is attached by orchestrator for debugging and must be preserved through merge

    # STEP 1: Handle intent - Session intent is immutable unless session is reset
    # If luma.intent == UNKNOWN: use session.intent (don't modify session intent)
    session_intent = session_state.get("intent")
    session_status = session_state.get("status", "")

    # Extract Luma intent
    luma_intent_obj = merged.get("intent", {})
    luma_intent_name = luma_intent_obj.get(
        "name", "") if isinstance(luma_intent_obj, dict) else ""

    logger.debug(
        f"merge_luma_with_session: luma_intent={luma_intent_name} "
        f"session_intent={session_intent} session_status={session_status}"
    )

    # If session.intent exists and session is not RESOLVED (status != "READY")
    if session_intent and session_status != "READY":
        # If luma.intent == UNKNOWN, use session.intent (session intent is immutable)
        if luma_intent_name == "UNKNOWN":
            # Always use session intent (convert to dict format if string)
            if isinstance(session_intent, str):
                merged["intent"] = {"name": session_intent}
            elif isinstance(session_intent, dict):
                merged["intent"] = session_intent.copy()
            logger.debug(
                f"merge_luma_with_session: Overrode UNKNOWN with session_intent={session_intent}"
            )
        # If luma.intent == session.intent, use session intent (ensures consistency)
        elif luma_intent_name == session_intent or (isinstance(session_intent, dict) and luma_intent_name == session_intent.get("name", "")):
            # Use session intent (convert to dict format if string)
            if isinstance(session_intent, str):
                merged["intent"] = {"name": session_intent}
            elif isinstance(session_intent, dict):
                merged["intent"] = session_intent.copy()
            logger.debug(
                f"merge_luma_with_session: Using session_intent={session_intent} (matches luma)"
            )

    # STEP 2: Extract slots from Luma response
    # First, get slots from luma_response.slots (if present)
    # STEP 2: Extract slots from Luma response
    # First, get slots from luma_response.slots (if present)
    raw_luma_slots = merged.get("slots", {}).copy()
    if not isinstance(raw_luma_slots, dict):
        raw_luma_slots = {}

    # Store raw_luma_slots for turn outcome snapshot logging
    merged["_raw_luma_slots"] = raw_luma_slots.copy()

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
        if "time" in entities and "time" not in luma_slots:
            time_value = entities.get("time")
            if time_value:
                luma_slots["time"] = time_value
                logger.debug(
                    f"Extracted time from entities.time: {time_value}")

    # Check if semantic data exists but wasn't found in trace/stages (try direct access)
    # Sometimes Luma provides semantic data at root level or in different structure
    if not semantic_data:
        # Try merged.get("semantic") directly
        root_semantic = merged.get("semantic")
        if isinstance(root_semantic, dict):
            semantic_data = root_semantic
            logger.debug("Found semantic data at root level")

    # Extract intent name early for reservation contract enforcement
    merged_intent_name = merged.get("intent", {}).get(
        "name", "") if isinstance(merged.get("intent"), dict) else ""

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
        # CRITICAL: Extract time from time_constraint dict (handles both string and dict with start/mode)
        if (time_refs or time_constraint) and "time" not in luma_slots:
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
        # CRITICAL: Extract time from time_constraint dict (handles both string and dict with start/mode)
        if (time_refs or time_constraint) and "time" not in luma_slots:
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

    # Additional fallback: Check if Luma provided date/time directly in merged response
    # (Sometimes Luma provides date in slots even without semantic data)
    if "date" not in luma_slots:
        # Check if date exists in merged response slots (Luma might have added it)
        direct_date = merged.get("slots", {}).get("date")
        if direct_date:
            luma_slots["date"] = direct_date
            logger.debug(
                f"Extracted date from merged.slots.date: {direct_date}")

    if "time" not in luma_slots:
        # Check if time exists in merged response slots
        direct_time = merged.get("slots", {}).get("time")
        if direct_time:
            luma_slots["time"] = direct_time
            logger.debug(
                f"Extracted time from merged.slots.time: {direct_time}")

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
    merged_slots = session_slots.copy()

    print(f"[DEBUG] Merge: session_slots={session_slots}")
    print(f"[DEBUG] Merge: merged_slots (after copy)={merged_slots}")

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

    print(f"[DEBUG] Merge: merged_slots (after luma merge)={merged_slots}")
    print(f"[DEBUG] Merge: merged_slots.keys()={list(merged_slots.keys())}")

    # LOG: merged_slots after merge
    logger.info(
        f"[SLOT_DURABILITY] merged_slots after merge: {list(merged_slots.keys())} = {merged_slots}"
    )
    print(
        f"[SLOT_DURABILITY] merged_slots after merge: {list(merged_slots.keys())} = {merged_slots}")

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

    # RESERVATION DATE ROUTING: Route generic date to start_date or end_date based on awaiting_slot
    # Rules:
    # 1. If intent == CREATE_RESERVATION
    # 2. And awaiting_slot is set to "start_date" or "end_date"
    # 3. And merged slots contain a `date`
    # 4. Then route: date → awaiting_slot, remove generic `date`, persist routed slot
    # 5. After routing, missing_slots will be recomputed from session.slots.keys()
    merged_intent_name = merged.get("intent", {}).get(
        "name", "") if isinstance(merged.get("intent"), dict) else ""

    # TRACE 1: Immediately after intent resolution (before any early return)
    import json
    print(json.dumps({
        "trace_point": "AFTER_INTENT",
        "intent": merged_intent_name,
        "is_first_turn": session_state is None,
        "raw_luma_slots": luma_response.get("slots") if luma_response else None,
        "raw_luma_context": luma_response.get("context") if luma_response else None,
    }))

    print(
        f"[AWAITING_SLOT_DEBUG] Reservation routing check: "
        f"intent={merged_intent_name}, "
        f"session_state={session_state is not None}, "
        f"merged_slots.keys()={list(merged_slots.keys())}"
    )
    if merged_intent_name == "CREATE_RESERVATION":
        awaiting_slot = session_state.get(
            "awaiting_slot") if session_state else None
        print(
            f"[AWAITING_SLOT_DEBUG] Reservation routing: "
            f"awaiting_slot={awaiting_slot}, "
            f"'date' in merged_slots={'date' in merged_slots}"
        )
        # CRITICAL: Only route to start_date, NOT end_date
        # Test requirement: "Core does NOT infer that a second `date` means `end_date`"
        # Routing to start_date is allowed (first date in range), but routing to end_date is forbidden
        if awaiting_slot == "start_date" and "date" in merged_slots:
            date_value = merged_slots.get("date")
            print(
                f"[AWAITING_SLOT_DEBUG] Routing condition met: "
                f"awaiting_slot={awaiting_slot}, date_value={date_value}, "
                f"merged_slots before routing={list(merged_slots.keys())}"
            )
            if date_value is not None:
                # Route date to awaiting_slot
                merged_slots[awaiting_slot] = date_value
                # Remove generic date (it's been routed to the specific slot)
                del merged_slots["date"]

                # CRITICAL: Clear awaiting_slot after routing - the awaited slot is now filled
                # awaiting_slot should only persist until the slot is actually filled
                # This prevents premature NEEDS_CLARIFICATION when awaiting_slot is set but slot is filled
                if "awaiting_slot" in merged:
                    del merged["awaiting_slot"]
                    print(
                        f"[AWAITING_SLOT_DEBUG] Cleared awaiting_slot from merged after routing: "
                        f"awaiting_slot={awaiting_slot} is now filled in merged_slots"
                    )

                logger.info(
                    f"[RESERVATION_DATE_ROUTING] Routed date={date_value} to {awaiting_slot} "
                    f"(removed generic 'date' slot, cleared awaiting_slot)"
                )
                print(
                    f"[RESERVATION_DATE_ROUTING] Routed date={date_value} to {awaiting_slot} "
                    f"(removed generic 'date' slot, cleared awaiting_slot)"
                )
                print(
                    f"[AWAITING_SLOT_DEBUG] After routing: "
                    f"awaiting_slot={awaiting_slot}, "
                    f"merged_slots.keys()={list(merged_slots.keys())}, "
                    f"routed_slot_value={merged_slots.get(awaiting_slot)}, "
                    f"awaiting_slot in merged_slots={awaiting_slot in merged_slots}, "
                    f"awaiting_slot in merged={merged.get('awaiting_slot')}"
                )

        # RESERVATION DATE PROMOTION: REMOVED
        # FIX: Stop auto-promoting generic slots.date into start_date for CREATE_RESERVATION
        # Only set reservation start_date/end_date when Luma explicitly provides:
        # - slots.start_date / slots.end_date, OR
        # - slots.date_range, OR
        # - explicit range cues (date_roles with START_DATE/END_DATE)
        # If Luma returns only slots.date for CREATE_RESERVATION, keep it as slots.date
        # and do NOT treat it as satisfying start_date.
        # This prevents scenarios like service_date_time_not_applied_to_reservation
        # where core currently fills start_date from date.

    # Update merged response with merged slots
    # CRITICAL: Ensure all session slots are preserved (non-destructive merge)
    # After reservation date routing, merged_slots contains the routed slot (start_date or end_date)
    # and the generic 'date' has been removed
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
    # Formula: missing_slots = required_slots(intent) - promoted_collected_slots

    # Import central slot contract functions
    from core.orchestration.api.slot_contract import (
        compute_missing_slots,
        filter_collected_slots_for_intent,
        promote_slots_for_intent
    )

    # Note: compute_missing_slots is also imported later for informational turns - that's intentional

    # Preserve awaiting_slot from session (if no intent change)
    # awaiting_slot indicates the session is explicitly awaiting a slot value
    # It must be preserved across turns until explicitly resolved
    awaiting_slot_from_session = None
    if session_state and isinstance(session_state, dict):
        awaiting_slot_from_session = session_state.get("awaiting_slot")
        print(
            f"[AWAITING_SLOT_DEBUG] Preserving awaiting_slot from session: "
            f"awaiting_slot_from_session={awaiting_slot_from_session}, "
            f"merged_slots.keys()={list(merged_slots.keys())}, "
            f"awaiting_slot_in_merged_slots={awaiting_slot_from_session in merged_slots if awaiting_slot_from_session else False}, "
            f"awaiting_slot_already_in_merged={merged.get('awaiting_slot')}"
        )
        if awaiting_slot_from_session:
            # Preserve awaiting_slot in merged response (will be checked in plan building)
            # Note: If routing cleared awaiting_slot, it won't be preserved here
            merged["awaiting_slot"] = awaiting_slot_from_session
            logger.debug(
                f"Preserved awaiting_slot={awaiting_slot_from_session} from session"
            )

    # STEP 3.6: Handle intent change (hard boundary)
    # ARCHITECTURAL INVARIANT: Intent change is a hard boundary
    # On intent change:
    # - Drop slots not valid for the new intent
    # - Preserve slots that overlap semantically (e.g., service_id if applicable)
    # - Recompute missing_slots from NEW intent contract ONLY

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

        # Reset awaiting_slot on intent change (it's only valid for same intent)
        if "awaiting_slot" in merged:
            del merged["awaiting_slot"]
            logger.debug(
                "[INTENT_CHANGE] Reset awaiting_slot (intent changed)")

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

    print(f"[SESSION_MERGE] ========== REACHED MODIFICATION CONTEXT DETECTION POINT ==========")
    print(f"[SESSION_MERGE] merged_intent_name={merged_intent_name}")
    print(
        f"[SESSION_MERGE] raw_luma_slots={raw_luma_slots if 'raw_luma_slots' in locals() else 'NOT_IN_SCOPE'}")

    # STEP 3.4.1: Detect and persist modification context for MODIFY_* intents
    # CRITICAL: raw_luma_slots must be available here (set at line 86)
    print(f"[SESSION_MERGE] ========== ABOUT TO DETECT MODIFICATION CONTEXT ==========")
    print(f"[SESSION_MERGE] merged_intent_name={merged_intent_name}")
    print(
        f"[SESSION_MERGE] raw_luma_slots available={hasattr(locals(), 'raw_luma_slots') or 'raw_luma_slots' in globals()}")
    # CRITICAL: This must run BEFORE informational-turn early return and BEFORE slot promotion
    # This ensures modification context is available even when slots are empty
    # Modification context is INTENT-DRIVEN, not slot-driven
    # It uses raw_luma_slots (available before promotion) to detect modification type
    # If raw_luma_slots are empty, still set default context for MODIFY_* intents
    print(f"[SESSION_MERGE] ========== MODIFICATION CONTEXT DETECTION (INTENT-DRIVEN) ==========")
    print(f"[SESSION_MERGE] merged_intent_name={merged_intent_name}")
    print(f"[SESSION_MERGE] raw_luma_slots={raw_luma_slots}")
    print(f"[SESSION_MERGE] raw_luma_slots keys={list(raw_luma_slots.keys())}")

    modification_context = None
    if merged_intent_name == "MODIFY_BOOKING":
        print(
            f"[SESSION_MERGE] MODIFY_BOOKING: Detecting modification context (intent-driven)")
        # Detect modification type from raw_luma_slots (before promotion)
        # This is intent-driven: we detect MODIFY_BOOKING intent, then check for signals
        has_time = "time" in raw_luma_slots and raw_luma_slots.get(
            "time") is not None
        has_date = "date" in raw_luma_slots and raw_luma_slots.get(
            "date") is not None

        print(
            f"[SESSION_MERGE] MODIFY_BOOKING: has_time={has_time}, has_date={has_date}")

        # Always set modification context for MODIFY_BOOKING (intent-driven)
        # If no slots detected, set default context that will be refined later
        modification_context = {
            "modifying_time": has_time,
            "modifying_date": has_date
        }
        print(
            f"[SESSION_MERGE] MODIFY_BOOKING: ✓ Detected modification context (intent-driven): {modification_context}")
        # Persist modification context to merged response (will be persisted to session)
        merged["_modification_context"] = modification_context
        print(
            f"[SESSION_MERGE] MODIFY_BOOKING: Persisted _modification_context to merged response")

    elif merged_intent_name == "MODIFY_RESERVATION":
        print(
            f"[SESSION_MERGE] MODIFY_RESERVATION: Detecting modification context (intent-driven)")
        # Detect modification type from raw_luma_slots (before promotion)
        # This is intent-driven: we detect MODIFY_RESERVATION intent, then check for signals
        has_start_date = "start_date" in raw_luma_slots and raw_luma_slots.get(
            "start_date") is not None
        has_end_date = "end_date" in raw_luma_slots and raw_luma_slots.get(
            "end_date") is not None
        has_date = "date" in raw_luma_slots and raw_luma_slots.get(
            "date") is not None

        print(
            f"[SESSION_MERGE] MODIFY_RESERVATION: has_start_date={has_start_date}, has_end_date={has_end_date}, has_date={has_date}")

        # Always set modification context for MODIFY_RESERVATION (intent-driven)
        # If no slots detected, set default context that will be refined later
        modification_context = {
            "modifying_start_date": has_start_date,
            "modifying_end_date": has_end_date,
            "modifying_date": has_date
        }
        print(
            f"[SESSION_MERGE] MODIFY_RESERVATION: ✓ Detected modification context (intent-driven): {modification_context}")
        # Persist modification context to merged response (will be persisted to session)
        merged["_modification_context"] = modification_context
        print(
            f"[SESSION_MERGE] MODIFY_RESERVATION: Persisted _modification_context to merged response")
    else:
        print(
            f"[SESSION_MERGE] Not MODIFY_* intent (merged_intent_name={merged_intent_name}), skipping modification context detection")

    # If no modification context detected in current turn, check session for persisted context
    print(
        f"[SESSION_MERGE] modification_context after detection={modification_context}")
    if not modification_context and session_state:
        print(
            f"[SESSION_MERGE] No modification context detected, checking session for persisted context")
        persisted_context = session_state.get("_modification_context")
        print(
            f"[SESSION_MERGE] session_state._modification_context={persisted_context}")
        if persisted_context:
            modification_context = persisted_context
            merged["_modification_context"] = modification_context
            print(
                f"[SESSION_MERGE] ✓ Using persisted modification context from session: {modification_context}")
        else:
            print(f"[SESSION_MERGE] ✗ No persisted modification context in session")

    print(f"[SESSION_MERGE] FINAL modification_context={modification_context}")
    print(
        f"[SESSION_MERGE] merged.get('_modification_context')={merged.get('_modification_context')}")
    print(f"[SESSION_MERGE] ========== END MODIFICATION CONTEXT DETECTION ==========")

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

    # For informational turns WITH new slots: use session intent but still process normally
    effective_intent = merged_intent_name
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

    # Ensure effective_intent is set (fallback to merged_intent_name)
    if not effective_intent:
        effective_intent = merged_intent_name

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
    # Capture before promotion for logging
    merged_session_slots = merged_slots.copy()
    print(f"[SESSION_MERGE] ========== BEFORE PROMOTION ==========")
    print(
        f"[SESSION_MERGE] About to call promote_slots_for_intent with intent={effective_intent}")
    promoted_slots = promote_slots_for_intent(
        merged_slots, effective_intent, context)
    print(f"[SESSION_MERGE] ========== AFTER PROMOTION ==========")
    print(f"[SESSION_MERGE] promoted_slots returned: {promoted_slots}")
    print(
        f"[SESSION_MERGE] promoted_slots keys: {list(promoted_slots.keys())}")

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
    # This ensures modification context is available for required slot computation
    # CRITICAL: modification_context must be available for compute_missing_slots call later
    modification_context = merged.get("_modification_context")
    if not modification_context and session_state:
        # Fallback: check session for persisted context (shouldn't be needed if detection worked)
        modification_context = session_state.get("_modification_context")
        if modification_context:
            merged["_modification_context"] = modification_context
            print(
                f"[SESSION_MERGE] Using persisted modification context from session (fallback): {modification_context}")

    # Store modification_context in a variable that will be available for compute_missing_slots
    # This ensures it's in scope when compute_missing_slots is called later

    print(f"[DEBUG] Promotion: merged_slots={merged_slots}")
    print(f"[DEBUG] Promotion: promoted_slots={promoted_slots}")
    print(
        f"[DEBUG] Promotion: promoted_slots.keys()={list(promoted_slots.keys())}")

    print(
        f"[MERGE] Slot promotion: intent={effective_intent}, "
        f"raw_slots={list(merged_slots.keys())}, promoted_slots={list(promoted_slots.keys())}")

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
    from core.orchestration.api.slot_contract import filter_slots_by_domain
    domain_filtered_slots = filter_slots_by_domain(
        promoted_slots, effective_intent)

    # STEP 4.1.6: For CREATE_RESERVATION, strip generic 'date' from effective_slots
    # unless explicitly routed via awaiting_slot
    # Generic 'date' must NOT satisfy start_date/end_date
    effective_slots_for_computation = domain_filtered_slots.copy()
    if effective_intent == "CREATE_RESERVATION":
        awaiting_slot = merged.get("awaiting_slot")
        if "date" in effective_slots_for_computation and awaiting_slot != "date":
            # Strip generic 'date' - it should only route via awaiting_slot, not satisfy required slots
            del effective_slots_for_computation["date"]
            logger.info(
                f"[DOMAIN_FILTER] Stripped generic 'date' from CREATE_RESERVATION effective_slots "
                f"(awaiting_slot={awaiting_slot})"
            )
            print(
                f"[DOMAIN_FILTER] Stripped generic 'date' from CREATE_RESERVATION effective_slots "
                f"(awaiting_slot={awaiting_slot})"
            )

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

    print(f"[SESSION_MERGE] ========== BEFORE MISSING_SLOTS COMPUTATION ==========")
    print(f"[SESSION_MERGE] effective_intent={effective_intent}")
    print(
        f"[SESSION_MERGE] durable_slots_for_computation keys={list(durable_slots_for_computation.keys())}")
    print(
        f"[SESSION_MERGE] durable_slots_for_computation={durable_slots_for_computation}")
    for key, value in durable_slots_for_computation.items():
        print(f"[SESSION_MERGE]   durable_slot[{key}] = {value}")
    print(
        f"[AWAITING_SLOT_DEBUG] Before compute_missing_slots: "
        f"effective_intent={effective_intent}, "
        f"durable_slots.keys()={list(durable_slots_for_computation.keys())}, "
        f"awaiting_slot_in_merged={merged.get('awaiting_slot')}, "
        f"awaiting_slot_in_session={session_state.get('awaiting_slot') if session_state else None}"
    )

    # LOG: intent, durable slots (session.slots after promotion), and computed missing_slots
    if is_intent_change_recomputation:
        logger.info(
            f"[INTENT_CHANGE] Recomputing missing_slots from NEW intent contract: "
            f"intent={effective_intent}, "
            f"durable_slots={list(durable_slots_for_computation.keys())}"
        )
        print(
            f"[INTENT_CHANGE] Recomputing missing_slots from NEW intent contract: "
            f"intent={effective_intent}, "
            f"durable_slots={list(durable_slots_for_computation.keys())}"
        )
    else:
        logger.info(
            f"[MISSING_SLOTS] Computing missing_slots: "
            f"intent={effective_intent}, "
            f"durable_slots={list(durable_slots_for_computation.keys())}"
        )
        print(
            f"[MISSING_SLOTS] Computing missing_slots: "
            f"intent={effective_intent}, "
            f"durable_slots={list(durable_slots_for_computation.keys())}"
        )

    # Compute missing_slots from durable slots (session.slots after promotion)
    # Formula: missing_slots = REQUIRED_SLOTS(intent) - durable_slots.keys()
    # CRITICAL: A slot is satisfied ONLY if it exists in durable_slots under its exact slot name
    # No inference, no type-based satisfaction, no sibling slot satisfaction
    # CRITICAL: On intent change, this uses the NEW intent contract (effective_intent = new intent)
    #
    # CRITICAL: For MODIFY_* intents, modification_context (detected from current turn or persisted)
    # is passed to compute_missing_slots to enable context-aware required slot derivation
    # even when current turn slots are empty. This does NOT depend on session.slots truthiness.

    print(f"[SESSION_MERGE] ========== CALLING compute_missing_slots ==========")
    print(f"[SESSION_MERGE] effective_intent={effective_intent}")
    print(
        f"[SESSION_MERGE] durable_slots_for_computation type={type(durable_slots_for_computation)}")
    print(
        f"[SESSION_MERGE] durable_slots_for_computation={durable_slots_for_computation}")
    print(
        f"[SESSION_MERGE] durable_slots_for_computation keys={list(durable_slots_for_computation.keys()) if durable_slots_for_computation else 'N/A'}")
    print(f"[SESSION_MERGE] modification_context={modification_context}")
    if durable_slots_for_computation:
        for key, value in durable_slots_for_computation.items():
            print(
                f"[SESSION_MERGE]   durable_slot[{key}] = {value} (type={type(value)})")

    # BEFORE_REQUIRED_SLOTS: Log right before required-slot computation
    before_required_slots_log = {
        "trace": "BEFORE_REQUIRED_SLOTS",
        "intent": effective_intent,
        "slots_used": durable_slots_for_computation,
        "session_slots": session_state.get("slots") if session_state else None,
        "modification_context": modification_context
    }
    # Note: json is already imported at module level (line 10)
    logger.info("BEFORE_REQUIRED_SLOTS: %s", json.dumps(
        before_required_slots_log, ensure_ascii=False, default=str))
    print(
        f"\n[BEFORE_REQUIRED_SLOTS] {json.dumps(before_required_slots_log, ensure_ascii=False, default=str)}")

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

    missing_slots = compute_missing_slots(
        effective_intent, durable_slots_for_computation, modification_context, session_state)
    print(f"[SESSION_MERGE] compute_missing_slots returned: {missing_slots}")
    print(f"[SESSION_MERGE] ========== RETURNED FROM compute_missing_slots ==========")

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

    print(
        f"[AWAITING_SLOT_DEBUG] After compute_missing_slots: "
        f"missing_slots={missing_slots}, "
        f"awaiting_slot_in_merged={merged.get('awaiting_slot')}, "
        f"awaiting_slot_in_session={session_state.get('awaiting_slot') if session_state else None}, "
        f"awaiting_slot_in_missing={merged.get('awaiting_slot') in missing_slots if merged.get('awaiting_slot') else 'N/A'}"
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

    # LOG: computed missing_slots
    logger.info(
        f"[MISSING_SLOTS] Computed missing_slots: {missing_slots}"
    )
    print(f"[MISSING_SLOTS] Computed missing_slots: {missing_slots}")

    # Set missing_slots in merged response (for plan building)
    # Set missing_slots in merged response (for plan building)
    # NOTE: missing_slots computed here is for planning purposes
    # It will be recomputed from persisted slots in build_session_state_from_outcome
    # to ensure it reflects what's actually persisted, not pre-persistence state
    # The recomputed missing_slots will then be persisted to session_state
    merged["missing_slots"] = missing_slots

    print(
        f"[AWAITING_SLOT_DEBUG] After setting missing_slots in merged: "
        f"merged['missing_slots']={merged.get('missing_slots')}, "
        f"merged['awaiting_slot']={merged.get('awaiting_slot')}, "
        f"merged['slots'].keys()={list(merged.get('slots', {}).keys())}"
    )

    print(
        f"[AWAITING_SLOT_DEBUG] After setting missing_slots in merged: "
        f"merged['missing_slots']={merged.get('missing_slots')}, "
        f"merged['awaiting_slot']={merged.get('awaiting_slot')}, "
        f"merged['slots'].keys()={list(merged.get('slots', {}).keys())}"
    )

    # Remove force recompute flag (no longer needed after computation)
    if "_force_recompute_missing_slots" in merged:
        del merged["_force_recompute_missing_slots"]

    # ARCHITECTURAL FIX: Store effective collected slots (post-promotion) for persistence
    # These are the slots that actually satisfy required slots after promotion
    # This ensures slots explicitly satisfied in a turn are persisted so they're not re-computed as missing
    # Pass awaiting_slot for CREATE_RESERVATION date handling
    awaiting_slot_for_computation = merged.get("awaiting_slot")
    effective_collected_slots = _compute_effective_collected_slots_internal(
        promoted_slots, effective_intent, awaiting_slot_for_computation
    )
    merged["_effective_collected_slots"] = effective_collected_slots

    # STRUCTURED DEBUG: Slot state transitions after promotion and before finalization
    # This trace object allows debugging slot transformations without stepping through code
    slot_state_trace = {
        "intent": effective_intent,
        "raw_luma_slots": {
            "keys": list(raw_luma_slots.keys()),
            "values": {k: str(v)[:50] for k, v in raw_luma_slots.items()}
        },
        "merged_session_slots": {
            "keys": list(merged_session_slots.keys()),
            "values": {k: str(v)[:50] for k, v in merged_session_slots.items()}
        },
        "promoted_slots": {
            "keys": list(promoted_slots.keys()),
            "values": {k: str(v)[:50] for k, v in promoted_slots.items()}
        },
        "effective_collected_slots": {
            "keys": list(effective_collected_slots.keys()),
            "values": {k: str(v)[:50] for k, v in effective_collected_slots.items()}
        },
        "missing_slots": missing_slots,
        "awaiting_slot": merged.get("awaiting_slot")
    }

    logger.info(
        f"[SLOT_STATE_TRACE] After promotion, before finalization: intent={effective_intent}, "
        f"raw_luma_slots_keys={list(raw_luma_slots.keys())}, "
        f"merged_session_slots_keys={list(merged_session_slots.keys())}, "
        f"promoted_slots_keys={list(promoted_slots.keys())}, "
        f"effective_collected_slots_keys={list(effective_collected_slots.keys())}, "
        f"missing_slots={missing_slots}, awaiting_slot={merged.get('awaiting_slot')}"
    )
    import json
    print(
        f"[SLOT_STATE_TRACE] After promotion, before finalization: {json.dumps(slot_state_trace, indent=2)}")

    print(
        f"[MERGE] Computed missing_slots fresh: intent={effective_intent}, "
        f"raw_slots={list(merged_slots.keys())}, promoted_slots={list(promoted_slots.keys())}, "
        f"effective_collected={list(effective_collected_slots.keys())}, missing_slots={missing_slots}")
    logger.debug(
        f"Computed missing_slots fresh: intent={effective_intent}, "
        f"raw={list(merged_slots.keys())}, promoted={list(promoted_slots.keys())}, "
        f"effective_collected={list(effective_collected_slots.keys())}, missing={missing_slots}")

    # CONTRACT ENFORCEMENT: missing_slots are computed fresh from intent contract
    # When intent is CREATE_RESERVATION, required slots are ["service_id", "start_date", "end_date"]
    # When intent changes, collected slots are filtered to prevent cross-domain leakage
    # missing_slots = required_slots - collected_slots (computed fresh every turn)

    # Assertion: session.intent determines planner path exclusively
    # Verify that merged intent matches session intent (when session exists and not reset)
    merged_intent = merged.get("intent", {})
    merged_intent_name = merged_intent.get(
        "name", "") if isinstance(merged_intent, dict) else ""
    if session_intent and session_status != "READY":
        session_intent_str = session_intent if isinstance(
            session_intent, str) else session_intent.get("name", "")
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
    awaiting_slot: Optional[str] = None
) -> Dict[str, Any]:
    """
    Compute effective collected slots from promoted slots.

    CRITICAL: Domain slot isolation is enforced here:
    - Filter slots by domain BEFORE computing effective_collected_slots
    - Prevent service_id from appearing in reservation slots if not valid
    - Prevent generic 'date' from satisfying start_date/end_date for CREATE_RESERVATION

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
    domain_filtered_slots = filter_slots_by_domain(
        promoted_slots, effective_intent)

    # CRITICAL: For CREATE_RESERVATION, strip generic 'date' unless routed via awaiting_slot
    if effective_intent == "CREATE_RESERVATION":
        effective_slots_for_filtering = domain_filtered_slots.copy()
        if "date" in effective_slots_for_filtering and awaiting_slot != "date":
            # Strip generic 'date' - it should only route via awaiting_slot, not satisfy required slots
            del effective_slots_for_filtering["date"]
    else:
        effective_slots_for_filtering = domain_filtered_slots

    required_slots_set = set(get_required_slots_for_intent(effective_intent))

    print(f"[DEBUG] Computing effective_collected_slots:")
    print(f"  effective_intent={effective_intent}")
    print(f"  required_slots_set={required_slots_set}")
    print(f"  promoted_slots.keys()={list(promoted_slots.keys())}")
    print(
        f"  domain_filtered_slots.keys()={list(domain_filtered_slots.keys())}")
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


def _compute_effective_collected_slots(luma_response: Dict[str, Any]) -> Dict[str, Any]:
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
    import json
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

    # Get raw slots from Luma response (before promotion)
    raw_slots = luma_response.get("slots", {})
    if not isinstance(raw_slots, dict):
        raw_slots = {}

    # STEP: Detect and persist modification context for MODIFY_* intents (BEFORE promotion)
    # CRITICAL: This must run for first turns (no session) to ensure modification context is persisted
    # Modification context is INTENT-DRIVEN, not slot-driven
    print(f"[_compute_effective_collected_slots] ========== MODIFICATION CONTEXT DETECTION (FIRST TURN) ==========")
    print(f"[_compute_effective_collected_slots] intent_name={intent_name}")
    print(f"[_compute_effective_collected_slots] raw_slots={raw_slots}")
    print(
        f"[_compute_effective_collected_slots] raw_slots keys={list(raw_slots.keys())}")

    modification_context = None
    if intent_name == "MODIFY_BOOKING":
        print(f"[_compute_effective_collected_slots] MODIFY_BOOKING: Detecting modification context (intent-driven)")
        # Detect modification type from raw_slots (before promotion)
        has_time = "time" in raw_slots and raw_slots.get("time") is not None
        has_date = "date" in raw_slots and raw_slots.get("date") is not None

        print(
            f"[_compute_effective_collected_slots] MODIFY_BOOKING: has_time={has_time}, has_date={has_date}")

        # Always set modification context for MODIFY_BOOKING (intent-driven)
        modification_context = {
            "modifying_time": has_time,
            "modifying_date": has_date
        }
        print(
            f"[_compute_effective_collected_slots] MODIFY_BOOKING: ✓ Detected modification context: {modification_context}")
        luma_response["_modification_context"] = modification_context
        print(f"[_compute_effective_collected_slots] MODIFY_BOOKING: Persisted _modification_context to luma_response")

    elif intent_name == "MODIFY_RESERVATION":
        print(f"[_compute_effective_collected_slots] MODIFY_RESERVATION: Detecting modification context (intent-driven)")
        # Detect modification type from raw_slots (before promotion)
        has_start_date = "start_date" in raw_slots and raw_slots.get(
            "start_date") is not None
        has_end_date = "end_date" in raw_slots and raw_slots.get(
            "end_date") is not None
        has_date = "date" in raw_slots and raw_slots.get("date") is not None

        print(
            f"[_compute_effective_collected_slots] MODIFY_RESERVATION: has_start_date={has_start_date}, has_end_date={has_end_date}, has_date={has_date}")

        # Always set modification context for MODIFY_RESERVATION (intent-driven)
        modification_context = {
            "modifying_start_date": has_start_date,
            "modifying_end_date": has_end_date,
            "modifying_date": has_date
        }
        print(
            f"[_compute_effective_collected_slots] MODIFY_RESERVATION: ✓ Detected modification context: {modification_context}")
        luma_response["_modification_context"] = modification_context
        print(f"[_compute_effective_collected_slots] MODIFY_RESERVATION: Persisted _modification_context to luma_response")
    else:
        print(
            f"[_compute_effective_collected_slots] Not MODIFY_* intent (intent_name={intent_name}), skipping modification context detection")

    print(
        f"[_compute_effective_collected_slots] FINAL modification_context={modification_context}")
    print(
        f"[_compute_effective_collected_slots] luma_response.get('_modification_context')={luma_response.get('_modification_context')}")
    print(f"[_compute_effective_collected_slots] ========== END MODIFICATION CONTEXT DETECTION ==========")

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
    # Note: awaiting_slot not available in this path (no session), pass None
    effective_collected_slots = _compute_effective_collected_slots_internal(
        promoted_slots, intent_name, None
    )

    # TRACE 2: After modification context detection (both paths)
    import json
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
    from core.orchestration.api.slot_contract import compute_missing_slots
    # Use modification context if available (detected above for MODIFY_* intents)
    modification_context = luma_response.get("_modification_context")
    print(
        f"[_compute_effective_collected_slots] Computing missing_slots with modification_context={modification_context}")

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

    missing_slots = compute_missing_slots(
        intent_name, promoted_slots, modification_context, None)

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
    previous_session_state: Optional[Dict[str, Any]] = None
) -> Optional[Dict[str, Any]]:
    """
    Build session state from outcome and merged Luma response.

    CONTRACT: Persist conversation state, not just slots:
    - Always save session.awaiting_slot if present in plan (for routing continuity)
    - Always save session.missing_slots (recomputed from effective_collected_slots) for continuity/debugging

    CRITICAL: missing_slots is recomputed from effective_collected_slots (post-promotion)
    and is the ONLY source of truth. NO later code must overwrite it with raw/filtered missing slots.

    Args:
        outcome: Outcome dictionary from handle_message
        outcome_status: Outcome status ("READY" | "NEEDS_CLARIFICATION" | "AWAITING_CONFIRMATION")
        merged_luma_response: Optional merged Luma response (for extracting intent)
        previous_session_state: Optional previous session state (unused, for compatibility)

    Returns:
        Session state dictionary (WITH awaiting_slot and missing_slots if present) or None if status is READY
    """
    # Don't save session for READY or EXECUTED status - session should be cleared
    if outcome_status in ("READY", "EXECUTED"):
        return None

    # Guard: outcome must be a dict
    if not outcome or not isinstance(outcome, dict):
        print(
            f"[ERROR] build_session_state_from_outcome: outcome is None or not a dict: {outcome}")
        return None

    # Extract facts (contains slots and missing_slots)
    facts = outcome.get("facts", {})
    if not isinstance(facts, dict):
        facts = {}

    # ARCHITECTURAL INVARIANT: Persist ALL collected slots, not only effective or required ones
    # session.slots is the single source of truth for collected slots
    # Slots present in session MUST be preserved across turns unless intent changes
    # Do NOT filter slots using REQUIRED_SLOTS during persistence
    # Do NOT drop slots because they were not mentioned this turn

    # Get ALL merged slots from merged_luma_response (session slots + luma slots)
    # These are the durable facts that must be preserved
    slots = {}
    try:
        if merged_luma_response and isinstance(merged_luma_response, dict):
            slots = merged_luma_response.get("slots", {})
            if not isinstance(slots, dict):
                slots = {}
    except Exception as e:
        print(
            f"[ERROR] build_session_state_from_outcome: Exception accessing merged_luma_response: {e}")
        print(f"  merged_luma_response type: {type(merged_luma_response)}")
        print(f"  merged_luma_response: {merged_luma_response}")
        slots = {}

    print(f"[DEBUG] build_session_state_from_outcome:")
    print(f"  merged_luma_response type: {type(merged_luma_response)}")
    print(f"  merged_luma_response={merged_luma_response}")
    print(f"  slots from merged_luma_response={slots}")
    print(f"  slots.keys()={list(slots.keys())}")

    # LOG: persisted session.slots
    logger.info(
        f"[SLOT_DURABILITY] persisted session.slots: {list(slots.keys())} = {slots}"
    )
    print(
        f"[SLOT_DURABILITY] persisted session.slots: {list(slots.keys())} = {slots}")

    print(f"[DEBUG] Persisting to session: slots={slots}")
    print(f"[DEBUG] Persisting to session: slots.keys()={list(slots.keys())}")

    # Extract intent - prefer from merged Luma response, fallback to outcome
    intent_name = ""
    if merged_luma_response and isinstance(merged_luma_response, dict):
        intent_obj = merged_luma_response.get("intent", {})
        if isinstance(intent_obj, dict):
            intent_name = intent_obj.get("name", "")
        elif isinstance(intent_obj, str):
            intent_name = intent_obj
    if not intent_name and "intent_name" in outcome:
        intent_name = outcome.get("intent_name", "")

    # CRITICAL: Recompute missing_slots from persisted slots AFTER persistence
    # ARCHITECTURAL INVARIANT: missing_slots = REQUIRED_SLOTS(intent) - session.slots.keys()
    # missing_slots must be computed ONLY from durable session.slots after merging and persisting
    # This is the ONLY source of truth - DO NOT use raw_missing_slots or filtered missing_slots
    # Do NOT carry forward missing_slots computed before merge/persistence
    # This ensures missing_slots accurately reflects what's actually persisted
    recomputed_missing_slots = None
    if intent_name:
        from core.orchestration.api.slot_contract import compute_missing_slots
        # Use modification context from merged_luma_response (detected earlier) or previous_session_state
        modification_context = None
        if merged_luma_response:
            modification_context = merged_luma_response.get(
                "_modification_context")
        if not modification_context and previous_session_state:
            modification_context = previous_session_state.get(
                "_modification_context")
        recomputed_missing_slots = compute_missing_slots(
            intent_name, slots, modification_context, previous_session_state)

        # Normalize MODIFY_BOOKING missing_slots (test contract)
        from core.orchestration.nlu.luma_response_processor import _normalize_modify_booking_missing_slots
        recomputed_missing_slots = _normalize_modify_booking_missing_slots(
            recomputed_missing_slots, merged_luma_response or {}
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
        "NEEDS_CLARIFICATION", "AWAITING_CONFIRMATION") else "READY"

    # Extract awaiting_slot from plan (if present)
    # awaiting_slot is computed when exactly one missing slot exists
    # CRITICAL: Reset awaiting_slot on intent/domain change
    awaiting_slot = None
    plan = outcome.get("plan", {})
    if isinstance(plan, dict):
        awaiting_slot = plan.get("awaiting_slot")

    # Reset awaiting_slot if intent changed (it's only valid for same intent)
    previous_intent = None
    if previous_session_state:
        previous_intent = previous_session_state.get("intent")
        if isinstance(previous_intent, dict):
            previous_intent = previous_intent.get("name", "")
    if previous_intent and intent_name and previous_intent != intent_name:
        # Intent changed - reset awaiting_slot
        awaiting_slot = None
        print(
            f"[BUILD_SESSION] Intent changed: {previous_intent} -> {intent_name}, resetting awaiting_slot")

    # Build session state WITH missing_slots and awaiting_slot (for conversation continuity)
    # missing_slots are recomputed from effective_collected_slots (post-promotion) and persisted
    # awaiting_slot is persisted if present in plan (for routing in next turn)
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

    session_state = {
        "intent": intent_name,
        # ARCHITECTURAL INVARIANT: Persist ALL collected slots (durable facts)
        # session.slots is the single source of truth for collected slots
        # Slots present in session MUST be preserved across turns unless intent changes
        # Do NOT filter slots using REQUIRED_SLOTS during persistence
        # service_id, start_date, end_date must never disappear once collected
        # ALL merged slots (session + luma), not filtered by required slots
        "slots": slots,
        "status": status
    }

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

    # CRITICAL: ALWAYS persist awaiting_slot when status=NEEDS_CLARIFICATION
    # awaiting_slot MUST come from plan.awaiting_slot (or merged_luma_response.awaiting_slot as fallback)
    # It MUST be persisted to ensure multi-turn flows keep the correct awaiting lock
    # Enforce: persisted.awaiting_slot == plan.awaiting_slot
    if status == "NEEDS_CLARIFICATION":
        # When status=NEEDS_CLARIFICATION, always persist awaiting_slot from plan
        # plan.awaiting_slot is the authoritative source
        if awaiting_slot is not None:
            session_state["awaiting_slot"] = awaiting_slot
        elif merged_luma_response and isinstance(merged_luma_response, dict):
            # Fallback: get awaiting_slot from merged_luma_response if not in plan
            awaiting_slot_from_merged = merged_luma_response.get(
                "awaiting_slot")
            if awaiting_slot_from_merged is not None:
                session_state["awaiting_slot"] = awaiting_slot_from_merged

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
            # Fallback: recompute missing_slots if not already computed
            from core.orchestration.api.slot_contract import compute_missing_slots
            from core.orchestration.nlu.luma_response_processor import _normalize_modify_booking_missing_slots
            # Use persisted modification context if available
            modification_context = slots.get(
                "_modification_context") if isinstance(slots, dict) else None
            missing_slots_to_persist = compute_missing_slots(
                intent_name, slots, modification_context)
            missing_slots_to_persist = _normalize_modify_booking_missing_slots(
                missing_slots_to_persist, merged_luma_response or {}
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
        f"awaiting_slot={session_state.get('awaiting_slot')}, "
        f"missing_slots={session_state.get('missing_slots', [])}")

    # CRITICAL: Structured snapshot log + invariant checks at exact point of session persistence
    # This runs after plan/outcome is finalized, before returning response
    # Only runs in test/debug mode
    import json
    if os.getenv("DEBUG_SESSION_PERSISTENCE") == "1" or os.getenv("PYTEST_CURRENT_TEST"):
        # Get all required data for snapshot
        final_intent = intent_name
        final_status = status
        final_awaiting_slot = session_state.get("awaiting_slot")
        final_missing_slots = session_state.get("missing_slots", [])

        # Compute effective_collected_slots from persisted slots
        effective_collected_slots = {}
        if final_intent:
            from core.orchestration.api.slot_contract import get_required_slots_for_intent
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
            from core.orchestration.api.slot_contract import get_required_slots_for_intent
            required_slots_list = get_required_slots_for_intent(final_intent)

        # Get plan.awaiting_slot for invariant check
        plan = outcome.get("plan", {})
        plan_awaiting_slot = plan.get(
            "awaiting_slot") if isinstance(plan, dict) else None

        # STRUCTURED SNAPSHOT LOG
        persistence_snapshot = {
            "intent": final_intent,
            "status": final_status,
            "awaiting_slot": final_awaiting_slot,
            "missing_slots": final_missing_slots,
            "effective_collected_slots": {
                "keys": list(effective_collected_slots.keys()),
                "values": {k: str(v)[:50] for k, v in effective_collected_slots.items()}
            },
            "required_slots": required_slots_list
        }

        logger.info(
            f"[SESSION_PERSISTENCE_SNAPSHOT] Final session state before persistence: "
            f"intent={final_intent}, status={final_status}, awaiting_slot={final_awaiting_slot}, "
            f"missing_slots={final_missing_slots}"
        )
        print(
            f"[SESSION_PERSISTENCE_SNAPSHOT] {json.dumps(persistence_snapshot, indent=2)}")

        # INVARIANT CHECKS (only in test/debug mode)
        invariant_violations = []

        # 1) awaiting_slot != None AND status == READY -> INVALID
        if final_awaiting_slot is not None and final_status == "READY":
            violation_msg = (
                f"INVARIANT VIOLATION 1: awaiting_slot={final_awaiting_slot} is not None "
                f"but status={final_status} is READY. awaiting_slot must be None when status is READY."
            )
            invariant_violations.append(violation_msg)
            logger.error(f"[SESSION_PERSISTENCE_INVARIANT] {violation_msg}")
            print(f"[SESSION_PERSISTENCE_INVARIANT] {violation_msg}")

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

        # 3) intent == CREATE_RESERVATION AND awaiting_slot is None AND missing_slots != [] -> INVALID
        if (final_intent == "CREATE_RESERVATION" and
            final_awaiting_slot is None and
                len(final_missing_slots) > 0):
            violation_msg = (
                f"INVARIANT VIOLATION 3: intent=CREATE_RESERVATION with missing_slots={final_missing_slots} "
                f"but awaiting_slot is None. For CREATE_RESERVATION with missing slots, awaiting_slot should be set."
            )
            invariant_violations.append(violation_msg)
            logger.error(f"[SESSION_PERSISTENCE_INVARIANT] {violation_msg}")
            print(f"[SESSION_PERSISTENCE_INVARIANT] {violation_msg}")

        # 4) persisted_session.awaiting_slot != plan.awaiting_slot -> INVALID
        if final_awaiting_slot != plan_awaiting_slot:
            violation_msg = (
                f"INVARIANT VIOLATION 4: persisted_session.awaiting_slot={final_awaiting_slot} "
                f"does not match plan.awaiting_slot={plan_awaiting_slot}. "
                f"They must be equal at persistence time."
            )
            invariant_violations.append(violation_msg)
            logger.error(f"[SESSION_PERSISTENCE_INVARIANT] {violation_msg}")
            print(f"[SESSION_PERSISTENCE_INVARIANT] {violation_msg}")

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

    return session_state
