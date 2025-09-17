# agents/nodes/ask_disambiguation.py
from typing import Any, Dict, List, Optional
from uuid import uuid4

from agents.state import AgentState
from agents.utils import enforce_agent_state

MAX_OPTIONS = 10  # keep UI sane

def _product_display(products: Dict[str, str], pid: str) -> str:
    """
    Returns a human-friendly name for a product id.
    The products dict maps catalog_id -> name.
    """
    product_name = (products or {}).get(pid, pid)
    return product_name

@enforce_agent_state
def ask_disambiguation_node(state: AgentState) -> AgentState:
    """
    Builds a clarification prompt when validator flagged ambiguity / not-found / missing name.
    Writes a 'pending_disambiguation' object to state and adds a friendly question to messages (if present)
    or to a 'followup_question' field for your UI to render.
    """
    meta = dict(getattr(state, "intent_meta", {}) or {})
    vmeta = (meta.get("validated_tool") or {})
    candidates: List[str] = vmeta.get("candidates") or []
    reason = vmeta.get("reason")
    tool_name = vmeta.get("name")
    cleaned_args = vmeta.get("cleaned_args") or {}
    products: Dict[str, Any] = getattr(state, "products", {}) or cleaned_args.get("products") or {}

    # Prepare numbered options
    options = candidates[:MAX_OPTIONS]
    numbered = [f"{i+1}. {_product_display(products, pid)}  —  id: {pid}" for i, pid in enumerate(options)]

    # Build the question text
    if reason == "ambiguous_catalog_item":
        header = "I found multiple matches for that product. Which one did you mean?"
    elif reason in {"catalog_item_not_found"}:
        header = "I couldn’t find an exact match. Did you mean one of these?"
    elif reason in {"missing_catalog_name"}:
        header = "Which item would you like?"
    else:
        header = "Could you clarify which item you meant?"

    question = header
    if numbered:
        question += "\n\n" + "\n".join(numbered)
        question += "\n\nReply with the **number**, the **catalog id**, or the **name**."

    # Persist disambiguation context so the next user message can resolve it
    disamb_id = str(uuid4())
    pending = {
        "id": disamb_id,
        "tool_name": tool_name,
        "options": options,          # list of product_ids
        "args": cleaned_args,        # keep qty/unit/etc.
        "reason": reason,
    }

    updates = {
        "pending_disambiguation": pending,
        "awaiting_user_clarification": True,
        "intent_meta": {**meta, "pending_disambiguation_id": disamb_id},
    }

    # If you keep a messages list in state, append an assistant turn; otherwise set followup_question
    msgs = list(getattr(state, "messages", []) or [])
    if msgs is not None:
        msgs.append({"role": "assistant", "content": question})
        updates["messages"] = msgs
    else:
        updates["followup_question"] = question

    # Ensure we don't accidentally execute a tool while waiting
    updates["tool_name"] = None

    print("[DEBUG] ask_disambiguation_node -> updates: ", updates)

    return state.model_copy(update=updates)
