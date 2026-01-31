"""
Durable Intent Configuration

Defines which intents may be persisted in session state and accumulate slots across turns.
All intents NOT marked as durable in intent_policy.yaml are ephemeral by default.

Design Rules:
- Only intents with metadata.durable=true in intent_policy.yaml may:
  - Be persisted in session
  - Accumulate slots across turns
  - Be resumed when Luma returns UNKNOWN
- Any intent not marked as durable is ephemeral and must NOT be persisted

This module now reads from intent_policy.yaml as the single source of truth.
"""


def is_durable_intent(intent_name: str) -> bool:
    """
    Check if an intent is durable (may be persisted in session).

    Reads from intent_policy.yaml metadata.durable field.

    Args:
        intent_name: Intent name to check

    Returns:
        True if intent is marked as durable in intent_policy.yaml, False otherwise
    """
    if not intent_name:
        return False

    try:
        from core.policy.intent_policy import get_intent_durable
        return get_intent_durable(intent_name)
    except (ImportError, Exception) as e:
        # Fallback: log error and return False (safe default)
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(
            f"Failed to check durable status for intent '{intent_name}': {e}. "
            "Defaulting to False (ephemeral)."
        )
        return False


def filter_slots_for_intent(intent_name: str, slots: dict) -> dict:
    """
    Filter slots to only include those allowed for the given intent.

    For durable intents, returns all slots (no filtering applied).
    For ephemeral intents, returns empty dict.

    Args:
        intent_name: Intent name (must be durable)
        slots: Raw slots dictionary

    Returns:
        Filtered slots dictionary. For durable intents, returns all slots.
        For ephemeral intents, returns empty dict.
    """
    if not is_durable_intent(intent_name):
        return {}

    # For durable intents, return all slots (no filtering)
    # Slot filtering can be added later if needed based on intent_policy.yaml
    return slots.copy() if slots else {}
