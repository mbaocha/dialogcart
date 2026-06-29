"""
FAQ query resolution — enriches vague queries with session context before calling commerce.

v1 deterministic: no LLM.
Vague triggers: short queries (≤3 words) containing pronouns, bare price phrases,
or tokens that are purely generic without a subject noun.
"""

import re
from typing import Any, Dict, List, Optional

_PRONOUN_RE = re.compile(r"\b(it|that|this|one|those|them)\b", re.IGNORECASE)
_GENERIC_PRICE_RE = re.compile(
    r"^(how much|price|cost|rate|fee|pricing)[\s?]*$", re.IGNORECASE
)
_GENERIC_TOKENS = frozenset(
    {"how much", "cost", "price", "pricing", "rate", "fee", "details", "info", "information"}
)


def _is_vague(query: str) -> bool:
    """True when query is under-specified and may benefit from session context."""
    q = query.strip().lower()
    if not q:
        return True
    # Pronoun references are vague regardless of query length
    if _PRONOUN_RE.search(q):
        return True
    words = q.split()
    if len(words) <= 3:
        if _GENERIC_PRICE_RE.match(q):
            return True
        if set(words).issubset(_GENERIC_TOKENS):
            return True
    return False


def _extract_last_service(session: Dict[str, Any]) -> Optional[str]:
    """Extract the most-recent service name or id from session context."""
    # 1. Current-turn slots
    slots = session.get("slots") or {}
    sid = slots.get("service_id") or slots.get("service")
    if sid and isinstance(sid, str):
        return sid.strip()

    # 2. Persistent booking-kernel slots
    session_slots = session.get("session_slots") or {}
    sid = session_slots.get("service_id") or session_slots.get("service")
    if sid and isinstance(sid, str):
        return sid.strip()

    # 3. Conversation turns — last search_query referencing something non-vague
    conv = session.get("conversation") or {}
    turns: List[Dict[str, Any]] = conv.get("turns") or []
    for turn in reversed(turns):
        sq = turn.get("search_query")
        if sq and isinstance(sq, str) and not _is_vague(sq):
            return sq.strip()

    return None


def resolve_faq_query(
    *,
    search_query: Optional[str],
    user_text: str,
    session: Optional[Dict[str, Any]],
) -> str:
    """
    Return a resolved query string for the commerce FAQ retrieve call.

    Starts from search_query (NLU output) or falls back to user_text.
    If the result looks vague, enriches it using session context.

    Examples:
        search_query="how much", service_id="haircut" → "haircut price"
        prior turn about haircut + "how much is it" → "haircut price"
        search_query="cancellation policy" → "cancellation policy" (passthrough)
    """
    base = (search_query or "").strip() or user_text.strip()

    if not _is_vague(base):
        return base

    service = _extract_last_service(session or {})
    if not service:
        return base

    low = base.lower()
    if any(tok in low for tok in ("how much", "cost", "price", "pricing", "rate", "fee")):
        return f"{service} price"

    # Generic vague — prepend service context
    return f"{service} {base}".strip()
