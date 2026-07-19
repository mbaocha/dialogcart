"""
Core-Owned Base Intents

Explicitly declares which intents are owned and orchestrated by core.
This establishes a stable boundary for the core state machine.

These intents represent the foundational booking operations that core
is responsible for orchestrating. All other intents are considered
non-core and should not be orchestrated by core.

This is a declarative module only - it does not modify behavior, only
establishes boundaries.
"""

from typing import Set

# Core-owned base intents that core orchestrates
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
