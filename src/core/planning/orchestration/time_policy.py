"""
Time Policy

Handles temporal normalization and time constraint processing.
"""

import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


def normalize_temporal_slots(
    luma_response: Dict[str, Any],
    intent_name: str
) -> Dict[str, Any]:
    """
    Normalize temporal slots from Luma response.
    
    Extracts time from time_constraint and promotes to slots.time.
    This ensures time expressions like "noon", "morning" are available in slots.
    
    Args:
        luma_response: Luma API response (modified in place)
        intent_name: Intent name for context
        
    Returns:
        Modified luma_response with normalized temporal slots
    """
    slots_for_filtering = luma_response.get("slots", {})
    if not isinstance(slots_for_filtering, dict):
        slots_for_filtering = {}
        luma_response["slots"] = slots_for_filtering
    
    # Only normalize time if it's not already in slots
    if "time" not in slots_for_filtering or not slots_for_filtering.get("time"):
        time_value = None
        time_mode = None
        
        def _extract_time_from_constraint(time_constraint_obj, source_name: str):
            """Extract time value and mode from time_constraint object."""
            if not time_constraint_obj:
                return None, None
            
            # If time_constraint is a string, use directly
            if isinstance(time_constraint_obj, str):
                logger.debug(f"Extracting time from {source_name}: string value={time_constraint_obj}")
                return time_constraint_obj, None
            
            # If time_constraint is a dict, extract based on mode
            if isinstance(time_constraint_obj, dict):
                constraint_mode = time_constraint_obj.get("mode", "")
                constraint_start = time_constraint_obj.get("start")
                
                # For exact mode, use start (e.g., "12:00" for "noon")
                if constraint_mode == "exact" and constraint_start:
                    logger.debug(f"Extracting time from {source_name}: mode=exact, start={constraint_start}")
                    return constraint_start, "exact"
                
                # For other modes or if start exists, use start
                if constraint_start:
                    logger.debug(f"Extracting time from {source_name}: start={constraint_start}, mode={constraint_mode}")
                    return constraint_start, constraint_mode
                
                # Fallback: check for "value" field (some formats use "value" instead of "start")
                constraint_value = time_constraint_obj.get("value")
                if constraint_value:
                    logger.debug(f"Extracting time from {source_name}: value={constraint_value}, mode={constraint_mode}")
                    return constraint_value, constraint_mode
                
                # Fallback: check for direct time value
                if "time" in time_constraint_obj:
                    time_val = time_constraint_obj["time"]
                    logger.debug(f"Extracting time from {source_name}: time field={time_val}, mode={constraint_mode}")
                    return time_val, constraint_mode
            
            return None, None
        
        # Check context.time_constraint (most common for resolved expressions like "noon", "morning")
        context = luma_response.get("context", {})
        if isinstance(context, dict):
            time_constraint = context.get("time_constraint")
            if time_constraint:
                extracted_time, extracted_mode = _extract_time_from_constraint(time_constraint, "context.time_constraint")
                if extracted_time:
                    time_value = extracted_time
                    time_mode = extracted_mode or context.get("time_mode")
                    logger.debug(f"Normalized time from context.time_constraint to slots.time: {time_value} (mode={time_mode})")
        
        # Fallback: Check trace.semantic.time_constraint
        if not time_value:
            trace = luma_response.get("trace", {})
            if isinstance(trace, dict):
                semantic = trace.get("semantic", {})
                if isinstance(semantic, dict):
                    time_constraint = semantic.get("time_constraint")
                    if time_constraint:
                        extracted_time, extracted_mode = _extract_time_from_constraint(time_constraint, "trace.semantic.time_constraint")
                        if extracted_time:
                            time_value = extracted_time
                            time_mode = extracted_mode or semantic.get("time_mode")
                            logger.debug(f"Normalized time from trace.semantic.time_constraint to slots.time: {time_value} (mode={time_mode})")
        
        # Fallback: Check stages for semantic data
        if not time_value:
            stages = luma_response.get("stages", [])
            if isinstance(stages, list):
                for stage in stages:
                    if isinstance(stage, dict):
                        semantic = stage.get("semantic", {})
                        if isinstance(semantic, dict):
                            time_constraint = semantic.get("time_constraint")
                            if time_constraint:
                                extracted_time, extracted_mode = _extract_time_from_constraint(time_constraint, "stages[].semantic.time_constraint")
                                if extracted_time:
                                    time_value = extracted_time
                                    time_mode = extracted_mode or semantic.get("time_mode")
                                    logger.debug(f"Normalized time from stages[].semantic.time_constraint to slots.time: {time_value} (mode={time_mode})")
                                    break
        
        # Write normalized time to slots
        if time_value:
            slots_for_filtering["time"] = time_value
            # Update luma_response slots to include normalized time
            luma_response["slots"] = slots_for_filtering
            logger.info(f"Temporal slot normalization: promoted time={time_value} from context to slots before filtering (mode={time_mode})")
    
    return luma_response

