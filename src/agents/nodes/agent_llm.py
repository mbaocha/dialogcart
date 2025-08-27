"""
Agent LLM node for processing user input and generating agent responses.
"""

import json
import re

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from agents.state import AgentState
from agents.utils import enforce_agent_state
from agents.llm_history import LLMHistoryManager


def _requires_tool(user_text: str) -> bool:
    """
    Cheap guard (not a second classifier!):
    If the user asks to show/list/search products, or to add/remove/clear/view cart,
    we must use tools. This prevents free-typed, potentially hallucinated answers.
    """
    if not user_text:
        return False

    # Catalog / browse / search vibes
    if re.search(r"\b(show|list|browse|see|view|display|explore|catalog(?:ue)?|inventory|products?)\b", user_text, re.I):
        return True

    # Cart actions
    if re.search(r"\b(add|put|include|insert|place|throw|stick|drop|remove|delete|update|change|modify|clear)\b", user_text, re.I):
        return True
    if re.search(r"\b(cart|basket|bag|trolley)\b", user_text, re.I):
        return True

    return False


@enforce_agent_state
def call_agent(state: AgentState, history_manager: LLMHistoryManager, llm, get_system_message) -> AgentState:
    """Process user input and generate agent response."""
    all_time_history = list(state.all_time_history)
    existing_messages = list(state.messages)
    user_input = state.user_input
    raw_user_input = user_input  # keep an unmodified copy for guards
    user_profile = state.user_profile
    user_name = user_profile.get('name', 'Customer')

    # Check if this is an ambiguity resolution scenario
    is_ambiguity = (
        getattr(state, "force_llm_path", False)
        and state.metadata
        and state.metadata.get("match_count", 0) > 1
    )

    system_msg = get_system_message(user_name, is_ambiguity=is_ambiguity)

    # If ambiguity, add the product matches to the user input context (server-provided list only)
    if is_ambiguity and state.metadata and state.metadata.get("products"):
        products_info = state.metadata["products"]
        products_lines = []
        for i, product in enumerate(products_info, 1):
            name = product.get('name', '')
            price = product.get('price', '')
            unit = product.get('unit', '')
            avail = product.get('available_quantity', '')
            products_lines.append(f"{i}. {name} - ${price}/{unit} (available: {avail})")
        products_text = "\n".join(products_lines)

        # Add products info to the user input for context
        if user_input:
            user_input = f"{user_input}\n\nMATCHING PRODUCTS FOUND:\n{products_text}\n\nPlease reply with the product name and quantity (e.g., 'Stockfish 2 boxes')."
        else:
            # If no user input, create a context message
            context_msg = HumanMessage(
                content=f"User needs to choose from these products:\n{products_text}\n\nPlease reply with the product name and quantity (e.g., 'Stockfish 2 boxes')."
            )
            existing_messages.append(context_msg)

    print(f"[DEBUG] call_agent user_input={user_input}")

    # Step 1: Build message list
    if user_input:
        new_message = HumanMessage(content=user_input)
        updated_messages = existing_messages + [new_message]
        messages_with_system = [system_msg] + updated_messages
    else:
        messages_with_system = [system_msg] + existing_messages

    # Step 2: First LLM response
    response = history_manager.invoke(
        llm, messages_with_system, config={"configurable": {"user_profile": user_profile}}, state=state
    )

    # Step 3: Extract tool call info (if any)
    def _extract_tool(response_msg: AIMessage):
        tool_name_local = None
        tool_args_local = {}
        if isinstance(response_msg, AIMessage):
            tool_calls = getattr(response_msg, "tool_calls", None)
            if not tool_calls:
                tool_calls = response_msg.additional_kwargs.get("tool_calls", [])  # type: ignore[attr-defined]
            if tool_calls:
                call = tool_calls[0]
                tool_name_local = call.get("name") or call.get("function", {}).get("name")
                tool_args = call.get("args") or call.get("function", {}).get("arguments") or {}
                if isinstance(tool_args, str):
                    try:
                        tool_args = json.loads(tool_args)
                    except json.JSONDecodeError:
                        print(f"[WARNING] Failed to parse tool arguments: {tool_args}")
                        tool_args = {}
                tool_args_local = tool_args
        return tool_name_local, tool_args_local

    tool_name, tool_args = _extract_tool(response)

    # --- New: Enforce "Agent must pick a tool" for catalog/cart-y prompts ---
    must_use_tool = _requires_tool(raw_user_input)
    if must_use_tool and not tool_name:
        print("[DEBUG] No tool call returned for a tool-required request. Forcing a strict retry.")
        strict_constraint = SystemMessage(
            content=(
                "CRITICAL: For this request, you MUST choose exactly ONE tool call. "
                "Do not answer in prose. Do not include any text outside the tool call."
            )
        )
        # Retry once with a strict system constraint appended
        response_retry = history_manager.invoke(
            llm,
            messages_with_system + [strict_constraint],
            config={"configurable": {"user_profile": user_profile}},
            state=state,
        )
        retry_tool_name, retry_tool_args = _extract_tool(response_retry)

        if retry_tool_name:
            # Use the retry response if it produced a valid tool call
            response = response_retry
            tool_name, tool_args = retry_tool_name, retry_tool_args
        else:
            # Final guard: block hallucinated prose
            print("[DEBUG] Strict retry still produced no tool call. Emitting guard message.")
            guard_msg = AIMessage(content="I need to use my tools to do that. Please try again.")
            updated_messages = existing_messages + ([HumanMessage(content=user_input)] if user_input else []) + [response, guard_msg]
            update_payload = {
                "messages": updated_messages,
                "all_time_history": all_time_history + [response, guard_msg],
                "user_input": "",
                "tool_name": None,
            }
            # Clear ambiguity context to prevent it from affecting future commands
            if is_ambiguity:
                update_payload["metadata"] = {}
                update_payload["force_llm_path"] = False

            new_state = state.model_copy(update=update_payload)
            history_manager.maybe_summarize(state.all_time_history, state)
            return new_state

    print(f"[DEBUG] call_agent extracted tool_name={tool_name}, tool_args={tool_args}")

    # Step 4: Update AgentState
    updated_messages = existing_messages + ([HumanMessage(content=user_input)] if user_input else []) + [response]

    # Clear ambiguity context after LLM responds (whether it resolved it or not)
    update_payload = {
        "messages": updated_messages,
        "all_time_history": all_time_history + [response],
        "user_input": "",
        "tool_name": tool_name,
        "tool_args": tool_args,
    }

    # Clear ambiguity context to prevent it from affecting future commands
    if is_ambiguity:
        update_payload["metadata"] = {}
        update_payload["force_llm_path"] = False

    new_state = state.model_copy(update=update_payload)
    history_manager.maybe_summarize(state.all_time_history, state)

    return new_state
