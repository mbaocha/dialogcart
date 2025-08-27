"""
Agent-specific utility functions.
"""

from functools import wraps
from typing import Callable


def enforce_agent_state(func: Callable) -> Callable:
    """Decorator to enforce AgentState return type and handle errors."""
    @wraps(func)
    def wrapper(state, *args, **kwargs):
        from agents.state import AgentState
        try:
            result = func(state, *args, **kwargs)
            if isinstance(result, AgentState):
                return result
            if isinstance(result, dict):
                return AgentState(**result)
            raise TypeError(f"Node {func.__name__} must return AgentState, got {type(result)}")
        except Exception as e:
            raise
    return wrapper