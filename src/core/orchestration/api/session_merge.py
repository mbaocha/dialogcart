"""
Session Merge Helper

Merges session state with Luma response for follow-up handling.

This module provides pure functions for merging session state without
changing core logic.
"""

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
    
    # STEP 1: Handle intent - Session intent is immutable unless session is reset
    # If luma.intent == UNKNOWN: use session.intent (don't modify session intent)
    session_intent = session_state.get("intent")
    session_status = session_state.get("status", "")
    
    # Extract Luma intent
    luma_intent_obj = merged.get("intent", {})
    luma_intent_name = luma_intent_obj.get("name", "") if isinstance(luma_intent_obj, dict) else ""
    
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
    luma_slots = merged.get("slots", {}).copy()
    if not isinstance(luma_slots, dict):
        luma_slots = {}
    
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
                            return date_candidate.split("T")[0].split(" ")[0]  # Extract date part
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
        logger.debug(f"Extracted date using comprehensive helper: {extracted_date}")
    
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
                logger.debug(f"Extracted date from entities.date: {date_value}")
        if "time" in entities and "time" not in luma_slots:
            time_value = entities.get("time")
            if time_value:
                luma_slots["time"] = time_value
                logger.debug(f"Extracted time from entities.time: {time_value}")
    
    # Check if semantic data exists but wasn't found in trace/stages (try direct access)
    # Sometimes Luma provides semantic data at root level or in different structure
    if not semantic_data:
        # Try merged.get("semantic") directly
        root_semantic = merged.get("semantic")
        if isinstance(root_semantic, dict):
            semantic_data = root_semantic
            logger.debug("Found semantic data at root level")
    
    # If we still have semantic_data, process it (this handles the case where we found it in a different location)
    if isinstance(semantic_data, dict) and not any(k in luma_slots for k in ["date", "start_date", "time"]):
        date_refs = semantic_data.get("date_refs", [])
        date_mode = semantic_data.get("date_mode", "")
        time_constraint = semantic_data.get("time_constraint")
        time_refs = semantic_data.get("time_refs", [])
        date_roles = semantic_data.get("date_roles", [])
        
        # Process date_refs if found
        if date_refs and "date" not in luma_slots and "start_date" not in luma_slots:
            if date_mode == "single_day" or not date_mode:
                if isinstance(date_refs, list) and len(date_refs) > 0:
                    luma_slots["date"] = date_refs[0]
                    logger.debug(f"Extracted date from semantic.date_refs (root/found): {date_refs[0]}")
        
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
                        logger.debug(f"Extracted time from semantic.time_constraint.start: {constraint_start} (mode={constraint_mode})")
                    else:
                        # Fallback: use time_constraint dict as-is if no start
                        luma_slots["time"] = time_constraint
                        logger.debug(f"Extracted time from semantic.time_constraint (dict): {time_constraint}")
                else:
                    # time_constraint is a string, use directly
                    luma_slots["time"] = time_constraint
                    logger.debug(f"Extracted time from semantic.time_constraint: {time_constraint}")
            elif time_refs and isinstance(time_refs, list) and len(time_refs) > 0:
                luma_slots["time"] = time_refs[0]
                logger.debug(f"Extracted time from semantic.time_refs: {time_refs[0]}")
    
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
                    if isinstance(date_refs, list) and len(date_refs) > 1:
                        luma_slots["end_date"] = date_refs[-1]
                    elif isinstance(date_refs, list) and len(date_refs) == 1 and "start_date" in luma_slots:
                        # Single date for range - use same date for end
                        luma_slots["end_date"] = date_refs[0]
            
            # single_day → slots["date"] (for service appointments)
            if date_mode == "single_day" and "date" not in luma_slots and "start_date" not in luma_slots:
                if isinstance(date_refs, list) and len(date_refs) > 0:
                    luma_slots["date"] = date_refs[0]
            # range → slots["date_range"] or start_date/end_date
            elif date_mode == "range":
                if "date_range" not in luma_slots and "start_date" not in luma_slots:
                    if isinstance(date_refs, list):
                        if len(date_refs) >= 2:
                            luma_slots["start_date"] = date_refs[0]
                            luma_slots["end_date"] = date_refs[-1]
                        elif len(date_refs) == 1:
                            # Single date in range mode - use for start_date
                            luma_slots["start_date"] = date_refs[0]
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
                        logger.debug(f"Extracted time from semantic.time_constraint.start (projection): {constraint_start} (mode={constraint_mode})")
                    else:
                        # Fallback: use time_constraint dict as-is if no start
                        luma_slots["time"] = time_constraint
                        logger.debug(f"Extracted time from semantic.time_constraint (dict, projection): {time_constraint}")
                else:
                    # time_constraint is a string, use directly
                    luma_slots["time"] = time_constraint
                    logger.debug(f"Extracted time from semantic.time_constraint (projection): {time_constraint}")
            elif time_refs and isinstance(time_refs, list) and len(time_refs) > 0:
                luma_slots["time"] = time_refs[0]
                logger.debug(f"Extracted time from semantic.time_refs (projection): {time_refs[0]}")
    
    # Additional fallback: Check if Luma provided date/time directly in merged response
    # (Sometimes Luma provides date in slots even without semantic data)
    if "date" not in luma_slots:
        # Check if date exists in merged response slots (Luma might have added it)
        direct_date = merged.get("slots", {}).get("date")
        if direct_date:
            luma_slots["date"] = direct_date
            logger.debug(f"Extracted date from merged.slots.date: {direct_date}")
    
    if "time" not in luma_slots:
        # Check if time exists in merged response slots
        direct_time = merged.get("slots", {}).get("time")
        if direct_time:
            luma_slots["time"] = direct_time
            logger.debug(f"Extracted time from merged.slots.time: {direct_time}")
    
    # Check booking object for date/time (Luma might provide in booking.datetime_range)
    booking_obj = merged.get("booking")
    if isinstance(booking_obj, dict) and "date" not in luma_slots:
        booking_date = booking_obj.get("date") or (booking_obj.get("datetime_range", {}).get("start") if isinstance(booking_obj.get("datetime_range"), dict) else None)
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
    # 1. All session slots are preserved (non-destructive)
    # 2. New Luma slots are added
    # 3. Existing slots can be updated with new values from Luma
    session_slots = session_state.get("slots", {})
    if not isinstance(session_slots, dict):
        session_slots = {}
    
    # Start with session slots (preserve all previously resolved slots)
    merged_slots = session_slots.copy()
    
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
                logger.debug(f"Normalized time slot from dict to start value: {time_start}")
            else:
                # Fallback: use dict as-is if no start
                merged_slots[key] = value
        elif value is not None:  # Only merge non-None values
            merged_slots[key] = value
    
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
    
    # Special handling for reservations: map "date" to "start_date" when appropriate
    # If intent is CREATE_RESERVATION and we have "date" but not "start_date", and we're missing start_date/end_date
    merged_intent_name = merged.get("intent", {}).get("name", "") if isinstance(merged.get("intent"), dict) else ""
    if merged_intent_name == "CREATE_RESERVATION":
        if "date" in merged_slots and "start_date" not in merged_slots:
            # For reservations, a single date should map to start_date
            merged_slots["start_date"] = merged_slots["date"]
            # Remove "date" since we've mapped it to start_date for reservations
            # But keep it if it's also needed for other purposes
            logger.debug(f"Mapped date to start_date for reservation: {merged_slots['start_date']}")
    
    # Update merged response with merged slots
    # CRITICAL: Ensure all session slots are preserved (non-destructive merge)
    merged["slots"] = merged_slots
    
    # Assertion: All session slots must be preserved in merged slots
    if session_slots:
        missing_session_slots = set(session_slots.keys()) - set(merged_slots.keys())
        if missing_session_slots:
            logger.error(
                f"CRITICAL: Session slots were lost during merge! "
                f"Missing: {list(missing_session_slots)}, "
                f"session_slots={list(session_slots.keys())}, "
                f"merged_slots={list(merged_slots.keys())}"
            )
            # Restore missing session slots (fail-safe)
            for key in missing_session_slots:
                merged_slots[key] = session_slots[key]
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
    
    # STEP 4: Update missing_slots after merge (must shrink on follow-up turns)
    session_missing = session_state.get("missing_slots", [])
    if not isinstance(session_missing, list):
        session_missing = []
    
    # Get slots that were filled in current request (present in merged_slots but not in session_slots)
    filled_slots = set(merged_slots.keys()) - set(session_slots.keys())
    
    # Map filled slots to missing slot names (e.g., start_date/end_date satisfy date)
    # For reservations, "date" can satisfy "start_date" if intent is CREATE_RESERVATION
    merged_intent_name_for_satisfaction = merged.get("intent", {}).get("name", "") if isinstance(merged.get("intent"), dict) else ""
    slot_satisfaction_map = {
        "date": ["date"] + (["start_date"] if merged_intent_name_for_satisfaction == "CREATE_RESERVATION" else []),
        "start_date": ["date", "start_date"],
        "end_date": ["end_date"],
        "time": ["time"],
        "date_range": ["date", "date_range", "start_date", "end_date"]
    }
    
    # Determine which missing slots are satisfied
    satisfied_missing = set()
    for filled_slot in filled_slots:
        if filled_slot in slot_satisfaction_map:
            satisfied_missing.update(slot_satisfaction_map[filled_slot])
        else:
            satisfied_missing.add(filled_slot)
    
    # Remove satisfied slots from session missing_slots (must shrink on follow-up turns)
    new_missing = [slot for slot in session_missing if slot not in satisfied_missing]
    
    # Add any missing slots from current Luma response (if any)
    # BUT only if the slot isn't already satisfied in merged_slots
    # This prevents Luma from re-adding missing slots that were filled via semantic extraction
    current_missing = merged.get("missing_slots", [])
    if isinstance(current_missing, list):
        for slot in current_missing:
            # Check if this slot is already satisfied in merged_slots
            slot_satisfied = False
            
            # Direct slot match
            if slot in merged_slots:
                slot_satisfied = True
            # Date slot satisfaction mapping
            elif slot == "date":
                # date is satisfied if date, start_date, or date_range exists
                if "date" in merged_slots or "start_date" in merged_slots or "date_range" in merged_slots:
                    slot_satisfied = True
            elif slot == "start_date":
                # start_date is satisfied if start_date exists, OR if date exists and intent is CREATE_RESERVATION
                if "start_date" in merged_slots:
                    slot_satisfied = True
                elif "date" in merged_slots and merged_intent_name_for_satisfaction == "CREATE_RESERVATION":
                    slot_satisfied = True
            elif slot == "end_date" and "end_date" in merged_slots:
                slot_satisfied = True
            elif slot == "time" and "time" in merged_slots:
                slot_satisfied = True
            
            # Only add if not satisfied and not already in new_missing
            if not slot_satisfied and slot not in new_missing:
                new_missing.append(slot)
    
    # Normalize MODIFY_BOOKING missing_slots after merge (test contract)
    # Import here to avoid circular dependency
    from core.orchestration.luma_response_processor import _normalize_modify_booking_missing_slots
    new_missing = _normalize_modify_booking_missing_slots(new_missing, merged)
    
    merged["missing_slots"] = new_missing
    
    # Assertion: session.intent determines planner path exclusively
    # Verify that merged intent matches session intent (when session exists and not reset)
    merged_intent = merged.get("intent", {})
    merged_intent_name = merged_intent.get("name", "") if isinstance(merged_intent, dict) else ""
    if session_intent and session_status != "READY":
        session_intent_str = session_intent if isinstance(session_intent, str) else session_intent.get("name", "")
        assert merged_intent_name == session_intent_str, (
            f"Session intent mismatch: session.intent={session_intent_str}, "
            f"merged.intent={merged_intent_name}. Session intent must determine planner path exclusively."
        )
    
    return merged


# Backward compatibility alias
merge_session_with_luma_response = merge_luma_with_session


def build_session_state_from_outcome(
    outcome: Dict[str, Any],
    outcome_status: str,
    merged_luma_response: Optional[Dict[str, Any]] = None
) -> Optional[Dict[str, Any]]:
    """
    Build session state from outcome and merged Luma response.
    
    Args:
        outcome: Outcome dictionary from handle_message
        outcome_status: Outcome status ("READY" | "NEEDS_CLARIFICATION" | "AWAITING_CONFIRMATION")
        merged_luma_response: Optional merged Luma response (for extracting intent)
        
    Returns:
        Session state dictionary or None if status is READY
    """
    # Don't save session for READY or EXECUTED status - session should be cleared
    if outcome_status in ("READY", "EXECUTED"):
        return None
    
    # Extract facts (contains slots, missing_slots)
    facts = outcome.get("facts", {})
    if not isinstance(facts, dict):
        facts = {}
    
    # Extract slots from facts
    slots = facts.get("slots", {})
    if not isinstance(slots, dict):
        slots = {}
    
    # Extract missing_slots from facts or data.missing
    missing_slots = facts.get("missing_slots", [])
    if not isinstance(missing_slots, list):
        missing_slots = []
    
    # Also check data.missing (clarification outcomes)
    data = outcome.get("data", {})
    if isinstance(data, dict) and "missing" in data:
        data_missing = data.get("missing", [])
        if isinstance(data_missing, list):
            for slot in data_missing:
                if slot not in missing_slots:
                    missing_slots.append(slot)
    
    # Extract intent - prefer from merged Luma response, fallback to outcome
    intent_name = ""
    
    # Try merged Luma response first (most reliable)
    if merged_luma_response:
        intent_obj = merged_luma_response.get("intent", {})
        if isinstance(intent_obj, dict):
            intent_name = intent_obj.get("name", "")
        elif isinstance(intent_obj, str):
            intent_name = intent_obj
    
    # Fallback to outcome intent_name (for non-core intents)
    if not intent_name and "intent_name" in outcome:
        intent_name = outcome.get("intent_name", "")
    
    # Determine status
    status = "NEEDS_CLARIFICATION" if outcome_status in ("NEEDS_CLARIFICATION", "AWAITING_CONFIRMATION") else "READY"
    
    return {
        "intent": intent_name,
        "slots": slots,
        "missing_slots": missing_slots,
        "status": status
    }
