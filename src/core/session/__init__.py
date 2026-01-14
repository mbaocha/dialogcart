"""
Session Management Module

Redis-backed session storage for conversational state.
"""

# Import session_manager to trigger Redis validation at startup
import core.session.session_manager  # noqa: F401

from core.session.session_manager import (
    get_session,
    save_session,
    clear_session,
)

__all__ = [
    "get_session",
    "save_session",
    "clear_session",
]

