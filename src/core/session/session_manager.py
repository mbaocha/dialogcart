"""SessionManager — Phase 1 architectural boundary for session I/O.

Initially wraps core/orchestration/session/session_manager.py.
Phase 2 will consolidate all session I/O through this class.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


class SessionManager:
    """Load, save, and clear sessions.

    Phase 1: thin facade over the existing session store in
             core.orchestration.session.session_manager.
    """

    def load(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Return the persisted session for *user_id*, or None if no session exists."""
        from core.orchestration.session.session_manager import get_session

        return get_session(user_id)

    def save(self, user_id: str, session_state: Dict[str, Any]) -> None:
        """Persist *session_state* for *user_id*."""
        from core.orchestration.session.session_manager import save_session

        save_session(user_id, session_state)

    def clear(self, user_id: str) -> None:
        """Remove the session for *user_id*."""
        from core.orchestration.session.session_manager import clear_session

        clear_session(user_id)
