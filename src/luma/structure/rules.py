"""
Pure rule functions for structural interpretation.

These functions analyze psentence patterns and entity positions
to determine structural relationships. No entity extraction or modification.
"""
import re
from typing import Dict, List, Any


# Booking verbs that indicate a booking action
BOOKING_VERBS = {
    "book", "schedule", "reserve", "reservation", "appointment",
    "appoint", "set", "arrange", "plan"
}

# Sentence separators that indicate multiple bookings
SENTENCE_SEPARATORS = {
    "and", "then", "next", "after", "also", "plus", "or"
}

# Conjunctions that join entities without resetting scope
CONJUNCTIONS = {"and", "or", "plus", "&"}

# Range markers for time ranges
TIME_RANGE_MARKERS = {
    "between", "from", "to", "until", "till"
}


def tokenize_psentence(psentence: str) -> List[str]:
    """
    Tokenize parameterized sentence into list of tokens.
    
    Args:
        psentence: Parameterized sentence string
        
    Returns:
        List of lowercase tokens
    """
    return psentence.lower().split()


def count_bookings(psentence: str) -> int:
    """
    Rule 1: Count number of bookings.
    
    If there is a single booking verb and no sentence split → 1
    If multiple booking verbs or sentence separators → >1
    
    Args:
        psentence: Parameterized sentence
        
    Returns:
        Booking count (default: 1)
    """
    tokens = tokenize_psentence(psentence)
    
    # Count booking verbs
    booking_verb_count = sum(1 for token in tokens if token in BOOKING_VERBS)
    
    # Check for sentence separators (after first booking verb)
    has_separator = False
    found_booking = False
    for token in tokens:
        if token in BOOKING_VERBS:
            found_booking = True
        elif found_booking and token in SENTENCE_SEPARATORS:
            has_separator = True
            break
    
    # Multiple bookings if:
    # - Multiple booking verbs, OR
    # - Booking verb followed by separator
    if booking_verb_count > 1 or (booking_verb_count == 1 and has_separator):
        return max(2, booking_verb_count)
    
    return 1


def determine_service_scope(psentence: str, entities: Dict[str, List]) -> str:
    """
    Rule 2: Determine service scope.
    
    If multiple servicefamilytokens joined by conjunctions
    without verb reset → shared
    
    Else → separate
    
    Args:
        psentence: Parameterized sentence
        entities: Entity dictionary (for counting services)
        
    Returns:
        "shared" or "separate"
    """
    service_count = len(entities.get("service_families", []))
    
    if service_count <= 1:
        return "separate"
    
    tokens = tokenize_psentence(psentence)
    
    # Find all servicefamilytoken positions
    service_positions = []
    for i, token in enumerate(tokens):
        if token == "servicefamilytoken":
            service_positions.append(i)
    
    if len(service_positions) < 2:
        return "separate"
    
    # Check if services are joined by conjunctions without verbs between them
    for i in range(len(service_positions) - 1):
        start = service_positions[i]
        end = service_positions[i + 1]
        
        # Extract tokens between two service tokens
        between_tokens = tokens[start + 1:end]
        
        # Check if there's a booking verb between services
        has_verb_between = any(token in BOOKING_VERBS for token in between_tokens)
        
        # Check if joined by conjunction
        has_conjunction = any(token in CONJUNCTIONS for token in between_tokens)
        
        # If verb between services → separate
        if has_verb_between:
            return "separate"
        
        # If no conjunction → separate
        if not has_conjunction:
            return "separate"
    
    # All services joined by conjunctions without verbs → shared
    return "shared"


def determine_time_type(psentence: str, entities: Dict[str, List]) -> str:
    """
    Rule 3: Determine time type.
    
    If pattern "between timetoken and timetoken"
    or "from timetoken to timetoken" → range
    
    Else if timetoken exists → exact
    
    Else if timewindowtoken exists → window
    
    Else → none
    
    Args:
        psentence: Parameterized sentence
        entities: Entity dictionary
        
    Returns:
        "range", "exact", "window", or "none"
    """
    tokens = tokenize_psentence(psentence)
    psentence_lower = psentence.lower()
    
    # Check for range patterns
    # Pattern: "between timetoken and timetoken" or "from timetoken to timetoken"
    range_patterns = [
        r"between\s+timetoken\s+and\s+timetoken",
        r"from\s+timetoken\s+to\s+timetoken",
        r"timetoken\s+to\s+timetoken",
        r"timetoken\s+until\s+timetoken",
        r"timetoken\s+till\s+timetoken"
    ]
    
    for pattern in range_patterns:
        if re.search(pattern, psentence_lower):
            return "range"
    
    # Check for exact time tokens
    time_count = len(entities.get("times", []))
    if time_count > 0:
        return "exact"
    
    # Check for time window tokens
    time_window_count = len(entities.get("time_windows", []))
    if time_window_count > 0:
        return "window"
    
    return "none"


def determine_time_scope(psentence: str, entities: Dict[str, List]) -> str:
    """
    Rule 4: Determine time scope.
    
    If time tokens appear after all servicefamilytokens → shared
    
    If time tokens are interleaved with services → per_service
    
    Args:
        psentence: Parameterized sentence
        entities: Entity dictionary
        
    Returns:
        "shared" or "per_service"
    """
    tokens = tokenize_psentence(psentence)
    
    # Find positions of service and time tokens
    service_positions = []
    time_positions = []
    time_window_positions = []
    
    for i, token in enumerate(tokens):
        if token == "servicefamilytoken":
            service_positions.append(i)
        elif token == "timetoken":
            time_positions.append(i)
        elif token == "timewindowtoken":
            time_window_positions.append(i)
    
    all_time_positions = sorted(time_positions + time_window_positions)
    
    if not all_time_positions:
        return "shared"  # Default if no time tokens
    
    if not service_positions:
        return "shared"  # Default if no service tokens
    
    # Check if all time tokens come after all service tokens
    last_service_pos = max(service_positions)
    first_time_pos = min(all_time_positions)
    
    if first_time_pos > last_service_pos:
        return "shared"
    
    # Check if time tokens are interleaved with services
    # (time token appears between two service tokens)
    for i in range(len(service_positions) - 1):
        start = service_positions[i]
        end = service_positions[i + 1]
        
        # Check if any time token is between these two services
        if any(start < time_pos < end for time_pos in all_time_positions):
            return "per_service"
    
    # If time appears before first service, consider it per_service
    if all_time_positions and all_time_positions[0] < service_positions[0]:
        return "per_service"
    
    return "shared"


def check_has_duration(entities: Dict[str, List]) -> bool:
    """
    Rule 6: Check if duration exists.
    
    Args:
        entities: Entity dictionary
        
    Returns:
        True if any durationtoken exists
    """
    duration_count = len(entities.get("durations", []))
    return duration_count > 0


def check_needs_clarification(
    psentence: str,
    entities: Dict[str, List]
) -> bool:
    """
    Rule 7: Check if clarification is needed.
    
    True if conflicting signals exist:
    - Multiple dates with no range marker
    - Multiple times with no range marker (unless time_type is range)
    - Conflicting scopes
    
    Args:
        psentence: Parameterized sentence
        entities: Entity dictionary
        
    Returns:
        True if clarification needed, False otherwise
    """
    tokens = tokenize_psentence(psentence)
    psentence_lower = psentence.lower()
    
    # Check for multiple dates without range markers
    date_count = len(entities.get("dates", []))
    date_abs_count = len(entities.get("dates_absolute", []))
    total_dates = date_count + date_abs_count
    
    if total_dates > 1:
        # Check for date range markers (whole-word matches only)
        date_range_markers = {"from", "to", "until", "till", "between"}
        tokens_set = set(tokens)
        has_date_range_marker = any(
            marker in tokens_set for marker in date_range_markers
        )
        if not has_date_range_marker:
            return True
    
    # Check for multiple times without range markers
    time_count = len(entities.get("times", []))
    if time_count > 1:
        # Check if time_type is range (already handled)
        time_type = determine_time_type(psentence, entities)
        if time_type != "range":
            return True
    
    # Check for conflicting time types (both exact and window)
    time_count = len(entities.get("times", []))
    time_window_count = len(entities.get("time_windows", []))
    if time_count > 0 and time_window_count > 0:
        # This is actually valid (e.g., "tomorrow morning at 9am")
        # But if they're not clearly related, might need clarification
        # For now, allow it (not a conflict)
        pass
    
    return False

