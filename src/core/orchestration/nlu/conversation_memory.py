"""
Conversation memory helpers for NLU context passing.

Stores the last 3 turns under session["conversation"] so that follow-up messages
("and for groups?", "how long is it?") can be resolved by NLU without the
orchestrator needing to touch the booking kernel.

Schema (session["conversation"]):
    {
        "last_intent":      str | None,   # intent from the previous turn
        "last_search_query": str | None,  # search_query from the previous turn
        "turns": [                        # prior turns, max 3, FIFO
            {
                "user":         str,
                "intent":       str,
                "search_query": str | None,
                "assistant":    str,       # optional
            }
        ]
    }
"""
from typing import Any, Dict, List, Optional


def build_conversation_context(
    session: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Return the NLU conversation_context dict from session, or None.

    Returns None when session is absent, has no "conversation" key, or the
    stored conversation carries no useful data (empty turns, no intent/query).
    """
    if not session or not isinstance(session, dict):
        return None
    conv = session.get("conversation")
    if not conv or not isinstance(conv, dict):
        return None
    has_data = (
        conv.get("last_intent")
        or conv.get("last_search_query")
        or conv.get("turns")
    )
    return conv if has_data else None


def update_conversation(
    session: Dict[str, Any],
    *,
    user_text: str,
    intent: str,
    search_query: Optional[str],
    assistant_text: Optional[str] = None,
) -> Dict[str, Any]:
    """Return a new session dict with session["conversation"] updated for this turn.

    Appends the current turn to the turns list and caps the list at 3 (FIFO).
    Does NOT mutate the input dict.
    """
    prev: Dict[str, Any] = (session or {}).get("conversation") or {}
    turns: List[Dict[str, Any]] = list(prev.get("turns") or [])

    turn: Dict[str, Any] = {
        "user": user_text,
        "intent": intent,
        "search_query": search_query,
    }
    if assistant_text is not None:
        turn["assistant"] = assistant_text
    turns.append(turn)

    if len(turns) > 3:
        turns = turns[-3:]

    updated_conv: Dict[str, Any] = {
        "last_intent": intent,
        "last_search_query": search_query,
        "turns": turns,
    }
    return {**session, "conversation": updated_conv}
