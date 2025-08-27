"""
Bulkpot Agent Graph - Main agent orchestration and conversation flow.
"""
from typing import Any, Dict, List
from langchain_core.messages import SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, END

from agents.state import AgentState
from agents.config import SYSTEM_MESSAGE, AMBIGUITY_SYSTEM_MESSAGE, DEFAULT_MODEL, FALLBACK_MODEL
from agents.llm_history import LLMHistoryManager
from agents.nodes.onboarding import onboarding_node
from agents.nodes.welcome import init_node, welcome_agent_node
from agents.nodes.format_output import format_output_llm
from agents.nodes.intent_classifier import intent_classifier
from agents.nodes.tool_args_builder import tool_args_builder
from agents.nodes.tool_args_validator import tool_args_validator
from agents.nodes.apply_disambiguation_selection import apply_disambiguation_selection
from agents.nodes.ask_disambiguation_llm import ask_disambiguation_llm_node
from agents.nodes.non_tool_llm import non_tool_handler_llm


# If your direct-call node lives here; adjust path if needed
from agents.nodes.tool_call import process_tool_call

# Discovered tools: list[{"name": str, "args": list[str], "func": callable}]
from agents.tools import TOOL_REGISTRY
from agents.nodes.route_gate import needs_tool

# Initialize tool registry (list of dicts)
tool_registry: List[Dict[str, Any]] = TOOL_REGISTRY
print("Available tools for agent:", [t["name"] for t in tool_registry])

# Initialize LLM with fallback (no tool binding for direct-call approach)
try:
    llm = ChatOpenAI(model=DEFAULT_MODEL)
except Exception:
    llm = ChatOpenAI(model=FALLBACK_MODEL)

# Initialize history manager
history_manager = LLMHistoryManager(SYSTEM_MESSAGE, llm)


def get_system_message(user_name: str | None = None, is_ambiguity: bool = False) -> SystemMessage:
    base = (AMBIGUITY_SYSTEM_MESSAGE.content if is_ambiguity else SYSTEM_MESSAGE.content)
    if user_name:
        base += f"\nYou are talking to: {user_name}"
    return SystemMessage(content=base)

# agents/nodes/router.py
from agents.state import AgentState
from agents.nodes.route_gate import needs_tool

def router_decider(state: AgentState) -> str:
    im = getattr(state, "intent_meta", {}) or {}
    branch = "TOOL" if needs_tool(im) else "NON_TOOL"
    print(f"[DEBUG] router_decider -> branch: {branch}, im: {im}")
    return branch



def build_graph(history_manager, llm, tool_registry, get_system_message):
    """Build and configure the agent graph."""
    graph = StateGraph(AgentState)

    # Nodes
    graph.add_node("init", init_node)
    graph.add_node("welcome", lambda s: welcome_agent_node(s, history_manager, llm, get_system_message))
    graph.add_node("onboarding", onboarding_node)
    graph.add_node("intent_classifier", intent_classifier)
    graph.add_node("tool_args_builder", tool_args_builder)
    graph.add_node("tool_args_validator", tool_args_validator)
    graph.add_node("non_tool", non_tool_handler_llm)


    graph.add_node("ask_disambiguation", lambda s: ask_disambiguation_llm_node(s, llm))
    graph.add_node("apply_disambiguation_selection", apply_disambiguation_selection)

    # Direct tool call node; we pass the registry so it can look up {"name","args","func"}
    graph.add_node("tool_call", process_tool_call)

    graph.add_node("format_output", lambda s: format_output_llm(s, history_manager, llm, get_system_message))

    graph.set_entry_point("init")

    # ---- Entry routing (every turn starts here) ----
    # If clarification pending, apply it first; else normal flow.
    graph.add_conditional_edges(
        "init",
        lambda s:
            "end" if getattr(s, "is_disabled", False) else
            "onboarding" if not getattr(s, "is_registered", False) else
            ("apply_disambiguination_selection" if getattr(s, "pending_disambiguination", None) else  # backward-compat guard
             ("apply_disambiguation_selection" if getattr(s, "pending_disambiguation", None) else
              ("welcome" if (getattr(s, "previous_message_count", 0) == 0 or getattr(s, "just_registered", False))
               else "intent_classifier"))),
        {
            "onboarding": "onboarding",
            "welcome": "welcome",
            "intent_classifier": "intent_classifier",
            "apply_disambiguination_selection": "apply_disambiguation_selection",  # typo-safe mapping
            "apply_disambiguation_selection": "apply_disambiguation_selection",
            "end": END,
        }
    )

    # After onboarding, go to welcome once
    graph.add_conditional_edges(
        "onboarding",
        lambda s: "end" if not getattr(s, "is_registered", False) else "welcome",
        {"welcome": "welcome", "end": END}
    )

    # Normal path after welcome
    graph.add_edge("welcome", "intent_classifier")

    graph.add_conditional_edges(
    "intent_classifier",
    router_decider,
    {
        "NON_TOOL": "non_tool",
        "TOOL": "tool_args_builder",
    },
    )

    graph.add_edge("non_tool", END)
    graph.add_edge("format_output", END)


    # Intent → args → validate
    graph.add_edge("tool_args_builder", "tool_args_validator")

    # Route after validation
    def route_after_validation(state: AgentState):
        awaiting = getattr(state, "awaiting_user_clarification", False)
        pending = bool(getattr(state, "pending_disambiguation", None))
        print(f"[DEBUG] route_after_validation -> awaiting={awaiting}, pending={pending}")
        return "ASK" if awaiting or pending else "CALL"

    graph.add_conditional_edges(
        "tool_args_validator",
        route_after_validation,
        {"ASK": "ask_disambiguation", "CALL": "tool_call"}
    )

    # Ask ends the tick (wait for user)
    graph.add_edge("ask_disambiguation", END)

    # On the next user message, if we were clarifying, apply selection first
    graph.add_conditional_edges(
        "apply_disambiguation_selection",
        lambda s: "ASK" if getattr(s, "awaiting_user_clarification", False) else "VALIDATE",
        {"ASK": "ask_disambiguation", "VALIDATE": "tool_args_validator"},
    )

    # Tool result → format → END
    graph.add_edge("tool_call", "format_output")
    graph.add_edge("format_output", END)

    return graph.compile()


# Build the graph
app = build_graph(history_manager, llm, tool_registry, get_system_message)
