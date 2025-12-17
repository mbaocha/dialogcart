"""
Structural interpreter orchestrator.

Coordinates rule execution to produce StructureResult.
"""
from typing import Dict, Any

from luma.structure.structure_types import StructureResult
from luma.structure.rules import (
    count_bookings,
    determine_service_scope,
    determine_time_type,
    determine_time_scope,
    check_has_duration,
    check_needs_clarification
)


def interpret_structure(
    psentence: str,
    entities: Dict[str, Any]
) -> StructureResult:
    """
    Interpret structural relationships from parameterized sentence and entities.
    
    This function determines HOW entities relate to each other,
    not WHAT the entities are. It does NOT extract or modify entities.
    
    Args:
        psentence: Parameterized sentence (e.g., "book servicefamilytoken datetoken")
        entities: Raw extraction output from EntityMatcher.extract_with_parameterization()
                 Must contain keys: service_families, dates, dates_absolute, times,
                 time_windows, durations
                 
    Returns:
        StructureResult with structural interpretation
        
    Example:
        >>> psentence = "book servicefamilytoken and servicefamilytoken datetoken timetoken"
        >>> entities = {
        ...     "service_families": [{"text": "haircut"}, {"text": "beard trim"}],
        ...     "dates": [{"text": "tomorrow"}],
        ...     "times": [{"text": "9am"}]
        ... }
        >>> result = interpret_structure(psentence, entities)
        >>> assert result.booking_count == 1
        >>> assert result.service_scope == "shared"
        >>> assert result.time_type == "exact"
    """
    # Rule 1: Booking count
    booking_count = count_bookings(psentence)
    
    # Rule 2: Service scope
    service_scope = determine_service_scope(psentence, entities)
    
    # Rule 3: Time type
    time_type = determine_time_type(psentence, entities)
    
    # Rule 4: Time scope
    time_scope = determine_time_scope(psentence, entities)
    
    # Rule 5: Date scope (default to shared)
    date_scope = "shared"
    
    # Rule 6: Has duration
    has_duration = check_has_duration(entities)
    
    # Rule 7: Needs clarification
    needs_clarification = check_needs_clarification(psentence, entities)
    
    return StructureResult(
        booking_count=booking_count,
        service_scope=service_scope,
        time_scope=time_scope,
        date_scope=date_scope,
        time_type=time_type,
        has_duration=has_duration,
        needs_clarification=needs_clarification
    )

