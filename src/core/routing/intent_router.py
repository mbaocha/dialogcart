"""
Intent Router

Maps intent names to action names.

This is a pure routing function with no side effects, no execution,
and no rendering logic. It only performs semantic signal → identifier mapping.
"""

from typing import Dict, Optional

# Intent name → action name mapping
INTENT_ACTIONS: Dict[str, str] = {
    "CREATE_BOOKING": "booking.create",
    "CREATE_APPOINTMENT": "booking.create",
    "CREATE_RESERVATION": "booking.create",
    "MODIFY_BOOKING": "booking.modify",
    "CANCEL_BOOKING": "booking.cancel",
    "BOOKING_INQUIRY": "booking.inquiry",
}


def get_action_name(intent_name: str) -> Optional[str]:
    """
    Get action name for intent.

    Maps an intent name (semantic signal) to an action name (identifier).
    This is a pure routing function with no side effects.

    Args:
        intent_name: Intent name string (e.g., "CREATE_BOOKING")

    Returns:
        Action name string (e.g., "booking.create") or None if unsupported
    """
    return INTENT_ACTIONS.get(intent_name)

