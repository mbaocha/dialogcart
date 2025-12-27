"""
Routing Logic

Maps clarification reasons to template keys and intents to action names.
"""

from typing import Dict, Optional


# Clarification reason → template key mapping
CLARIFICATION_TEMPLATES: Dict[str, str] = {
    "MISSING_TIME": "{domain}.ask_time",
    "MISSING_DATE": "{domain}.ask_date",
    "MISSING_DATE_TIME": "{domain}.ask_date_time",
    "MISSING_SERVICE": "{domain}.ask_service",
    "AMBIGUOUS_TIME_NO_WINDOW": "{domain}.clarify",
    "AMBIGUOUS_DATE_MULTIPLE": "{domain}.clarify",
    "LOCALE_AMBIGUOUS_DATE": "{domain}.clarify",
    "VAGUE_DATE_REFERENCE": "{domain}.clarify",
    "AMBIGUOUS_PLURAL_WEEKDAY": "{domain}.clarify",
    "AMBIGUOUS_WEEKDAY_REFERENCE": "{domain}.clarify",
    "CONFLICTING_SIGNALS": "{domain}.clarify",
    "CONTEXT_DEPENDENT_DATE": "{domain}.clarify",
    "CONTEXT_DEPENDENT_VALUE": "{domain}.clarify",
    "MISSING_BOOKING_REFERENCE": "{domain}.clarify",
    "MISSING_DATE_FOR_TIME_CONSTRAINT": "{domain}.clarify",
    "MISSING_CONTEXT": "{domain}.clarify",
}

# Intent name → action name mapping
INTENT_ACTIONS: Dict[str, str] = {
    "CREATE_BOOKING": "booking.create",
    "MODIFY_BOOKING": "booking.modify",
    "CANCEL_BOOKING": "booking.cancel",
    "BOOKING_INQUIRY": "booking.inquiry",
}


def get_template_key(reason: str, domain: str = "service") -> str:
    """
    Get template key for clarification reason.
    
    Args:
        reason: Clarification reason string
        domain: Domain (default: "service")
        
    Returns:
        Template key string (e.g., "service.ask_time")
    """
    template = CLARIFICATION_TEMPLATES.get(reason, "{domain}.clarify")
    return template.format(domain=domain)


def get_action_name(intent_name: str) -> Optional[str]:
    """
    Get action name for intent.
    
    Args:
        intent_name: Intent name string
        
    Returns:
        Action name string or None if unsupported
    """
    return INTENT_ACTIONS.get(intent_name)

