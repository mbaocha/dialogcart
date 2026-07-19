"""In-memory session store for multi-turn test scenarios."""

from typing import Any, Dict, Optional


class MockSessionStore:
    """Simple session store that stores session state in memory."""

    def __init__(self):
        self.sessions: Dict[tuple[int, str], Dict[str, Any]] = {}

    def get_session(
        self, organization_id: int, user_id: str
    ) -> Optional[Dict[str, Any]]:
        return self.sessions.get((organization_id, user_id))

    def save_session(
        self, organization_id: int, user_id: str, session_state: Dict[str, Any]
    ) -> None:
        self.sessions[(organization_id, user_id)] = session_state
