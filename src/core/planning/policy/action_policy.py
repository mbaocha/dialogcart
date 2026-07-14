"""
Action Policy

Pure, deterministic, stateless planning function for intent execution.
No dialog logic, no execution - only planning.
"""

from typing import Any, Dict, List, Optional, Set


def _get_required_slots_from_unified_policy(intent_name: str) -> List[str]:
    """
    Get required slots from unified intent_policy.yaml with fallback to legacy.

    This function provides a migration path from legacy planning configs
    to the unified intent_policy.yaml.

    Args:
        intent_name: Intent name (uppercase, e.g., "CREATE_APPOINTMENT")

    Returns:
        List of required slot names, or empty list if not found in unified policy.
    """
    try:
        from core.policy.intent_policy import get_planning_required_slots

        return get_planning_required_slots(intent_name)
    except (ImportError, Exception):
        # If unified policy module doesn't exist or fails, return empty list
        # This allows fallback to legacy policy
        return []


def load_planning_policy(config_path: Optional[str] = None) -> Dict[str, Any]:
    """
    Load intent planning policy from intent_policy.yaml.

    DEPRECATED: This function is kept for backward compatibility but now loads
    from intent_policy.yaml. The policy structure is flattened to match the
    legacy format expected by plan_intent().

    Args:
        config_path: Ignored - always loads from intent_policy.yaml

    Returns:
        Dictionary containing intent planning policies (legacy format).

    Raises:
        RuntimeError: If intent_policy.yaml cannot be loaded.
    """
    # Load from unified policy and convert to legacy format
    from core.policy.intent_policy import _load_unified_policy

    unified_policy = _load_unified_policy()

    # Convert to legacy format: {INTENT_NAME: {required_slots, optional_slots, executable_with}}
    legacy_format = {}
    for intent_name, intent_config in unified_policy.items():
        if isinstance(intent_config, dict):
            planning_config = intent_config.get("planning", {})
            if isinstance(planning_config, dict):
                legacy_format[intent_name] = {
                    "required_slots": planning_config.get("required_slots", []),
                    "optional_slots": planning_config.get("optional_slots", []),
                    "executable_with": planning_config.get("executable_with", []),
                }

    return legacy_format


def plan_intent(
    intent: str, slots: Dict[str, Any], policy: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Plan intent execution based on collected slots and policy.

    Pure, deterministic, stateless function. Same inputs always produce same outputs.
    No side effects, no dialog logic, no execution.

    Args:
        intent: Intent name (e.g., "CREATE_APPOINTMENT", "MODIFY_BOOKING")
        slots: Dictionary of collected slot values (e.g., {"service_id": "123", "date": "2024-01-01"})
        policy: Intent planning policy dictionary (from load_planning_policy)

    Returns:
        Dictionary with:
            - intent: The input intent name
            - collected_slots: List of slot names that are present in slots dict
            - missing_slots: List of required slot names that are missing
            - executable_actions: List of action names that can be executed with current slots
                (based on executable_with subsets in policy)

    Example:
        >>> policy = load_planning_policy()
        >>> plan = plan_intent("CREATE_APPOINTMENT", {"service_id": "123"}, policy)
        >>> plan["missing_slots"]
        ['date', 'time']
        >>> plan["executable_actions"]
        ['SEARCH_AVAILABILITY']
    """
    # Normalize intent name (handle case variations)
    intent_upper = intent.upper() if intent else None

    # Get intent policy
    intent_policy = policy.get(intent_upper) if intent_upper else None

    if not intent_policy:
        # Unknown intent - return empty plan
        return {
            "intent": intent,
            "collected_slots": [],
            "missing_slots": [],
            "executable_actions": [],
        }

    # Extract policy fields from unified policy (intent_policy.yaml)
    # All planning data MUST come from intent_policy.yaml - no fallback
    required_slots = _get_required_slots_from_unified_policy(intent_upper)
    if not required_slots:
        # If unified policy doesn't have required_slots, this is an error
        # Return empty plan (intent not supported)
        return {
            "intent": intent,
            "collected_slots": [],
            "missing_slots": [],
            "executable_actions": [],
        }

    # Get optional_slots from unified policy
    from core.policy.intent_policy import get_planning_optional_slots

    optional_slots = get_planning_optional_slots(intent_upper)

    # Get executable_with from unified policy
    from core.policy.intent_policy import get_planning_executable_with

    executable_with = get_planning_executable_with(intent_upper)

    # Normalize to sets for efficient operations
    required_set = set(required_slots)
    optional_set = set(optional_slots)

    # Determine collected slots (slots that are present and non-None)
    collected_slots = [
        slot_name for slot_name, slot_value in slots.items() if slot_value is not None
    ]
    collected_set = set(collected_slots)

    # Determine missing required slots
    missing_slots = sorted(list(required_set - collected_set))

    # Determine executable actions based on executable_with subsets
    executable_actions = []
    for executable_subset in executable_with:
        # executable_subset can be a list of slot names or a single string
        if isinstance(executable_subset, str):
            subset_slots = {executable_subset}
        elif isinstance(executable_subset, list):
            subset_slots = set(executable_subset)
        else:
            continue

        # Check if all slots in this subset are collected
        if subset_slots.issubset(collected_set):
            # Map executable subset to action name based on intent
            action = _map_executable_subset_to_action(intent_upper, subset_slots)
            if action:
                executable_actions.append(action)

    # Remove duplicates while preserving order
    seen = set()
    unique_executable_actions = []
    for action in executable_actions:
        if action not in seen:
            seen.add(action)
            unique_executable_actions.append(action)

    return {
        "intent": intent,
        "collected_slots": sorted(collected_slots),
        "missing_slots": missing_slots,
        "executable_actions": unique_executable_actions,
    }


def _map_executable_subset_to_action(
    intent: str, subset_slots: Set[str]
) -> Optional[str]:
    """
    Map an executable slot subset to an action name.

    This is a deterministic mapping based on intent and slot combination.
    Pure function with no side effects.

    Args:
        intent: Intent name (uppercase)
        subset_slots: Set of slot names that are collected

    Returns:
        Action name string, or None if no mapping exists.
    """
    # Map common executable subsets to actions
    if intent == "CREATE_APPOINTMENT":
        if "service_id" in subset_slots:
            return "SEARCH_AVAILABILITY"
    elif intent == "CREATE_RESERVATION":
        if "service_id" in subset_slots:
            return "SEARCH_AVAILABILITY"
    elif intent == "MODIFY_BOOKING":
        if "booking_id" in subset_slots:
            return "PREVIEW_MODIFICATION"
    elif intent == "CANCEL_BOOKING":
        if "booking_id" in subset_slots:
            return "FETCH_BOOKING"

    return None
