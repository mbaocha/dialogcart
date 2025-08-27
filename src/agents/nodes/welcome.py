"""
Welcome node for initializing agent state and generating welcome messages.
"""

from langchain_core.messages import SystemMessage

from agents.state import AgentState
from agents.utils import enforce_agent_state


@enforce_agent_state
def init_node(state: AgentState) -> AgentState:
    """Initialize the agent state."""
    return state


@enforce_agent_state
def welcome_agent_node(state: AgentState, history_manager, llm, get_system_message) -> AgentState:
    """Generate welcome message for new or returning users."""
    user_profile = state.user_profile
    just_registered = state.just_registered
    user_name = user_profile.get('name', 'Customer')
    system_msg = get_system_message(user_name)
    
    prompt = (
        "The user just completed registration. Send a warm welcome message and ask how you can help."
        if just_registered else
        "The user is returning. Greet them with a welcome back message and ask how you can help."
    )
    response = history_manager.invoke(llm, [system_msg, SystemMessage(content=prompt)], state=state)
    new_messages = list(state.messages) + [response]
    result = state.model_copy(update={
        "messages": new_messages,
        "has_seen_welcome": True,
        "just_registered": False
    })
    return result 