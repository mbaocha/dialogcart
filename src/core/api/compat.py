"""Temporary application-entrypoint compatibility helpers.

Prefer ConversationEngine.process_turn() for new code.
``handle_message`` preserves the historical three-fallback session load
and delegates to the engine.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, Optional

from core.adapters.clients.organization_client import OrganizationClient
from core.adapters.nlu import LumaClient

logger = logging.getLogger(__name__)


def handle_message(
    text: str,
    user_id: str,
    luma_client: Optional[LumaClient] = None,
    availability_client: Optional[Any] = None,
    organization_client: Optional[OrganizationClient] = None,
    session_store: Optional[Any] = None,
    frozen_time: Optional[datetime] = None,
    organization_id: Optional[int] = None,
    **kwargs,  # Backward-compat shim: ignore unknown infra parameters (e.g., customer_id)  # noqa: ARG001
) -> Dict[str, Any]:
    """Compatibility wrapper around ConversationEngine.process_turn()."""
    session_state = None
    if session_store is not None:
        try:
            if hasattr(session_store, "get_session"):
                session_state = session_store.get_session(user_id)
            elif callable(session_store):
                session_state = session_store(user_id)
        except Exception as e:
            logger.warning(f"Failed to get session for user {user_id}: {e}")

    if session_state is None and "session_state" in kwargs:
        session_state = kwargs["session_state"]

    kwargs.pop("session_state", None)
    kwargs.pop("domain", None)

    if session_state is None:
        try:
            from core.session.session_manager import get_session

            session_state = get_session(user_id)
            if session_state:
                logger.debug(
                    f"[SESSION_FALLBACK] Loaded session from default store for user_id={user_id} "
                    f"(session_store was None, kwargs.session_state was None or filtered out)"
                )
        except (ImportError, Exception) as e:
            logger.debug(
                f"[SESSION_FALLBACK] Could not load from default session store: {e}"
            )

    from core.engine.conversation_engine import ConversationEngine

    engine = ConversationEngine()
    return engine.process_turn(
        text=text,
        user_id=user_id,
        session_state=session_state,
        availability_client=availability_client,
        organization_client=organization_client,
        session_store=session_store,
        frozen_time=frozen_time,
        organization_id=organization_id,
        luma_client=luma_client,
        **kwargs,
    )
