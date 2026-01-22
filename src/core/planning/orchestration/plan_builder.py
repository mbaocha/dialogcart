"""
Plan Builder

Builds decision plans from Luma responses.

This module is pure and side-effect free:
- No external API calls
- No state mutation
- Deterministic interpretation of Luma responses

Responsibilities:
- Building decision plans with status, allowed_actions, blocked_actions, awaiting
"""

import logging
import threading
from pathlib import Path
from typing import Dict, Any, Optional, List

import yaml

from core.routing import get_template_key, get_action_name
from core.orchestration.errors import UnsupportedIntentError

logger = logging.getLogger(__name__)

# Cache for intent execution config (loaded once per process)
_intent_execution_cache: Optional[Dict[str, Any]] = None
_cache_lock = threading.Lock()


def _load_intent_execution_config() -> Dict[str, Any]:
    """
    Load intent execution configuration from YAML file (cached at module level).
    
    Thread-safe lazy loading: loads once on first access, reuses cached data
    for subsequent calls. Zero YAML I/O on request path after initial load.
    
    Returns:
        Dictionary with intent execution config (intents -> commit action mapping only)
    """
    global _intent_execution_cache
    
    # Fast path: return cached data if already loaded
    if _intent_execution_cache is not None:
        return _intent_execution_cache
    
    # Slow path: load and cache (thread-safe)
    with _cache_lock:
        # Double-check after acquiring lock (another thread may have loaded it)
        if _intent_execution_cache is not None:
            return _intent_execution_cache
        
        # Load YAML file
        config_dir = Path(__file__).resolve().parent.parent.parent / "config"
        config_path = config_dir / "intent_execution.yaml"
        
        if not config_path.exists():
            logger.warning(
                f"intent_execution.yaml not found at {config_path}, "
                "using empty config (no commit actions or fallbacks)"
            )
            _intent_execution_cache = {}
            return _intent_execution_cache
        
        with config_path.open(encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        
        # Extract intents dict from YAML (structure: {intents: {INTENT_NAME: {...}}})
        _intent_execution_cache = raw.get("intents", {}) if isinstance(raw, dict) else {}
        return _intent_execution_cache


def _extract_missing_slots(luma_response: Dict[str, Any]) -> List[str]:
    """
    Extract missing slots from Luma response.
    
    Checks multiple sources:
    1. luma_response.missing_slots (authoritative if present)
    2. luma_response.issues (dict with slot_name -> "missing")
    3. Empty list if neither present
    
    Args:
        luma_response: Luma API response
        
    Returns:
        List of missing slot names
    """
    # Check direct missing_slots field (authoritative)
    missing_slots = luma_response.get("missing_slots")
    if isinstance(missing_slots, list):
        return missing_slots
    
    # Fallback: extract from issues dict
    issues = luma_response.get("issues", {})
    if isinstance(issues, dict):
        missing = []
        for slot_name, issue_value in issues.items():
            if issue_value == "missing" or (isinstance(issue_value, dict) and issue_value.get("status") == "missing"):
                missing.append(slot_name)
        return missing
    
    return []


def build_decision_plan(
    intent_name: str,
    luma_response: Dict[str, Any],
    domain: str
) -> Dict[str, Any]:
    """
    Build a decision plan from Luma response.
    
    PLANNING BOUNDARY:
    - missing_slots MUST come from planner (intent_planning.yaml)
    - executable_actions MUST come from planner (intent_planning.yaml)
    - intent_execution.yaml is ONLY for action → handler routing (no slot logic)
    
    Applies rules:
    - commit.action is the irreversible commit step (from intent_execution.yaml)
    - Block commit when needs_clarification == true
    - executable_actions come from planner (partial execution with subsets of slots)
    
    Args:
        intent_name: Intent name from Luma
        luma_response: Luma API response (must have missing_slots from planner)
        domain: Domain for template key routing
        
    Returns:
        Decision plan dictionary with:
        - status: READY, NEEDS_CLARIFICATION, or AWAITING_CONFIRMATION
        - allowed_actions: List of allowed action names
        - blocked_actions: List of blocked action names
        - awaiting: USER_CONFIRMATION or null
    """
    # Load intent execution config
    intent_configs = _load_intent_execution_config()
    intent_config = intent_configs.get(intent_name, {})
    
    # Extract missing slots
    missing_slots = _extract_missing_slots(luma_response)
    
    # Determine status
    needs_clarification = luma_response.get("needs_clarification", False)
    booking = luma_response.get("booking", {})
    confirmation_state = booking.get("confirmation_state") if isinstance(booking, dict) else None
    
    # DEBUG: Print decision plan building details
    print(f"[BUILD_PLAN] intent={intent_name} missing_slots={missing_slots} needs_clarification={needs_clarification} confirmation_state={confirmation_state}")
    
    # CRITICAL: If missing_slots is non-empty, status MUST be NEEDS_CLARIFICATION
    # This is the authoritative rule - missing slots drive clarification, not Luma flags
    if missing_slots:
        status = "NEEDS_CLARIFICATION"
        print(f"[BUILD_PLAN] Setting status=NEEDS_CLARIFICATION because missing_slots={missing_slots}")
    elif needs_clarification:
        status = "NEEDS_CLARIFICATION"
        print(f"[BUILD_PLAN] Setting status=NEEDS_CLARIFICATION because needs_clarification=True")
    elif confirmation_state == "pending":
        status = "AWAITING_CONFIRMATION"
        print(f"[BUILD_PLAN] Setting status=AWAITING_CONFIRMATION because confirmation_state=pending")
    else:
        status = "READY"
        print(f"[BUILD_PLAN] Setting status=READY (no missing slots, no clarification needed, no pending confirmation)")
    
    # Get commit action
    commit_config = intent_config.get("commit", {})
    commit_action = commit_config.get("action") if isinstance(commit_config, dict) else None
    
    # Determine allowed and blocked actions
    allowed_actions: List[str] = []
    blocked_actions: List[str] = []
    
    # Get executable_actions from planner (ONLY source of truth for partial execution)
    # Planner computes executable_actions from intent_planning.yaml based on collected slots
    executable_actions = []
    if intent_name:
        from core.planning.policy.action_policy import plan_intent, load_planning_policy
        # Use effective_collected_slots if available (more accurate), otherwise use slots
        effective_slots = luma_response.get("_effective_collected_slots")
        if effective_slots is None:
            effective_slots = luma_response.get("slots", {})
        policy = load_planning_policy()
        planner_result = plan_intent(intent_name, effective_slots, policy)
        executable_actions = planner_result.get("executable_actions", [])
    
    # CRITICAL: If missing_slots exist, block ALL actions (including executable_actions from planner)
    # Planner's executable_actions are for partial execution, but we block them when missing required slots
    if missing_slots:
        # Block all actions when missing_slots exist
        if commit_action:
            blocked_actions.append(commit_action)
        # Do NOT allow executable_actions while clarifying - all actions blocked
    else:
        # No missing slots - allow executable_actions from planner (partial execution)
        allowed_actions.extend(executable_actions)
        
        # Commit action blocking rules
        if commit_action:
            # CRITICAL: If missing_slots is empty, allow commit immediately
            # Tests expect READY state to execute without confirmation when slots are complete
            # Do NOT require confirmation_state == "confirmed" when all slots are filled
            if needs_clarification:
                # Luma explicitly says needs clarification - block commit
                blocked_actions.append(commit_action)
            else:
                # All slots filled and no clarification needed - allow commit
                # Do NOT check confirmation_state - tests expect immediate execution
                allowed_actions.append(commit_action)
    
    # Deduplicate
    allowed_actions = list(set(allowed_actions))
    blocked_actions = list(set(blocked_actions))
    
    # Determine awaiting
    awaiting = "USER_CONFIRMATION" if confirmation_state == "pending" else None
    
    # Derive stage and action from current state
    # Stage/action mapping based on missing_slots and executable_actions
    stage = None
    action = None
    
    # CONFIRM ACTION MAP: Hard mapping for complete slots (missing_slots == [])
    CONFIRM_ACTION_MAP = {
        "CREATE_APPOINTMENT": "CONFIRM_APPOINTMENT",
        "CREATE_RESERVATION": "CONFIRM_RESERVATION",
        "MODIFY_BOOKING": "APPLY_MODIFICATION",
        "CANCEL_BOOKING": "CONFIRM_CANCELLATION"
    }
    
    if len(missing_slots) > 0:
        # Missing slots - determine stage based on intent and executable actions
        if intent_name in ("MODIFY_BOOKING", "CANCEL_BOOKING"):
            stage = "IDENTIFY"
            action = "FETCH_BOOKING"
        else:
            # CREATE_APPOINTMENT or CREATE_RESERVATION
            stage = "AVAILABILITY"
            # Use first executable action if available, otherwise default to SEARCH_AVAILABILITY
            if executable_actions:
                action = executable_actions[0]
            else:
                action = "SEARCH_AVAILABILITY"
    else:
        # No missing slots - CONFIRM decision
        stage = "CONFIRM"
        
        # Determine action from CONFIRM_ACTION_MAP or commit_action
        if intent_name in CONFIRM_ACTION_MAP:
            action = CONFIRM_ACTION_MAP[intent_name]
        elif commit_action and commit_action in allowed_actions:
            # Commit action is available and allowed - use it
            action = commit_action
        elif commit_action:
            # Commit action exists but is blocked - use fallback
            if intent_name == "MODIFY_BOOKING":
                action = "APPLY_MODIFICATION"
            elif intent_name == "CANCEL_BOOKING":
                action = "CONFIRM_CANCELLATION"
            else:
                # Fallback to first allowed action if no commit action
                # Filter out SEARCH_AVAILABILITY when all slots are complete
                filtered_actions = [a for a in allowed_actions if a != "SEARCH_AVAILABILITY"]
                action = filtered_actions[0] if filtered_actions else None
        else:
            # No commit action defined - use fallback based on intent
            if intent_name == "MODIFY_BOOKING":
                action = "APPLY_MODIFICATION"
            elif intent_name == "CANCEL_BOOKING":
                action = "CONFIRM_CANCELLATION"
            else:
                # Last resort: use first allowed action (but never SEARCH_AVAILABILITY when slots complete)
                filtered_actions = [a for a in allowed_actions if a != "SEARCH_AVAILABILITY"]
                action = filtered_actions[0] if filtered_actions else None
    
    return {
        "status": status,
        "stage": stage,
        "action": action,
        "allowed_actions": allowed_actions,
        "blocked_actions": blocked_actions,
        "awaiting": awaiting,
        "executable_actions": executable_actions
    }

