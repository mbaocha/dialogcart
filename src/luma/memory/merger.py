"""
Memory Merger

Implements merge logic for combining new input with existing memory state.
"""

from typing import Dict, Any, Optional
from datetime import datetime, timezone

# Try zoneinfo first (Python 3.9+), fallback to pytz
try:
    from zoneinfo import ZoneInfo
    try:
        # Test if tzdata is available
        _ = ZoneInfo("UTC")
        ZONEINFO_AVAILABLE = True
    except Exception:
        ZONEINFO_AVAILABLE = False
except ImportError:
    ZONEINFO_AVAILABLE = False

try:
    import pytz
    PYTZ_AVAILABLE = True
except ImportError:
    PYTZ_AVAILABLE = False


def merge_booking_state(
    memory_state: Optional[Dict[str, Any]],
    current_intent: str,
    current_booking: Dict[str, Any],
    current_clarification: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Merge current input with memory state.
    
    Args:
        memory_state: Existing memory state (None if no memory)
        current_intent: Intent from current input (real intent: CREATE_APPOINTMENT or CREATE_RESERVATION)
        current_booking: Booking state from current input (from calendar binding)
        current_clarification: Clarification from current input (if any)
        
    Returns:
        Merged memory state dict with real intent stored
    """
    # If intent changes, clear memory
    if memory_state and memory_state.get("intent") != current_intent:
        memory_state = None
    
    # Start with empty state if no memory
    if memory_state is None:
        merged_state = {
            "intent": current_intent,
            "booking_state": {
                "services": current_booking.get("services", []),
                "datetime_range": current_booking.get("datetime_range"),
                "duration": current_booking.get("duration")
            },
            "clarification": current_clarification,
            "last_updated": datetime.now(timezone.utc).isoformat()
        }
    else:
        # Merge onto existing memory
        merged_booking = _merge_booking_slots(
            memory_state.get("booking_state", {}),
            current_booking
        )
        
        # Handle clarification
        merged_clarification = _merge_clarification(
            memory_state.get("clarification"),
            current_clarification
        )
        
        merged_state = {
            "intent": current_intent,
            "booking_state": merged_booking,
            "clarification": merged_clarification,
            "last_updated": datetime.now(timezone.utc).isoformat()
        }
    
    return merged_state


def _merge_booking_slots(
    memory_booking: Dict[str, Any],
    current_booking: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Merge booking slots according to rules:
    
    SERVICES:
    - If services mentioned in input → REPLACE
    - Else → KEEP from memory
    
    DATETIME:
    - If input resolves datetime_range → REPLACE
    - If input partially resolves (date or time):
        - If memory has datetime_range → rebuild it using memory date + new time
        - Else → require clarification (handled upstream)
    - If input mentions no temporal info → KEEP
    
    DURATION:
    - If mentioned → REPLACE
    - Else → KEEP
    """
    merged = {}
    
    # SERVICES: Replace if mentioned, else keep from memory
    current_services = current_booking.get("services", [])
    if current_services:
        merged["services"] = current_services
    else:
        merged["services"] = memory_booking.get("services", [])
    
    # DATETIME: Replace if fully resolved, else merge partial updates
    current_datetime_range = current_booking.get("datetime_range")
    memory_datetime_range = memory_booking.get("datetime_range")
    current_date_range = current_booking.get("date_range")
    current_time_range = current_booking.get("time_range")
    
    if current_datetime_range is not None:
        # Input fully resolved datetime → REPLACE
        merged["datetime_range"] = current_datetime_range
    elif memory_datetime_range is not None:
        # Memory has datetime_range - check if we can rebuild with new time
        if current_time_range is not None and current_date_range is None:
            # Time-only update: rebuild datetime_range using memory date + new time
            rebuilt = _rebuild_datetime_range_from_time(
                memory_datetime_range, current_time_range
            )
            if rebuilt:
                merged["datetime_range"] = rebuilt
            else:
                # Rebuild failed, keep memory datetime_range
                merged["datetime_range"] = memory_datetime_range
        else:
            # Input has no datetime/time, memory has one → KEEP
            merged["datetime_range"] = memory_datetime_range
    else:
        # Neither has datetime → None
        merged["datetime_range"] = None
    
    # DURATION: Replace if mentioned, else keep from memory
    current_duration = current_booking.get("duration")
    if current_duration is not None:
        merged["duration"] = current_duration
    else:
        merged["duration"] = memory_booking.get("duration")
    
    return merged


def _rebuild_datetime_range_from_time(
    memory_datetime_range: Dict[str, Any],
    current_time_range: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """
    Rebuild datetime_range by combining memory date with new time.
    
    Args:
        memory_datetime_range: Existing datetime_range from memory (has date)
        current_time_range: New time_range from current input (time only)
        
    Returns:
        New datetime_range dict with memory date + new time, or None if rebuild fails
    """
    try:
        # Extract date from memory datetime_range
        memory_start = memory_datetime_range.get("start")
        if not memory_start:
            return None
        
        # Parse memory start datetime to get date
        memory_dt = datetime.fromisoformat(memory_start.replace("Z", "+00:00"))
        memory_date = memory_dt.date()
        
        # Extract timezone from memory datetime
        if memory_dt.tzinfo:
            tz = memory_dt.tzinfo
        else:
            # Fallback to UTC if no timezone
            if ZONEINFO_AVAILABLE:
                tz = ZoneInfo("UTC")
            elif PYTZ_AVAILABLE:
                tz = pytz.UTC
            else:
                # Last resort: timezone-aware UTC
                tz = timezone.utc
        
        # Get time from current_time_range
        start_time = current_time_range.get("start_time")
        end_time = current_time_range.get("end_time")
        
        if not start_time:
            return None
        
        # Parse time (HH:MM format)
        start_parts = start_time.split(":")
        start_hour = int(start_parts[0])
        start_minute = int(start_parts[1]) if len(start_parts) > 1 else 0
        
        # Create start datetime
        start_dt = datetime.combine(memory_date, datetime.min.time().replace(
            hour=start_hour, minute=start_minute
        ))
        start_dt = start_dt.replace(tzinfo=tz)
        
        # Determine if this is an exact time or a window
        # If start_time == end_time, it's an exact time
        # Exact times MUST override any existing time window (set start == end)
        is_exact_time = (not end_time or end_time == start_time)
        
        if is_exact_time:
            # Exact time: start == end (overrides any window from memory)
            end_dt = start_dt
        else:
            # Time window: use end_time
            end_parts = end_time.split(":")
            end_hour = int(end_parts[0])
            end_minute = int(end_parts[1]) if len(end_parts) > 1 else 0
            end_dt = datetime.combine(memory_date, datetime.min.time().replace(
                hour=end_hour, minute=end_minute
            ))
            end_dt = end_dt.replace(tzinfo=tz)
        
        return {
            "start": start_dt.isoformat(),
            "end": end_dt.isoformat()
        }
    except Exception:  # noqa: BLE001
        # If rebuild fails, return None to fall back to keeping memory datetime_range
        return None


def _merge_clarification(
    memory_clarification: Optional[Dict[str, Any]],
    current_clarification: Optional[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """
    Merge clarification state.
    
    Rules:
    - If new clarification detected → REPLACE old
    - If existing clarification is resolved by input → CLEAR
    - Never store more than one clarification
    """
    # If current input has clarification, use it (replaces old)
    if current_clarification is not None:
        return current_clarification
    
    # If current input has no clarification but memory has one,
    # keep it (user hasn't resolved it yet)
    return memory_clarification


def extract_memory_state_for_response(
    memory_state: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Extract production-ready booking state from memory.
    
    Returns only the fields needed for the response:
    - services
    - datetime_range
    - duration
    """
    booking_state = memory_state.get("booking_state", {})
    return {
        "services": booking_state.get("services", []),
        "datetime_range": booking_state.get("datetime_range"),
        "duration": booking_state.get("duration")
    }

