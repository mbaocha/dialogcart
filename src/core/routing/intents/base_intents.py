"""
Core-Owned Base Intents

Explicitly declares which intents are owned and orchestrated by core.
This establishes a stable boundary for the core state machine before
introducing workflow extensibility.

These intents represent the foundational booking operations that core
is responsible for orchestrating. All other intents are considered
non-core and should not be orchestrated by core (unless explicitly
extended in future workflow systems).

This is a declarative and defensive module only - it does not modify
behavior, only establishes boundaries.
"""

from typing import Set

# Core-owned base intents that core orchestrates
# These are the stable, foundational intents that core is responsible for
CORE_BASE_INTENTS: Set[str] = {
    "CREATE_APPOINTMENT",
    "CREATE_RESERVATION",
    "MODIFY_BOOKING",
    "CANCEL_BOOKING",
}


def is_core_intent(intent_name: str) -> bool:
    """
    Check if an intent is a core-owned base intent.
    
    Args:
        intent_name: Intent name to check
        
    Returns:
        True if the intent is a core-owned base intent, False otherwise
    """
    return intent_name in CORE_BASE_INTENTS


def validate_core_intent(intent_name: str) -> None:
    """
    Validate that an intent is a core-owned base intent.
    
    Raises ValueError if the intent is not a core intent.
    This is a defensive check to enforce the orchestration boundary.
    
    Args:
        intent_name: Intent name to validate
        
    Raises:
        ValueError: If the intent is not a core-owned base intent
    """
    if not is_core_intent(intent_name):
        raise ValueError(
            f"Intent '{intent_name}' is not a core-owned base intent. "
            f"Core only orchestrates: {', '.join(sorted(CORE_BASE_INTENTS))}"
        )

