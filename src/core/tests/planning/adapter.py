"""
Planning Test Adapter

Normalizes CoreOutcome from handle_message into a stable, semantic planning view
for planning tests. This adapter isolates planning tests from internal CoreOutcome
structure changes.

Usage:
    result = handle_message(...)
    normalized = normalize_planning_outcome(result)
    assert normalized["intent"] == "CREATE_APPOINTMENT"
    assert normalized["missing_slots"] == ["date", "time"]
"""

from typing import Any, Dict, List, Optional


def normalize_planning_outcome(core_result: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize CoreOutcome from handle_message into a stable planning view.

    This function extracts planning-relevant data from the CoreOutcome structure
    and presents it in a stable, semantic format that planning tests can assert against.

    The normalized structure contains ONLY planning data:
    - Intent name
    - Planning status (READY, NEEDS_CLARIFICATION, AWAITING_CONFIRMATION)
    - Required slots (derived from missing_slots + collected slots)
    - Missing slots (from planning logic)
    - Collected slots
    - Plan stage (e.g., AVAILABILITY, CONFIRM, IDENTIFY)
    - Plan action (e.g., SEARCH_AVAILABILITY, CONFIRM_APPOINTMENT)
    - Executable actions (if present)

    Args:
        core_result: Result dictionary from handle_message with structure:
            {
                "success": bool,
                "result": plan_dict,  # or execution_result
                "plan": plan_dict,     # optional, present if execution occurred
                "outcome": outcome_dict,  # optional, legacy format
                "_merged_luma_response": dict  # optional
            }

    Returns:
        Normalized planning outcome dictionary:
        {
            "intent": str,
            "status": str,
            "required_slots": List[str],
            "missing_slots": List[str],
            "slots": Dict[str, Any],
            "plan": {
                "stage": str,
                "action": str,
                "executable_actions": List[str]  # optional
            }
        }

    Raises:
        ValueError: If core_result is invalid or missing required fields
    """
    if not isinstance(core_result, dict):
        raise ValueError(f"core_result must be a dict, got {type(core_result)}")

    if not core_result.get("success", False):
        # Error case - return minimal structure
        error_msg = core_result.get("error", "Unknown error")
        return {
            "intent": None,
            "status": "ERROR",
            "required_slots": [],
            "missing_slots": [],
            "slots": {},
            "plan": {"stage": None, "action": None},
            "error": error_msg,
        }

    # Extract plan from multiple possible locations
    # Priority: 1) result.get("plan") (if execution occurred), 2) result.get("result") (planning-only), 3) result.get("outcome") (legacy)
    # When execution occurs, "plan" contains the planning data, "result" contains execution result
    # When no execution, "result" contains the plan
    plan_data = core_result.get("plan")
    if plan_data is None:
        plan_data = core_result.get("result")
    if plan_data is None:
        plan_data = core_result.get("outcome", {})

    if not isinstance(plan_data, dict):
        plan_data = {}

    # Extract intent - check multiple possible locations
    intent_name = plan_data.get("intent_name") or plan_data.get("intent") or None

    # Extract status - from planning status, not execution status
    status = plan_data.get("status")
    if status is None:
        # Infer from missing_slots: if missing_slots is empty, likely READY
        missing_slots = plan_data.get("missing_slots", [])
        if isinstance(missing_slots, list) and len(missing_slots) == 0:
            status = "READY"
        else:
            status = "NEEDS_CLARIFICATION"

    # Extract missing_slots - from planning logic only
    missing_slots = plan_data.get("missing_slots", [])
    if not isinstance(missing_slots, list):
        # Fallback: check facts.missing_slots
        facts = plan_data.get("facts", {})
        if isinstance(facts, dict):
            missing_slots = facts.get("missing_slots", [])
        if not isinstance(missing_slots, list):
            missing_slots = []

    ask_next = plan_data.get("ask_next")
    if ask_next is None:
        facts = plan_data.get("facts", {})
        if isinstance(facts, dict):
            ask_next = facts.get("ask_next")
    if ask_next is None:
        plan_dict = plan_data.get("plan", {})
        if isinstance(plan_dict, dict):
            ask_next = plan_dict.get("ask_next")

    # Extract slots - from planning logic only
    slots = plan_data.get("slots", {})
    if not isinstance(slots, dict):
        # Fallback: check facts.slots
        facts = plan_data.get("facts", {})
        if isinstance(facts, dict):
            slots = facts.get("slots", {})
        if not isinstance(slots, dict):
            slots = {}

    date_proposal = plan_data.get("date_proposal")
    time_proposal = plan_data.get("time_proposal")
    facts_obj = plan_data.get("facts", {})
    if isinstance(facts_obj, dict):
        if date_proposal is None:
            date_proposal = facts_obj.get("date_proposal")
        if time_proposal is None:
            time_proposal = facts_obj.get("time_proposal")

    # Derive required_slots from missing_slots + collected slots
    required_slots = list(set(list(slots.keys()) + missing_slots))
    required_slots.sort()  # Deterministic ordering

    # Extract stage - check multiple locations
    stage = (
        plan_data.get("stage") or (plan_data.get("plan", {}) or {}).get("stage") or None
    )

    # Extract action - check multiple locations
    action = (
        plan_data.get("action")
        or (plan_data.get("plan", {}) or {}).get("action")
        or None
    )

    # Extract executable_actions - optional
    executable_actions = plan_data.get("executable_actions", [])
    if not isinstance(executable_actions, list):
        plan_dict = plan_data.get("plan", {})
        if isinstance(plan_dict, dict):
            executable_actions = plan_dict.get("executable_actions", [])
        if not isinstance(executable_actions, list):
            executable_actions = []

    # Build normalized structure
    normalized = {
        "intent": intent_name,
        "status": status,
        "required_slots": required_slots,
        "missing_slots": list(missing_slots),  # Preserve planning policy order
        "ask_next": ask_next,
        "slots": slots,
        "plan": {"stage": stage, "action": action},
    }
    if date_proposal is not None:
        normalized["date_proposal"] = date_proposal
    if time_proposal is not None:
        normalized["time_proposal"] = time_proposal

    # Add executable_actions if present
    if executable_actions:
        normalized["plan"]["executable_actions"] = sorted(executable_actions)

    return normalized


def normalize_for_assertion(
    core_result: Dict[str, Any], expected: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Normalize CoreOutcome and prepare for assertion against expected values.

    This is a convenience wrapper that:
    1. Normalizes the core_result
    2. Extracts only the fields that are expected
    3. Returns a dict suitable for direct comparison

    Args:
        core_result: Result from handle_message
        expected: Expected outcome dict with keys like "intent", "stage", "action", etc.

    Returns:
        Dict with only the keys present in expected, normalized for comparison
    """
    normalized = normalize_planning_outcome(core_result)

    # Build assertion dict with only expected keys
    assertion_dict = {}

    if "intent" in expected:
        assertion_dict["intent"] = normalized["intent"]

    if "status" in expected:
        assertion_dict["status"] = normalized["status"]

    if "stage" in expected:
        assertion_dict["stage"] = normalized["plan"]["stage"]

    if "action" in expected:
        assertion_dict["action"] = normalized["plan"]["action"]

    if "missing_slots" in expected:
        assertion_dict["missing_slots"] = normalized["missing_slots"]

    if "slots" in expected:
        assertion_dict["slots"] = normalized["slots"]

    if "executable_actions" in expected:
        assertion_dict["executable_actions"] = normalized["plan"].get(
            "executable_actions", []
        )

    return assertion_dict
