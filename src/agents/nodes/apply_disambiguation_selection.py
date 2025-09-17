from typing import Any, Dict, List, Optional
from agents.state import AgentState
from agents.utils import enforce_agent_state
from utils.coreutil import search_in_list  # your existing helper

def _parse_number_reply(text: str) -> Optional[int]:
    if not text:
        return None
    t = text.strip().lower()
    
    # Pure number
    if t.isdigit():
        return int(t)
    
    # "option X" format
    if t.startswith("option "):
        rest = t.replace("option", "", 1).strip()
        return int(rest) if rest.isdigit() else None
    
    # Extract number from phrases like "add 2 to cart", "choose 3", "select 1", etc.
    import re
    # Look for patterns like "add 2 to cart", "choose 3", "select 1", "pick 2", etc.
    patterns = [
        r'add\s+(\d+)\s+to\s+cart',
        r'choose\s+(\d+)',
        r'select\s+(\d+)',
        r'pick\s+(\d+)',
        r'option\s+(\d+)',
        r'(\d+)\s+to\s+cart',
        r'(\d+)\s+please',
        r'(\d+)\s+thanks',
        r'(\d+)\s+thank\s+you',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, t)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                continue
    
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
    options: List[str] = pending.get("options") or []          # candidate catalog_ids
    args: Dict[str, Any] = dict(pending.get("args") or {})     # carry qty/unit/customer_id etc.

    print(f"[DEBUG] apply_disambiguation_selection -> user_text: '{user_text}'")
    print(f"[DEBUG] apply_disambiguation_selection -> options: {options}")
    print(f"[DEBUG] apply_disambiguation_selection -> tool_name: {tool_name}")

    # catalog: prefer state.products (id -> name)
    products: Dict[str, Any] = getattr(state, "products", {}) or {}

    chosen_pid: Optional[str] = None

    # 1) number-based reply
    idx1 = _parse_number_reply(user_text)
    print(f"[DEBUG] apply_disambiguation_selection -> parsed number: {idx1}")
    if idx1 is not None and 1 <= idx1 <= len(options):
        chosen_pid = options[idx1 - 1]
        print(f"[DEBUG] apply_disambiguation_selection -> selected catalog_id: {chosen_pid}")

    # 2) exact id
    if not chosen_pid and user_text in products:
        chosen_pid = user_text

    # 3) search by name (fuzzy allowed)
    if not chosen_pid and user_text:
        matches = search_in_list(user_text, products, fallback_to_fuzzy=True) or []
        if len(matches) == 1:
            chosen_pid = matches[0]

    # 3b) reverse contains: if the user's text contains a known product name
    if not chosen_pid and user_text and products:
        text_lower = user_text.lower()
        contains_matches = [pid for pid, name in products.items()
                            if isinstance(name, str) and name.lower() in text_lower]
        print(f"[DEBUG] apply_disambiguation_selection -> reverse contains matches: {contains_matches}")
        if len(contains_matches) == 1:
            chosen_pid = contains_matches[0]

    if not chosen_pid:
        print("[DEBUG] apply_disambiguation_selection -> no product chosen, keeping awaiting_user_clarification=True")
        # Keep waiting; the router should send us back to ask_disambiguation.
        return state.model_copy(update={
            "awaiting_user_clarification": True,
            "pending_disambiguation": pending,  # unchanged
        })

    # Fill args with resolved catalog id
    args["catalog_id"] = chosen_pid
    # Canonicalize catalog_name if available (products maps id -> name)
    canon_name = products.get(chosen_pid)
    if isinstance(canon_name, str) and canon_name.strip():
        canon_name = canon_name.strip()
        args["catalog_name"] = canon_name

    print(f"[DEBUG] apply_disambiguation_selection -> resolved to product: {canon_name} (id: {chosen_pid})")
    print(f"[DEBUG] apply_disambiguation_selection -> restored tool_name: {tool_name}")

    # Commit for next steps
    return state.model_copy(update={
        "tool_name": tool_name,       # restore execution target
        "tool_args": args,            # keep for traceability
        "tool_call_args": args,       # executable kwargs
        "awaiting_user_clarification": False,
        "pending_disambiguation": None,
    })
