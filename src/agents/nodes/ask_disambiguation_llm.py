# agents/nodes/ask_disambiguation_llm.py
from typing import Any, Dict, List, Optional
from uuid import uuid4
from agents.state import AgentState
from agents.utils import enforce_agent_state

MAX_OPTIONS = 10

def _product_display(products: Dict[str, Any], pid: str) -> str:
    """
    Returns a nice display name. Works with either:
      - simple map: id -> "Name"
      - rich map:   id -> {name/title/product_name, brand, unit, size}
    """
    item = (products or {}).get(pid)
    if isinstance(item, dict):
        name = item.get("name") or item.get("title") or item.get("product_name") or pid
        extras = []
        if item.get("brand"): extras.append(item["brand"])
        if item.get("size"): extras.append(item["size"])
        if item.get("unit"): extras.append(item["unit"])
        suffix = f" ({', '.join(extras)})" if extras else ""
        return f"{name}{suffix}"
    return str(item) if isinstance(item, str) else pid

def _numbered_list(products: Dict[str, Any], candidate_ids: List[str]) -> str:
    lines = []
    for i, pid in enumerate(candidate_ids, start=1):
        lines.append(f"{i}. {_product_display(products, pid)}  —  id: {pid}")
    return "\n".join(lines)

@enforce_agent_state
def ask_disambiguation_llm_node(state: AgentState, llm) -> AgentState:
    """
    Uses the LLM to draft a short clarification blurb, but the options are strictly deterministic.
    """
    meta = dict(getattr(state, "intent_meta", {}) or {})
    vmeta = meta.get("validated_tool") or {}
    candidates: List[str] = vmeta.get("candidates") or []
    reason = vmeta.get("reason") or "ambiguous_catalog_item"
    tool_name = vmeta.get("name") or getattr(state, "tool_name", None)
    cleaned_args = vmeta.get("cleaned_args") or getattr(state, "tool_args", {}) or {}
    products: Dict[str, Any] = getattr(state, "products", {}) or {}


    # Cap & render options deterministically
    options = candidates[:MAX_OPTIONS]
    
    # Handle case when no candidates are found
    if not options and reason in {"catalog_item_not_found"}:
        # Show available products as suggestions when no matches found
        all_product_ids = list(products.keys())[:MAX_OPTIONS]
        options = all_product_ids
        choices_block = _numbered_list(products, options)
        
        # Build message for product/catalog not found case
        user_text = getattr(state, "user_input", "") or ""
        product_name = cleaned_args.get("catalog_name") or ""
        
        system_msg = (
            "You are Ella, a concise shopping assistant for Bulkpot.\n"
            "The user asked for an item that doesn't exist in our catalog. "
            "Write a SHORT (≤2 sentences) message explaining this and suggesting they choose from available products. "
            "Be helpful and direct. Do NOT invent items, prices, or details."
        )
        user_msg = (
            f"User said: {user_text!r}\n"
            f"Item they asked for: {product_name!r}\n"
            f"Reason: {reason}\n"
            "Write a brief message explaining the product isn't available and suggesting they choose from the available options below."
        )
        
        # Default fallback message
        blurb = f"I couldn't find '{product_name}' in our catalog. Here are some available options you might like:"
        
        # Try the LLM for nicer phrasing
        try:
            resp = llm.invoke([
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ])
            maybe_text = getattr(resp, "content", resp)
            if isinstance(maybe_text, str) and maybe_text.strip():
                blurb = maybe_text.strip().split("\n\n")[0][:500]
        except Exception as e:
            print(f"[WARN] ask_disambiguation_llm_node LLM failed: {type(e).__name__}: {e}")
        
        final_message = f"{blurb}\n\n{choices_block}\n\nReply with the **number**, the **catalog id**, or the **name**."
        
    else:
        # Original logic for when we have candidates
        choices_block = _numbered_list(products, options)

        # Build a small, safe prompt for the LLM
        user_text = getattr(state, "user_input", "") or ""
        product_name = cleaned_args.get("catalog_name") or ""
        system_msg = (
            "You are Ella, a concise shopping assistant for Bulkpot.\n"
            "Your task: write a SHORT (≤2 sentences) clarification message asking the user to pick one product "
            "from a candidate list that WILL be shown below. Do NOT invent items, prices, or details. "
            "Do NOT repeat the numbered options; we append them separately. Keep tone helpful and direct."
        )
        user_msg = (
            f"User said: {user_text!r}\n"
            f"Matched ambiguous product name: {product_name!r}\n"
            f"Reason: {reason}\n"
            "Write a brief clarification blurb (<=2 sentences) that asks the user to choose from the options below "
            "and tells them they can reply with the number, product id, or name."
        )

        # Make fallback message context-aware based on the tool
        if tool_name == "remove_item_from_cart":
            blurb = f"I found multiple {product_name} products in your cart. Which one would you like to remove?"
        elif tool_name == "add_item_to_cart":
            blurb = f"I found multiple {product_name} products. Which one would you like to add to your cart?"
        else:
            blurb = f"I found multiple {product_name} products. Which one did you mean?"
        # Try the LLM for nicer phrasing
        try:
            # Works with LangChain-style chat models: llm.invoke([SystemMessage, HumanMessage])
            resp = llm.invoke([
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ])
            # Accept either a string or an object with .content
            maybe_text = getattr(resp, "content", resp)
            if isinstance(maybe_text, str) and maybe_text.strip():
                # hard cap to avoid long rambles
                blurb = maybe_text.strip().split("\n\n")[0][:500]
        except Exception as e:
            # Fall back silently
            print(f"[WARN] ask_disambiguation_llm_node LLM failed: {type(e).__name__}: {e}")

        # Compose final message: LLM blurb + deterministic list + reply instruction
        final_message = f"{blurb}\n\n{choices_block}\n\nReply with the **number**, the **catalog id**, or the **name**."

    # Persist disambiguation context
    disamb_id = str(uuid4())
    pending = {
        "id": disamb_id,
        "tool_name": tool_name,
        "options": options,          # product_ids in order
        "args": cleaned_args,        # carry qty/unit/customer_id, etc.
        "reason": reason,
    }

    msgs = list(getattr(state, "messages", []) or [])
    msgs.append({"role": "assistant", "content": final_message})
    print(f"[DEBUG] ask_disambiguation_llm_node -> final_message: {final_message}")

    from langchain_core.messages import AIMessage
    msgs.append(AIMessage(content=final_message))


    updates = {
        "pending_disambiguation": pending,
        "awaiting_user_clarification": True,
        "intent_meta": {**meta, "pending_disambiguination_id": disamb_id},
        "tool_name": None,           # halt execution until resolved
        "tool_call_args": {},
        "messages": msgs,
        "display_output": [final_message],  # for your CLI renderer
    }
    return state.model_copy(update=updates)
