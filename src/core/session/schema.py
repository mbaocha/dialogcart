"""Session state shape helpers and debug gating."""

import json
import logging
import os
from typing import Any, Dict

logger = logging.getLogger(__name__)


def debug_persistence_enabled() -> bool:
    return os.getenv("DEBUG_SESSION_PERSISTENCE") == "1" or bool(
        os.getenv("PYTEST_CURRENT_TEST")
    )


def ensure_facts_dict(session_state: Dict[str, Any]) -> None:
    """Ensure session_state['facts'] is a dict (mutates in place)."""
    if "facts" not in session_state:
        session_state["facts"] = {}
    elif session_state["facts"] is None or not isinstance(session_state["facts"], dict):
        session_state["facts"] = {}


def ensure_slot_attempts_dict(session_state: Dict[str, Any]) -> None:
    """Ensure session_state['slot_attempts'] is a dict (mutates in place)."""
    session_state.setdefault("slot_attempts", {})
    if session_state["slot_attempts"] is None or not isinstance(
        session_state["slot_attempts"], dict
    ):
        session_state["slot_attempts"] = {}


def normalize_session_guards(session_state: Dict[str, Any]) -> None:
    """Apply all required session dict shape guards."""
    ensure_facts_dict(session_state)
    ensure_slot_attempts_dict(session_state)
    assert isinstance(session_state["facts"], dict)
    assert isinstance(session_state["slot_attempts"], dict)


def filter_serializable_facts(facts: Dict[str, Any]) -> Dict[str, Any]:
    """Return facts dict containing only JSON-serializable values."""
    if not facts or not isinstance(facts, dict):
        return {}
    try:
        json.dumps(facts, default=str)
        return facts
    except (TypeError, ValueError):
        serializable: Dict[str, Any] = {}
        for key, value in facts.items():
            try:
                json.dumps(value, default=str)
                serializable[key] = value
            except (TypeError, ValueError):
                logger.warning(
                    "[FACTS_PERSISTENCE] Skipping non-serializable fact key: %s", key
                )
        return serializable


def debug_log(message: str, *args: Any) -> None:
    if debug_persistence_enabled():
        logger.debug(message, *args)
