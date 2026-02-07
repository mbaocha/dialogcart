"""
Action Router

Maps action names from intent_policy.yaml to handler functions.

This module routes actions like CONFIRM_APPOINTMENT, SEARCH_AVAILABILITY, etc.
to their corresponding handler functions (e.g., booking.create).

EXTENDING THE SYSTEM:
====================

To add a new workflow:

1. Define the commit action in intent_policy.yaml (execution step with mode=committing):
   intents:
     YOUR_INTENT:
       execution:
         YOUR_COMMIT_ACTION:
           mode: committing
           required_slots: [...]
           client: booking_client

2. Register the handler here in ACTION_HANDLERS:
   "YOUR_COMMIT_ACTION": "your.handler.function"

3. Implement the handler in orchestrator.py (in handle_message execution logic)

IMPORTANT: Every commit action (mode=committing) in intent_policy.yaml MUST have a handler
registered here. Use validate_commit_action_handlers() to check this at startup.
"""

from typing import Dict, Optional, List

# Action name (from intent_policy.yaml) → handler action name mapping
ACTION_HANDLERS: Dict[str, str] = {
    # Commit actions
    "CONFIRM_APPOINTMENT": "booking.create",
    "FINALIZE_RESERVATION": "booking.create",

    # Fallback actions (non-destructive)
    # Placeholder - may need dedicated handler
    "SEARCH_AVAILABILITY": "booking.inquiry",
    # Placeholder - may need dedicated handler
    "SERVICE_CATALOG": "booking.inquiry",
}


def get_handler_action(action_name: str) -> Optional[str]:
    """
    Get handler action name for an action from intent_policy.yaml.

    Maps action names like CONFIRM_APPOINTMENT to their handler functions
    like booking.create.

    Args:
        action_name: Action name from intent_policy.yaml (e.g., "CONFIRM_APPOINTMENT")

    Returns:
        Handler action name (e.g., "booking.create") or None if unsupported
    """
    return ACTION_HANDLERS.get(action_name)


def validate_commit_action_handlers() -> List[str]:
    """
    Validate that all commit actions defined in intent_policy.yaml have handlers.

    This function checks that every commit action (from execution steps with mode=committing)
    has a corresponding entry in ACTION_HANDLERS. Returns a list of missing handlers (empty if all valid).

    This should be called at startup to fail fast with clear errors.

    Returns:
        List of commit action names that are missing handlers (empty if all valid)

    Raises:
        RuntimeError: If any commit actions are missing handlers (fail fast)
    """
    from core.policy.intent_policy import get_execution_steps

    # Get all intents from unified policy
    from core.policy.intent_policy import _load_unified_policy
    unified_policy = _load_unified_policy()

    missing_handlers = []

    # Check all intents in unified policy
    for intent_name in unified_policy.keys():
        from core.policy.intent_policy import get_commit_action
        commit_action = get_commit_action(intent_name)

        if commit_action and commit_action not in ACTION_HANDLERS:
            missing_handlers.append(commit_action)

    if missing_handlers:
        error_msg = (
            f"Missing handlers for {len(missing_handlers)} commit action(s) defined in "
            f"intent_policy.yaml:\n"
            f"  {', '.join(missing_handlers)}\n\n"
            f"To fix: Add these actions to ACTION_HANDLERS in action_router.py:\n"
        )
        for action in missing_handlers:
            error_msg += f"  \"{action}\": \"your.handler.function\",\n"
        error_msg += (
            f"\nSee intent_policy.yaml and action_router.py for extension instructions."
        )
        raise RuntimeError(error_msg)

    return []


# Startup validation: ensure all commit actions have handlers
# This runs once at module import time (startup) and fails fast if misconfigured
# Does NOT affect runtime behavior - only validates configuration at startup
try:
    validate_commit_action_handlers()
except RuntimeError:
    # Re-raise to fail fast at startup
    raise
except Exception:
    # If config file doesn't exist or can't be loaded, that's OK
    # (validation will happen when config is actually used)
    pass
