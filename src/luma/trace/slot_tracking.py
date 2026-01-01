"""
Slot Tracking Utility

Provides structured logging for tracking slot/data transformations through the pipeline.
Helps identify where slots are lost or modified during processing.

This module is controlled by the LOG_SLOT_TRACKING config flag to minimize overhead
when not needed for debugging.
"""
import logging
from typing import Dict, Any, Optional, Set, List

logger = logging.getLogger(__name__)


def extract_slot_keys(data: Any, prefix: str = "") -> Set[str]:
    """
    Extract slot-like keys from a data structure (dict, list, or nested structures).
    
    Args:
        data: Data structure to extract keys from
        prefix: Optional prefix for nested keys
        
    Returns:
        Set of slot key paths (e.g., {"services", "datetime_range", "duration", "date_refs"})
    """
    slots = set()
    
    if isinstance(data, dict):
        for key, value in data.items():
            # Skip internal/private keys
            if key.startswith("_") or key in ("extra_data", "trace", "execution_trace"):
                continue
            
            full_key = f"{prefix}.{key}" if prefix else key
            slots.add(full_key)
            
            # Recursively extract from nested structures (limited depth)
            if isinstance(value, (dict, list)) and not prefix or len(prefix.split(".")) < 3:
                slots.update(extract_slot_keys(value, full_key))
    elif isinstance(data, list):
        # For lists, extract from first item if it's a dict
        if data and isinstance(data[0], dict):
            slots.update(extract_slot_keys(data[0], prefix))
    
    return slots


def compute_slot_diff(
    before: Dict[str, Any],
    after: Dict[str, Any],
    slot_keys: Optional[Set[str]] = None
) -> Dict[str, Any]:
    """
    Compute differences between two slot dictionaries.
    
    Args:
        before: Slot data before transformation
        after: Slot data after transformation
        slot_keys: Optional set of slot keys to track (if None, auto-detect)
        
    Returns:
        Dict with:
        - slots_added: Keys present in after but not in before
        - slots_removed: Keys present in before but not in after
        - slots_modified: Keys with different values
        - slots_unchanged: Keys with same values
    """
    if slot_keys is None:
        all_keys = extract_slot_keys(before) | extract_slot_keys(after)
        # Filter to top-level keys for main comparison
        slot_keys = {k.split(".")[0] for k in all_keys}
    
    before_keys = set(before.keys()) if isinstance(before, dict) else set()
    after_keys = set(after.keys()) if isinstance(after, dict) else set()
    
    slots_added = after_keys - before_keys
    slots_removed = before_keys - after_keys
    common_keys = before_keys & after_keys
    
    slots_modified = {}
    slots_unchanged = set()
    
    for key in common_keys:
        before_val = before.get(key)
        after_val = after.get(key)
        
        # Deep comparison for nested structures
        if _values_different(before_val, after_val):
            slots_modified[key] = {
                "before": _sanitize_for_log(before_val),
                "after": _sanitize_for_log(after_val)
            }
        else:
            slots_unchanged.add(key)
    
    return {
        "slots_added": list(slots_added),
        "slots_removed": list(slots_removed),
        "slots_modified": slots_modified,
        "slots_unchanged": list(slots_unchanged)
    }


def _values_different(a: Any, b: Any) -> bool:
    """Check if two values are different (handles nested structures)."""
    if a is None and b is None:
        return False
    if a is None or b is None:
        return True
    
    if isinstance(a, dict) and isinstance(b, dict):
        if set(a.keys()) != set(b.keys()):
            return True
        return any(_values_different(a.get(k), b.get(k)) for k in a.keys())
    
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return True
        return any(_values_different(ai, bi) for ai, bi in zip(a, b))
    
    return a != b


def _sanitize_for_log(value: Any, max_length: int = 200) -> Any:
    """Sanitize value for logging (truncate long strings, limit nested depth)."""
    if isinstance(value, str):
        if len(value) > max_length:
            return value[:max_length] + "..."
        return value
    
    if isinstance(value, dict):
        # Limit dict size for logging
        if len(value) > 20:
            return {k: _sanitize_for_log(v, max_length) for k, v in list(value.items())[:20]}
        return {k: _sanitize_for_log(v, max_length) for k, v in value.items()}
    
    if isinstance(value, list):
        # Limit list size for logging
        if len(value) > 20:
            return [_sanitize_for_log(v, max_length) for v in value[:20]]
        return [_sanitize_for_log(v, max_length) for v in value]
    
    return value


def log_slot_transformation(
    stage_name: str,
    input_data: Any,
    output_data: Any,
    request_id: Optional[str] = None,
    enabled: bool = True
) -> None:
    """
    Log slot transformation at a pipeline stage boundary.
    
    Args:
        stage_name: Name of the stage (e.g., "extraction", "semantic", "memory_merge")
        input_data: Input data structure
        output_data: Output data structure
        request_id: Optional request ID for correlation
        enabled: Whether slot tracking is enabled (checked by caller)
    """
    if not enabled:
        return
    
    try:
        # Extract slot-like data from input/output
        input_slots = _extract_slot_data(input_data)
        output_slots = _extract_slot_data(output_data)
        
        # Compute differences
        diff = compute_slot_diff(input_slots, output_slots)
        
        # Only log if there are actual changes
        if diff["slots_added"] or diff["slots_removed"] or diff["slots_modified"]:
            logger.debug(
                f"[slot_tracking] {stage_name} transformation",
                extra={
                    "request_id": request_id,
                    "stage": stage_name,
                    "slot_transformation": {
                        "input_slots": list(input_slots.keys()) if isinstance(input_slots, dict) else [],
                        "output_slots": list(output_slots.keys()) if isinstance(output_slots, dict) else [],
                        **diff
                    }
                }
            )
    except Exception as e:  # noqa: BLE001
        # Don't fail on logging errors
        logger.warning(f"Failed to log slot transformation for {stage_name}: {e}")


def log_field_removal(
    stage_name: str,
    field_name: str,
    field_value: Any,
    context: Optional[Dict[str, Any]] = None,
    request_id: Optional[str] = None,
    enabled: bool = True
) -> None:
    """
    Log when a field is removed from a data structure.
    
    Args:
        stage_name: Name of the stage/operation
        field_name: Name of the field being removed
        field_value: Value that was removed
        context: Optional context dict with additional info
        request_id: Optional request ID for correlation
        enabled: Whether slot tracking is enabled
    """
    if not enabled:
        return
    
    try:
        extra_data = {
            "request_id": request_id,
            "stage": stage_name,
            "field_removed": field_name,
            "removed_value": _sanitize_for_log(field_value)
        }
        
        if context:
            extra_data["context"] = context
        
        logger.debug(
            f"[slot_tracking] {stage_name} removed field: {field_name}",
            extra=extra_data
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Failed to log field removal for {stage_name}: {e}")


def create_slot_snapshot(data: Any, stage_name: str) -> Dict[str, Any]:
    """
    Create a snapshot of slot data for inclusion in execution_trace.
    
    Args:
        data: Data structure to snapshot
        stage_name: Name of the stage
        
    Returns:
        Dict with slot snapshot (empty if slot tracking disabled)
    """
    try:
        slots = _extract_slot_data(data)
        return {
            "stage": stage_name,
            "slots": _sanitize_for_log(slots, max_length=500)
        }
    except Exception as e:  # noqa: BLE001
        return {
            "stage": stage_name,
            "error": str(e)
        }


def _extract_slot_data(data: Any) -> Dict[str, Any]:
    """
    Extract slot-like data from various data structures.
    
    Handles:
    - Dict with booking/slot fields
    - SemanticResolutionResult objects
    - DecisionResult objects
    - CalendarBindingResult objects
    """
    if data is None:
        return {}
    
    # Handle SemanticResolutionResult
    if hasattr(data, "resolved_booking"):
        return data.resolved_booking if isinstance(data.resolved_booking, dict) else {}
    
    # Handle DecisionResult
    if hasattr(data, "status"):
        result = {"status": data.status}
        if hasattr(data, "reason") and data.reason:
            result["reason"] = data.reason
        if hasattr(data, "missing_slots") and data.missing_slots:
            result["missing_slots"] = data.missing_slots
        return result
    
    # Handle CalendarBindingResult
    if hasattr(data, "calendar_booking"):
        return data.calendar_booking if isinstance(data.calendar_booking, dict) else {}
    
    # Handle dict directly
    if isinstance(data, dict):
        # Return a filtered dict with slot-like keys
        slot_keys = {
            "services", "service_families", "business_categories",
            "dates", "dates_absolute", "date_refs", "date_range", "datetime_range",
            "times", "time_refs", "time_range", "time_constraint", "time_windows",
            "durations", "duration",
            "start_date", "end_date",
            "booking_state", "intent", "status", "missing_slots",
            "needs_clarification", "clarification"
        }
        return {k: v for k, v in data.items() if k in slot_keys or not k.startswith("_")}
    
    return {}


