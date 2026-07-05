"""
Unified Intent Policy Loader

Loads intent policy from intent_policy.yaml. This is the ONLY source of truth
for intent planning and execution behavior.

All planning and execution data MUST come from intent_policy.yaml.
No fallback to legacy configs - policy file is required.
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)

# Cache for unified policy
_unified_policy_cache: Optional[Dict[str, Any]] = None
_cache_lock = None

try:
    import threading

    _cache_lock = threading.Lock()
except ImportError:
    _cache_lock = None


def _load_unified_policy() -> Dict[str, Any]:
    """
    Load unified intent policy from intent_policy.yaml (cached at module level).

    Thread-safe lazy loading: loads once on first access, reuses cached data
    for subsequent calls.

    Returns:
        Dictionary with unified intent policy.

    Raises:
        FileNotFoundError: If intent_policy.yaml does not exist.
        RuntimeError: If policy file is invalid or empty.
    """
    global _unified_policy_cache

    # Fast path: return cached data if already loaded
    if _unified_policy_cache is not None:
        return _unified_policy_cache

    # Slow path: load and cache (thread-safe if threading available)
    if _cache_lock:
        with _cache_lock:
            # Double-check after acquiring lock
            if _unified_policy_cache is not None:
                return _unified_policy_cache
            _unified_policy_cache = _load_unified_policy_impl()
    else:
        _unified_policy_cache = _load_unified_policy_impl()

    return _unified_policy_cache


def _load_unified_policy_impl() -> Dict[str, Any]:
    """
    Internal implementation of unified policy loading.

    Returns:
        Dictionary with unified intent policy.

    Raises:
        FileNotFoundError: If intent_policy.yaml does not exist.
        RuntimeError: If policy file is invalid or empty.
    """
    # Load YAML file from config directory (parent of policy directory)
    policy_dir = Path(__file__).resolve().parent
    config_dir = policy_dir.parent / "config"
    config_path = config_dir / "intent_policy.yaml"

    if not config_path.exists():
        raise FileNotFoundError(
            f"intent_policy.yaml not found at {config_path}. "
            "This file is required and defines all intent planning and execution behavior."
        )

    try:
        with config_path.open(encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

        # Extract intents dict from YAML (structure: {intents: {INTENT_NAME: {...}}})
        unified_policy = raw.get("intents", {}) if isinstance(raw, dict) else {}

        if not unified_policy:
            raise RuntimeError(
                f"intent_policy.yaml at {config_path} is empty or has no 'intents' section. "
                "This file must define all intent policies."
            )

        return unified_policy
    except yaml.YAMLError as e:
        raise RuntimeError(
            f"Failed to parse intent_policy.yaml at {config_path}: {e}"
        ) from e
    except Exception as e:
        raise RuntimeError(
            f"Failed to load intent_policy.yaml at {config_path}: {e}"
        ) from e


def get_intent_durable(intent_name: str) -> bool:
    """
    Get durable status for an intent from intent_policy.yaml.

    Args:
        intent_name: Intent name (e.g., "CREATE_APPOINTMENT", "MODIFY_BOOKING")

    Returns:
        True if intent is durable (should persist across turns), False otherwise.
        Defaults to False if intent not found or metadata not present.
    """
    # Normalize intent name
    intent_upper = intent_name.upper() if intent_name else None
    if not intent_upper:
        return False

    unified_policy = _load_unified_policy()
    intent_config = unified_policy.get(intent_upper)

    if not isinstance(intent_config, dict):
        return False

    metadata = intent_config.get("metadata", {})
    if not isinstance(metadata, dict):
        return False

    return metadata.get("durable", False)


def is_durable_intent(intent_name: str) -> bool:
    """
    Check if an intent is marked as durable in intent_policy.yaml.

    This is a convenience alias for get_intent_durable() for readability.

    Args:
        intent_name: Intent name (e.g., "CREATE_APPOINTMENT", "MODIFY_BOOKING")

    Returns:
        True if intent is durable (should persist across turns), False otherwise.
        Defaults to False if intent not found or metadata not present.
    """
    if not intent_name:
        return False

    unified_policy = _load_unified_policy()
    intent_config = unified_policy.get(intent_name.upper())

    if not isinstance(intent_config, dict):
        return False

    metadata = intent_config.get("metadata", {})
    if not isinstance(metadata, dict):
        return False

    return bool(metadata.get("durable") is True)


def get_planning_required_slots(intent_name: str) -> List[str]:
    """
    Get planning required slots for an intent from intent_policy.yaml.

    Args:
        intent_name: Intent name (e.g., "CREATE_APPOINTMENT", "MODIFY_BOOKING")

    Returns:
        List of required slot names for planning completeness.

    Raises:
        KeyError: If intent is not defined in policy or missing required_slots.
    """
    # Normalize intent name
    intent_upper = intent_name.upper() if intent_name else None
    if not intent_upper:
        return []

    unified_policy = _load_unified_policy()
    intent_config = unified_policy.get(intent_upper)

    if not isinstance(intent_config, dict):
        raise KeyError(
            f"Intent '{intent_upper}' not found in intent_policy.yaml. "
            f"Available intents: {list(unified_policy.keys())}"
        )

    planning_config = intent_config.get("planning")
    if not isinstance(planning_config, dict):
        raise KeyError(
            f"Intent '{intent_upper}' missing 'planning' section in intent_policy.yaml"
        )

    required_slots = planning_config.get("required_slots")
    if not isinstance(required_slots, list):
        raise KeyError(
            f"Intent '{intent_upper}' missing 'planning.required_slots' in intent_policy.yaml"
        )

    return required_slots


def get_planning_executable_with(intent_name: str) -> List[List[str]]:
    """
    Get planning executable_with subsets for an intent from intent_policy.yaml.

    Args:
        intent_name: Intent name (e.g., "CREATE_APPOINTMENT", "MODIFY_BOOKING")

    Returns:
        List of executable_with subsets (each subset is a list of slot names).
    """
    # Normalize intent name
    intent_upper = intent_name.upper() if intent_name else None
    if not intent_upper:
        return []

    unified_policy = _load_unified_policy()
    intent_config = unified_policy.get(intent_upper)

    if not isinstance(intent_config, dict):
        return []

    planning_config = intent_config.get("planning", {})
    if not isinstance(planning_config, dict):
        return []

    executable_with = planning_config.get("executable_with", [])
    if not isinstance(executable_with, list):
        return []

    # Normalize: ensure each item is a list
    normalized = []
    for item in executable_with:
        if isinstance(item, list):
            normalized.append(item)
        elif isinstance(item, str):
            normalized.append([item])

    return normalized


def get_planning_optional_slots(intent_name: str) -> List[str]:
    """
    Get planning optional slots for an intent from intent_policy.yaml.

    Args:
        intent_name: Intent name (e.g., "CREATE_APPOINTMENT", "MODIFY_BOOKING")

    Returns:
        List of optional slot names for planning.
    """
    # Normalize intent name
    intent_upper = intent_name.upper() if intent_name else None
    if not intent_upper:
        return []

    unified_policy = _load_unified_policy()
    intent_config = unified_policy.get(intent_upper)

    if not isinstance(intent_config, dict):
        return []

    planning_config = intent_config.get("planning", {})
    if not isinstance(planning_config, dict):
        return []

    optional_slots = planning_config.get("optional_slots", [])
    if isinstance(optional_slots, list):
        return optional_slots

    return []


def get_commit_action(intent_name: str) -> Optional[str]:
    """
    Get commit action for an intent by finding the execution step with mode=committing.

    Args:
        intent_name: Intent name (e.g., "CREATE_APPOINTMENT", "MODIFY_BOOKING")

    Returns:
        Commit action name (e.g., "CONFIRM_APPOINTMENT") or None if not found.
    """
    # Normalize intent name
    intent_upper = intent_name.upper() if intent_name else None
    if not intent_upper:
        return None

    unified_policy = _load_unified_policy()
    intent_config = unified_policy.get(intent_upper)

    if not isinstance(intent_config, dict):
        return None

    execution_config = intent_config.get("execution", {})
    if not isinstance(execution_config, dict):
        return None

    # Find step with mode=committing
    for action_name, step_config in execution_config.items():
        if isinstance(step_config, dict):
            mode = step_config.get("mode", "exploratory")
            if mode == "committing":
                return action_name

    return None


def get_execution_steps(intent_name: str) -> List[Dict[str, Any]]:
    """
    Get available execution steps for an intent from intent_policy.yaml.

    Args:
        intent_name: Intent name (e.g., "CREATE_APPOINTMENT", "MODIFY_BOOKING")

    Returns:
        List of execution step dictionaries, each with:
        - action: Action name (e.g., "SEARCH_AVAILABILITY", "CONFIRM_APPOINTMENT")
        - mode: Step mode ("exploratory" or "committing")
        - required_slots: List of required slot names for this step
        - optional_slots: List of optional slot names for this step
        - resolves: List of what this step resolves (e.g., ["availability"])
        - requires: List of business fact prerequisites (e.g., ["availability_ready"])
        - client: Client name required for this step (e.g., "availability_client")
    """
    # Normalize intent name
    intent_upper = intent_name.upper() if intent_name else None
    if not intent_upper:
        return []

    unified_policy = _load_unified_policy()
    intent_config = unified_policy.get(intent_upper)

    if not isinstance(intent_config, dict):
        return []

    execution_config = intent_config.get("execution", {})
    if not isinstance(execution_config, dict):
        return []

    # Build list of execution steps
    steps = []
    for action_name, step_config in execution_config.items():
        if isinstance(step_config, dict):
            step = {
                "action": action_name,
                "mode": step_config.get("mode", "exploratory"),
                "required_slots": step_config.get("required_slots", []),
                "optional_slots": step_config.get("optional_slots", []),
                "resolves": step_config.get("resolves", []),
                "requires": step_config.get("requires", []),
                "client": step_config.get("client", ""),
            }
            steps.append(step)

    return steps


def _evaluate_step_requirement(requirement: str, *, flags: Dict[str, Any]) -> bool:
    """Return True when a policy ``requires`` business-fact token is satisfied."""
    _BUSINESS_FACT_KEYS = frozenset(
        {
            "availability_check_required",
            "availability_ready",
            "booking_identification_required",
            "booking_identified",
            "booking_hold_required",
            "booking_hold_ready",
            "time_selection_required",
            "time_selection_ready",
            "user_confirmation_required",
            "user_confirmation_satisfied",
        }
    )
    if requirement in _BUSINESS_FACT_KEYS:
        return bool(flags.get(requirement, False))

    logger.warning(
        "Unknown policy requirement %r — treating as unsatisfied (fail closed)",
        requirement,
    )
    return False


def select_next_execution_step(
    intent_name: str, slots: Dict[str, Any], flags: Optional[Dict[str, Any]] = None
) -> Optional[Dict[str, Any]]:
    """
    Select the next execution step to execute based on policy and current state.

    Selection rules (from policy):
    - Each step in intent_policy.yaml is evaluated in order.
    - Exploratory steps require their step required_slots plus all ``requires`` business facts.
    - Committing steps require planning.required_slots plus all ``requires`` business facts.

    Args:
        intent_name: Intent name (e.g., "CREATE_APPOINTMENT")
        slots: Current collected slots dictionary
        flags: Optional flags dictionary from build_policy_execution_flags(), including
            business facts (availability_ready, user_confirmation_satisfied, etc.) and
            runtime flags (availability_resolved, confirmation_state, etc.)

    Returns:
        Selected execution step dictionary (same format as get_execution_steps),
        or None if no step should be executed.
    """
    if flags is None:
        flags = {}

    # Normalize intent name
    intent_upper = intent_name.upper() if intent_name else None
    if not intent_upper:
        return None

    # Get available execution steps
    steps = get_execution_steps(intent_name)
    if not steps:
        # No execution steps defined in policy - return None
        return None

    # Extract flags for debug logging
    availability_resolved = flags.get("availability_resolved", False)
    confirmation_state = flags.get("confirmation_state")

    # Get collected slot names (non-None values)
    collected_slot_names = set(
        slot_name for slot_name, slot_value in slots.items() if slot_value is not None
    )

    # For CONFIRM_APPOINTMENT, we need to check planning.required_slots, not just step.required_slots
    planning_required_slots = get_planning_required_slots(intent_name)
    planning_required_slots_set = set(planning_required_slots)

    # Evaluate each step to see if it's ready
    for step in steps:
        action = step.get("action")
        required_slots = step.get("required_slots", [])
        requires = step.get("requires", [])

        # For committing steps (like CONFIRM_APPOINTMENT), check planning completeness
        # For exploratory steps (like SEARCH_AVAILABILITY), check step-specific slots
        mode = step.get("mode", "exploratory")

        if mode == "committing":
            # Committing steps require all planning.required_slots to be satisfied
            if not planning_required_slots_set.issubset(collected_slot_names):
                # Planning completeness not satisfied - skip this step
                continue
        else:
            # Exploratory steps only need their own required_slots
            required_slots_set = set(required_slots)
            if not required_slots_set.issubset(collected_slot_names):
                continue

        # Check prerequisites (requires)
        requirements_met = True
        for requirement in requires:
            if not _evaluate_step_requirement(requirement, flags=flags):
                requirements_met = False
                break
        if not requirements_met:
            continue

        # This step is ready - return it
        logger.debug(
            f"Selected execution step: {action} for intent {intent_upper} "
            f"(availability_resolved={availability_resolved}, "
            f"availability_check_required={flags.get('availability_check_required')}, "
            f"confirmation_state={confirmation_state}, "
            f"collected_slots={collected_slot_names}, mode={mode})"
        )
        return step

    # No step is ready
    return None


def validate_policy_completeness() -> List[str]:
    """
    Validate that intent_policy.yaml is complete and well-formed.

    Checks:
    1. Every intent has planning.required_slots
    2. Every durable intent has execution steps
    3. Exactly one committing step exists per intent

    Returns:
        List of validation error messages (empty if all valid).

    Raises:
        RuntimeError: If policy file cannot be loaded or is invalid.
    """
    errors = []

    try:
        unified_policy = _load_unified_policy()
    except Exception as e:
        raise RuntimeError(
            f"Failed to load intent_policy.yaml for validation: {e}"
        ) from e

    if not unified_policy:
        errors.append("intent_policy.yaml is empty - no intents defined")
        return errors

    for intent_name, intent_config in unified_policy.items():
        if not isinstance(intent_config, dict):
            errors.append(f"Intent '{intent_name}': config must be a dictionary")
            continue

        # Check 1: Every intent must have planning.required_slots
        planning_config = intent_config.get("planning")
        if not isinstance(planning_config, dict):
            errors.append(f"Intent '{intent_name}': missing 'planning' section")
            continue

        required_slots = planning_config.get("required_slots")
        if not isinstance(required_slots, list) or not required_slots:
            errors.append(
                f"Intent '{intent_name}': missing or empty 'planning.required_slots'"
            )

        # Check 2: Every durable intent must have execution steps
        metadata = intent_config.get("metadata", {})
        is_durable = (
            metadata.get("durable", False) if isinstance(metadata, dict) else False
        )

        execution_config = intent_config.get("execution", {})
        if not isinstance(execution_config, dict) or not execution_config:
            if is_durable:
                errors.append(
                    f"Intent '{intent_name}': durable intent must have 'execution' steps"
                )
        else:
            # Check 3: Exactly one committing step per intent
            committing_steps = []
            for action_name, step_config in execution_config.items():
                if isinstance(step_config, dict):
                    mode = step_config.get("mode", "exploratory")
                    if mode == "committing":
                        committing_steps.append(action_name)

            if len(committing_steps) == 0 and is_durable:
                errors.append(
                    f"Intent '{intent_name}': durable intent must have exactly one execution step with mode=committing, found 0"
                )
            elif len(committing_steps) > 1:
                errors.append(
                    f"Intent '{intent_name}': must have exactly one execution step with mode=committing, "
                    f"found {len(committing_steps)}: {committing_steps}"
                )

    return errors


# Startup validation: ensure policy is complete
# This runs once at module import time (startup) and fails fast if misconfigured
try:
    validation_errors = validate_policy_completeness()
    if validation_errors:
        error_msg = f"intent_policy.yaml validation failed with {len(validation_errors)} error(s):\n\n"
        for error in validation_errors:
            error_msg += f"  - {error}\n"
        error_msg += (
            f"\nSee src/core/config/intent_policy.yaml for the policy definition."
        )
        raise RuntimeError(error_msg)
except RuntimeError:
    # Re-raise to fail fast at startup
    raise
except Exception:
    # If policy file doesn't exist or can't be loaded, that's OK during import
    # (validation will happen when policy is actually used)
    pass
