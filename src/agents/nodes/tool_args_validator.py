# agents/nodes/tool_args_validator.py
from __future__ import annotations
from typing import Any, Dict

from agents.state import AgentState
from agents.utils import enforce_agent_state
from features.validators import VALIDATOR_REGISTRY, ensure_products


# ----------------------------
# Default validator (for everything else)
# ----------------------------
def _default_validator(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Accepts everything as-is. Use specific validators only where needed.
    """
    return {
        "ok": True,
        "needs_clarification": False,
        "reason": None,
        "candidates": [],
        "product_id": None,
        "cleaned_args": dict(args),
    }


# ----------------------------
# Node entry
# ----------------------------
@enforce_agent_state
def tool_args_validator(state: AgentState) -> AgentState:
    print("[DEBUG] tool_args_validator -> tool_args: ", state.tool_args)
    tool_name = state.tool_name
    cleaned_args: Dict[str, Any] = dict(getattr(state, "tool_args", {}) or {})

    products = ensure_products(state)
    print("[DEBUG] tool_args_validator -> products: ", products)

    # Pick specific validator or fall back to default
    validator = VALIDATOR_REGISTRY.get(tool_name, _default_validator)

    # Add products to cleaned_args for validators to access
    cleaned_args['products'] = products

    # Run validation with single argument
    meta = validator(cleaned_args)

    # Update meta for traceability
    intent_meta = dict(getattr(state, "intent_meta", {}) or {})
    intent_meta["validated_tool"] = {
        "name": tool_name,
        **meta,  # ok, needs_clarification, reason, candidates, product_id, cleaned_args
    }

    if meta["needs_clarification"]:
        print(f"[DEBUG] tool_args_validator -> needs_clarification: {meta['needs_clarification']}, reason: {meta['reason']}")
        # Pause: keep normalized args, stop execution until user clarifies
        updated_state = state.model_copy(update={
            "tool_name": None,                 # halt tool execution
            "tool_args": meta["cleaned_args"], # preserve normalized intent args
            "tool_call_args": {},              # nothing executable yet
            "intent_meta": intent_meta,
            "awaiting_user_clarification": True,
        })
        print(f"[DEBUG] tool_args_validator -> returning state with awaiting_user_clarification: {getattr(updated_state, 'awaiting_user_clarification', 'NOT_SET')}")
        return updated_state

    # Happy path: provide executable kwargs
    return state.model_copy(update={
        "tool_call_args": meta["cleaned_args"],   # final sanitized kwargs for the tool
        "tool_args": meta["cleaned_args"],        # keep in-sync for traceability
        "intent_meta": intent_meta,
        "awaiting_user_clarification": False,
    })
