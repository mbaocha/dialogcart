"""
Action Policy

Pure, deterministic, stateless planning function for intent execution.
No dialog logic, no execution - only planning.
"""

import yaml
from pathlib import Path
from typing import Dict, Any, List, Set, Optional


def load_planning_policy(config_path: Optional[str] = None) -> Dict[str, Any]:
    """
    Load intent planning policy from YAML configuration.

    Args:
        config_path: Optional path to config file. If None, uses default location.

    Returns:
        Dictionary containing intent planning policies.

    Raises:
        FileNotFoundError: If config file does not exist.
        yaml.YAMLError: If config file is invalid YAML.
    """
    if config_path is None:
        # Default to config/intent_planning.yaml relative to this file
        config_dir = Path(__file__).resolve().parent.parent.parent / "config"
        config_path = str(config_dir / "intent_planning.yaml")

    config_file = Path(config_path)
    if not config_file.exists():
        raise FileNotFoundError(f"Planning policy config not found: {config_path}")

    with config_file.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    return raw.get("intents", raw) if isinstance(raw, dict) else {}


def plan_intent(
    intent: str,
    slots: Dict[str, Any],
    policy: Dict[str, Any]
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
            "executable_actions": []
        }

    # Extract policy fields
    required_slots = intent_policy.get("required_slots", [])
    optional_slots = intent_policy.get("optional_slots", [])
    executable_with = intent_policy.get("executable_with", [])

    # Normalize to sets for efficient operations
    required_set = set(required_slots)
    optional_set = set(optional_slots)

    # Determine collected slots (slots that are present and non-None)
    collected_slots = [
        slot_name for slot_name, slot_value in slots.items()
        if slot_value is not None
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
        "executable_actions": unique_executable_actions
    }


def _map_executable_subset_to_action(
    intent: str,
    subset_slots: Set[str]
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

