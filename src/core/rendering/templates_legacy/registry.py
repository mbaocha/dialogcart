"""
Template Registry

Maps clarification reasons to templates.
"""

from typing import Dict, Optional


# Template mapping by clarification reason
CLARIFICATION_TEMPLATES: Dict[str, str] = {
    "MISSING_TIME": "templates.hotel.missing_time",
    "MISSING_DATE": "templates.hotel.missing_date",
    "MISSING_SERVICE": "templates.hotel.missing_service",
    "AMBIGUOUS_TIME_NO_WINDOW": "templates.hotel.ambiguous_time",
    "AMBIGUOUS_DATE_MULTIPLE": "templates.hotel.ambiguous_date",
    "LOCALE_AMBIGUOUS_DATE": "templates.hotel.locale_ambiguous_date",
    "VAGUE_DATE_REFERENCE": "templates.hotel.vague_date",
    "AMBIGUOUS_PLURAL_WEEKDAY": "templates.hotel.ambiguous_weekday",
    "AMBIGUOUS_WEEKDAY_REFERENCE": "templates.hotel.ambiguous_weekday",
    "CONFLICTING_SIGNALS": "templates.hotel.conflicting_signals",
    "CONTEXT_DEPENDENT_DATE": "templates.hotel.context_dependent",
    "CONTEXT_DEPENDENT_VALUE": "templates.hotel.context_dependent",
    "MISSING_BOOKING_REFERENCE": "templates.hotel.missing_reference",
    "MISSING_DATE_FOR_TIME_CONSTRAINT": "templates.hotel.missing_date",
    "MISSING_CONTEXT": "templates.hotel.missing_context",
}


def get_template_for_reason(reason: str) -> Optional[str]:
    """
    Get template module path for clarification reason.
    
    Args:
        reason: Clarification reason string
        
    Returns:
        Template module path or None if not found
    """
    return CLARIFICATION_TEMPLATES.get(reason)

