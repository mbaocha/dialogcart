"""
Conversation memory helpers for NLU context passing.

Stores the last 5 turns under session["conversation"] so that follow-up messages
("and for groups?", "how long is it?") can be resolved by NLU without the
orchestrator needing to touch the booking kernel.

Also maintains session["messages"] — a simple [{role, text}] list capped at 5
turn-pairs (10 entries) — written every turn for both durable and non-durable
intents, enabling cross-turn RAG context regardless of intent type.

Schema (session["conversation"]):
    {
        "last_intent":      str | None,   # intent from the previous turn
        "last_search_query": str | None,  # search_query from the previous turn
        "turns": [                        # prior turns, max 5, FIFO
            {
                "user":         str,
                "intent":       str,
                "search_query": str | None,
                "assistant":    str,       # optional
            }
        ]
    }

Schema (session["messages"]):
    [{"role": "user" | "assistant", "text": str}, ...]  # max 10 entries (5 turns)
"""
from typing import Any, Dict, List, Optional


def build_conversation_context(
    session: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Return the NLU conversation_context dict from session, or None.

    Merges session["conversation"] (structured turn data) with session["messages"]
    (flat role/text list). Returns None when session carries no useful data.
    """
    if not session or not isinstance(session, dict):
        return None
    conv = session.get("conversation")
    messages = session.get("messages")

    has_conv = (
        isinstance(conv, dict)
        and (conv.get("last_intent") or conv.get("last_search_query") or conv.get("turns"))
    )
    has_messages = isinstance(messages, list) and len(messages) > 0

    if not has_conv and not has_messages:
        return None

    result: Dict[str, Any] = dict(conv) if has_conv else {}
    if has_messages:
        result["messages"] = messages
    return result


def update_conversation(
    session: Dict[str, Any],
    *,
    user_text: str,
    intent: str,
    search_query: Optional[str],
    assistant_text: Optional[str] = None,
) -> Dict[str, Any]:
    """Return a new session dict with session["conversation"] updated for this turn.

    Appends the current turn to the turns list and caps the list at 5 (FIFO).
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

    if len(turns) > 5:
        turns = turns[-5:]

    updated_conv: Dict[str, Any] = {
        "last_intent": intent,
        "last_search_query": search_query,
        "turns": turns,
    }
    return {**session, "conversation": updated_conv}


def append_messages_turn(
    session: Dict[str, Any],
    user_text: str,
    assistant_text: Optional[str],
    max_turns: int = 5,
) -> Dict[str, Any]:
    """Return a new session dict with session["messages"] updated for this turn.

    Appends {role, text} entries for the user and (when present) assistant.
    Caps the list at max_turns * 2 entries (FIFO). Does NOT mutate the input.
    """
    messages: List[Dict[str, str]] = list((session or {}).get("messages") or [])
    messages.append({"role": "user", "text": user_text})
    if assistant_text:
        messages.append({"role": "assistant", "text": assistant_text})
    max_entries = max_turns * 2
    if len(messages) > max_entries:
        messages = messages[-max_entries:]
    return {**session, "messages": messages}
