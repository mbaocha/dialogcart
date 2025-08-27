from typing import Any, Dict, List, Optional
from agents.state import AgentState
from agents.utils import enforce_agent_state
from utils.coreutil import search_in_list  # your existing helper

def _parse_number_reply(text: str) -> Optional[int]:
    if not text:
        return None
    t = text.strip().lower()
    if t.isdigit():
        return int(t)
    if t.startswith("option "):
        rest = t.replace("option", "", 1).strip()
        return int(rest) if rest.isdigit() else None
    return None

@enforce_agent_state
def apply_disambiguation_selection(state: AgentState) -> AgentState:
    """
    Resolves the user's clarification reply to a single product_id.
    Accepts: numbered option, exact product id, or product name (via search_in_list).
    On success: restores tool_name, sets tool_args + tool_call_args, clears pending.
    On failure: keeps awaiting_user_clarification=True so the flow re-prompts.
    """
    pending = getattr(state, "pending_disambiguation", None) or {}
    if not pending:
        return state  # nothing to do

    user_text = (getattr(state, "user_input", "") or "").strip()
    tool_name = pending.get("tool_name")
    options: List[str] = pending.get("options") or []          # candidate product_ids
    args: Dict[str, Any] = dict(pending.get("args") or {})     # carry qty/unit/user_id etc.

    # catalog: prefer state.products (id -> name), fallback to args.products
    products: Dict[str, Any] = getattr(state, "products", {}) or args.get("products") or {}

    chosen_pid: Optional[str] = None

    # 1) number-based reply
    idx1 = _parse_number_reply(user_text)
    if idx1 is not None and 1 <= idx1 <= len(options):
        chosen_pid = options[idx1 - 1]

    # 2) exact id
    if not chosen_pid and user_text in products:
        chosen_pid = user_text

    # 3) search by name (fuzzy allowed)
    if not chosen_pid and user_text:
        matches = search_in_list(user_text, products, fallback_to_fuzzy=True) or []
        if len(matches) == 1:
            chosen_pid = matches[0]

    if not chosen_pid:
        # Keep waiting; the router should send us back to ask_disambiguation.
        return state.model_copy(update={
            "awaiting_user_clarification": True,
            "pending_disambiguation": pending,  # unchanged
        })

    # Fill args with resolved product
    args["product_id"] = chosen_pid
    # Canonicalize product_name if available (products maps id -> name in your logs)
    canon_name = products.get(chosen_pid)
    if isinstance(canon_name, str) and canon_name.strip():
        args["product_name"] = canon_name.strip()

    # Commit for next steps
    return state.model_copy(update={
        "tool_name": tool_name,       # restore execution target
        "tool_args": args,            # keep for traceability
        "tool_call_args": args,       # executable kwargs
        "awaiting_user_clarification": False,
        "pending_disambiguation": None,
    })
