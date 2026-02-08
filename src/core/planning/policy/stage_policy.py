"""
Stage Policy

Maps (intent, missing_slots) to advisory dialog prompts.
Returns structured JSON instructions, not plain text.

PLANNING BOUNDARY:
- This module is PURELY REACTIVE - it accepts missing_slots as input
- It MUST NOT compute, infer, or assume missing slots
- missing_slots MUST come from the planner (core.planning.policy.action_policy.plan_intent)
- No ordering or staging logic allowed
- Planning = intent_planning.yaml + planner code
- Dialog = dialog_policy.yaml (consumes planner output)
- Execution = intent_execution.yaml (dumb routing only)
"""

import yaml
from pathlib import Path
from typing import Dict, Any, List, Optional


def load_dialog_policy(config_path: Optional[str] = None) -> Dict[str, Any]:
    """
    Load dialog policy from YAML configuration.

    Args:
        config_path: Optional path to config file. If None, uses default location.

    Returns:
        Dictionary containing dialog policy mappings.

    Raises:
        FileNotFoundError: If config file does not exist.
        yaml.YAMLError: If config file is invalid YAML.
    """
    if config_path is None:
        # Default to config/dialog_policy.yaml relative to this file
        config_dir = Path(__file__).resolve().parent.parent.parent / "config"
        config_path = str(config_dir / "dialog_policy.yaml")

    config_file = Path(config_path)
    if not config_file.exists():
        raise FileNotFoundError(f"Dialog policy config not found: {config_path}")

    with config_file.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    return raw.get("dialog_prompts", raw) if isinstance(raw, dict) else {}


def get_dialog_instructions(
    intent: str,
    missing_slots: List[str],
    policy: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Get dialog instructions for a given intent and missing slots.
    
    GUARD: This function is PURELY REACTIVE.
    - It accepts missing_slots as input (from planner)
    - It MUST NOT compute, infer, or assume missing slots
    - missing_slots MUST come from core.planning.policy.action_policy.plan_intent()
    
    Returns structured JSON instructions, not plain text.
    The prompt is advisory and does not enforce order - users can provide
    missing slots in any order.
    
    Args:
        intent: Intent name (e.g., "CREATE_APPOINTMENT", "MODIFY_BOOKING")
        missing_slots: List of missing slot names (e.g., ["date", "time"])
                       MUST be computed by planner from intent_planning.yaml
        policy: Optional dialog policy dictionary (from load_dialog_policy).
                If None, loads policy automatically.
    
    Returns:
        Dictionary with:
            - intent: The input intent name
            - missing_slots: The input missing slots list
            - prompt: Advisory prompt text (advisory, no order enforced)
            - prompt_type: "advisory" (indicating the prompt is advisory)
            - slot_order: null (indicating no order is enforced)
    
    Example:
        >>> instructions = get_dialog_instructions("CREATE_APPOINTMENT", ["date", "time"])
        >>> instructions["prompt"]
        "What date and time would you like?"
        >>> instructions["slot_order"]
        None
    """
    # GUARD: Verify missing_slots is provided (not computed here)
    if not isinstance(missing_slots, list):
        raise ValueError(
            f"missing_slots must be a list (from planner), got {type(missing_slots)}. "
            f"This function is purely reactive and does not compute missing slots."
        )
    if policy is None:
        policy = load_dialog_policy()
    
    # Normalize intent name
    intent_upper = intent.upper() if intent else None
    
    # Get intent-specific prompts (list of prompt mappings or dict with default)
    intent_config = policy.get(intent_upper, {}) if intent_upper else {}
    
    # Normalize missing_slots to a set for comparison (order-independent)
    missing_slots_set = set(missing_slots)
    
    # Try to find exact match for missing_slots combination
    prompt = None
    
    # Handle dict format with prompts list and default
    if isinstance(intent_config, dict):
        # Check if it has a prompts list
        prompts_list = intent_config.get("prompts", [])
        if isinstance(prompts_list, list):
            for prompt_mapping in prompts_list:
                if isinstance(prompt_mapping, dict):
                    mapping_slots = prompt_mapping.get("missing_slots", [])
                    if set(mapping_slots) == missing_slots_set:
                        prompt = prompt_mapping.get("prompt")
                        break
        # If no exact match, try default for this intent
        if not prompt and "default" in intent_config:
            prompt = intent_config["default"]
    # Handle list format (legacy format - list of mappings)
    elif isinstance(intent_config, list):
        for prompt_mapping in intent_config:
            if isinstance(prompt_mapping, dict):
                mapping_slots = prompt_mapping.get("missing_slots", [])
                if set(mapping_slots) == missing_slots_set:
                    prompt = prompt_mapping.get("prompt")
                    break
    
    # Fallback to default intent prompt if no match found
    if not prompt:
        # Load full config to get default_intent_prompt
        config_dir = Path(__file__).resolve().parent.parent.parent / "config"
        config_path = str(config_dir / "dialog_policy.yaml")
        config_file = Path(config_path)
        if config_file.exists():
            with config_file.open(encoding="utf-8") as f:
                full_config = yaml.safe_load(f) or {}
                prompt = full_config.get("default_intent_prompt", "What information would you like to provide?")
        else:
            prompt = "What information would you like to provide?"
    
    return {
        "intent": intent,
        "missing_slots": missing_slots,
        "prompt": prompt,
        "prompt_type": "advisory",
        "slot_order": None  # No order enforced - users can provide slots in any order
    }

