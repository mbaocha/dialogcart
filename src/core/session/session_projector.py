"""SessionProjector — projects an execution/planning outcome onto session state.

Thin boundary used by the HTTP API layer over
``core.session.persist.build_session_state_from_outcome``.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


class SessionProjector:
    """Project an outcome onto a new session state for persistence."""

    def project(
        self,
        outcome: Dict[str, Any],
        outcome_status: str,
        merged_luma_response: Optional[Dict[str, Any]] = None,
        previous_session_state: Optional[Dict[str, Any]] = None,
        user_id: Optional[str] = None,
        session_store: Optional[Any] = None,
    ) -> Optional[Dict[str, Any]]:
        """Return a new session state derived from *outcome*.

        Parameters match build_session_state_from_outcome exactly.
        """
        from core.session.persist import build_session_state_from_outcome

        return build_session_state_from_outcome(
            outcome=outcome,
            outcome_status=outcome_status,
            merged_luma_response=merged_luma_response,
            previous_session_state=previous_session_state,
            user_id=user_id,
            session_store=session_store,
        )
