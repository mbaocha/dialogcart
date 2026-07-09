"""session_ops — neutral utility for persisting individual keys to the session store.

Extracted from orchestrator.py to break any potential circular imports with
workflow modules that also need to persist session keys.

Re-exported from orchestrator.py for backward compatibility.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _persist_to_session(
    session_store: Optional[Any],
    user_id: str,
    current_session: Dict[str, Any],
    key: str,
    value: Any,
) -> Dict[str, Any]:
    """Write key=value into current_session and save to store if available.

    Refreshes current_session from the store first so we don't clobber
    concurrent writes.  Returns the (possibly refreshed) session dict.
    """
    if session_store is not None:
        try:
            if hasattr(session_store, "get_session"):
                current_session = session_store.get_session(user_id) or current_session
            elif callable(session_store):
                current_session = session_store(user_id) or current_session
        except Exception as e:
            logger.debug(
                "Failed to refresh session before persisting %s: %s", key, e
            )

    current_session[key] = value

    if session_store is not None:
        try:
            if hasattr(session_store, "save_session"):
                session_store.save_session(user_id, current_session)
            elif hasattr(session_store, "save"):
                session_store.save(user_id, current_session)
        except Exception as e:
            logger.warning("Failed to persist %s to session_store: %s", key, e)

    return current_session
