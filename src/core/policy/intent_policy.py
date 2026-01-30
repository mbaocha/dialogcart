"""
Unified Intent Policy Loader

Loads intent policy from intent_policy.yaml with fallback to legacy configs.
This module provides a migration path from legacy planning configs to the unified policy.
"""

import yaml
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional

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
        Dictionary with unified intent policy, or empty dict if file not found.
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
        Dictionary with unified intent policy, or empty dict if file not found.
    """
    # Load YAML file (same directory as this Python file)
    config_dir = Path(__file__).resolve().parent
    config_path = config_dir / "intent_policy.yaml"
    
    if not config_path.exists():
        logger.debug(
            f"intent_policy.yaml not found at {config_path}, "
            "will use legacy planning config as fallback"
        )
        return {}
    
    try:
        with config_path.open(encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        
        # Extract intents dict from YAML (structure: {intents: {INTENT_NAME: {...}}})
        unified_policy = raw.get("intents", {}) if isinstance(raw, dict) else {}
        return unified_policy
    except Exception as e:
        logger.warning(
            f"Failed to load intent_policy.yaml at {config_path}: {e}, "
            "will use legacy planning config as fallback"
        )
        return {}


def _load_legacy_planning_policy() -> Dict[str, Any]:
    """
    Load legacy planning policy from intent_planning.yaml (fallback).
    
    Returns:
        Dictionary with legacy intent planning policies.
    """
    from core.planning.policy.action_policy import load_planning_policy
    try:
        return load_planning_policy()
    except Exception as e:
        logger.warning(f"Failed to load legacy planning policy: {e}")
        return {}


def get_planning_required_slots(intent_name: str) -> List[str]:
    """
    Get planning required slots for an intent.
    
    Priority:
    1. intent_policy.yaml (planning.required_slots) - NEW unified policy
    2. intent_planning.yaml (required_slots) - LEGACY fallback
    
    Args:
        intent_name: Intent name (e.g., "CREATE_APPOINTMENT", "MODIFY_BOOKING")
    
    Returns:
        List of required slot names for planning completeness.
    """
    # Normalize intent name
    intent_upper = intent_name.upper() if intent_name else None
    if not intent_upper:
        return []
    
    # Try unified policy first
    unified_policy = _load_unified_policy()
    intent_config = unified_policy.get(intent_upper, {})
    
    if isinstance(intent_config, dict):
        planning_config = intent_config.get("planning", {})
        if isinstance(planning_config, dict):
            required_slots = planning_config.get("required_slots")
            if isinstance(required_slots, list) and required_slots:
                logger.debug(
                    f"Using unified policy for {intent_upper} required_slots: {required_slots}"
                )
                return required_slots
    
    # Fallback to legacy planning config
    legacy_policy = _load_legacy_planning_policy()
    intent_policy = legacy_policy.get(intent_upper, {})
    required_slots = intent_policy.get("required_slots", [])
    
    if isinstance(required_slots, list):
        logger.debug(
            f"Using legacy planning config for {intent_upper} required_slots: {required_slots}"
        )
        return required_slots
    
    return []


def get_execution_steps(intent_name: str) -> List[Dict[str, Any]]:
    """
    Get available execution steps for an intent from unified policy.
    
    Args:
        intent_name: Intent name (e.g., "CREATE_APPOINTMENT", "MODIFY_BOOKING")
    
    Returns:
        List of execution step dictionaries, each with:
        - action: Action name (e.g., "SEARCH_AVAILABILITY", "CONFIRM_APPOINTMENT")
        - mode: Step mode ("exploratory" or "committing")
        - required_slots: List of required slot names for this step
        - optional_slots: List of optional slot names for this step
        - resolves: List of what this step resolves (e.g., ["availability"])
        - requires: List of prerequisites (e.g., ["availability_resolved"])
        - client: Client name required for this step (e.g., "availability_client")
    """
    # Normalize intent name
    intent_upper = intent_name.upper() if intent_name else None
    if not intent_upper:
        return []
    
    # Load unified policy
    unified_policy = _load_unified_policy()
    intent_config = unified_policy.get(intent_upper, {})
    
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
                "client": step_config.get("client", "")
            }
            steps.append(step)
    
    return steps


def select_next_execution_step(
    intent_name: str,
    slots: Dict[str, Any],
    flags: Optional[Dict[str, Any]] = None
) -> Optional[Dict[str, Any]]:
    """
    Select the next execution step to execute based on policy and current state.
    
    Selection rules (from policy):
    - SEARCH_AVAILABILITY is selected if:
        - availability_resolved == False (or not set)
        - required_slots for SEARCH_AVAILABILITY are satisfied
    - CONFIRM_APPOINTMENT is selected if:
        - availability_resolved == True
        - planning.required_slots are satisfied
    
    Args:
        intent_name: Intent name (e.g., "CREATE_APPOINTMENT")
        slots: Current collected slots dictionary
        flags: Optional flags dictionary with:
            - availability_resolved: Boolean indicating if availability has been resolved
    
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
    
    # Extract availability_resolved flag (defaults to False)
    availability_resolved = flags.get("availability_resolved", False)
    
    # Get collected slot names (non-None values)
    collected_slot_names = set(
        slot_name for slot_name, slot_value in slots.items()
        if slot_value is not None
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
                # Required slots not satisfied - skip this step
                continue
        
        # Check prerequisites (requires)
        if "availability_resolved" in requires:
            if not availability_resolved:
                # Prerequisite not met - skip this step
                continue
        
        # Check if this step should be blocked by availability_resolved status
        # SEARCH_AVAILABILITY should only run if availability_resolved == False
        if action == "SEARCH_AVAILABILITY":
            if availability_resolved:
                # Availability already resolved - don't search again
                continue
        
        # This step is ready - return it
        logger.debug(
            f"Selected execution step: {action} for intent {intent_upper} "
            f"(availability_resolved={availability_resolved}, "
            f"collected_slots={collected_slot_names}, mode={mode})"
        )
        return step
    
    # No step is ready
    return None

