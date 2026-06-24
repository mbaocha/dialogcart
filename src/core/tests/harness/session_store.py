"""In-memory session store for multi-turn test scenarios."""

from typing import Any, Dict, Optional


class MockSessionStore:
    """Simple session store that stores session state in memory."""

    def __init__(self):
        self.sessions: Dict[str, Dict[str, Any]] = {}

    def get_session(self, user_id: str) -> Optional[Dict[str, Any]]:
        return self.sessions.get(user_id)

    def save_session(self, user_id: str, session_state: Dict[str, Any]) -> None:
        self.sessions[user_id] = session_state
