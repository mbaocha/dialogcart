"""
Shared conversation context formatter used by both Stage 1 and Stage 2 prompts.
"""
from typing import Any, Dict


def format_conversation_context(ctx: Dict[str, Any]) -> str:
    """Format conversation_context into a prompt block.

    Returns empty string when ctx is empty or has no useful data.
    Moved from nlu/slm/extractor.py so both pipeline stages share the same formatter.
    """
    if not ctx:
        return ""
    has_data = (
        ctx.get("last_intent")
        or ctx.get("last_search_query")
        or (ctx.get("turns") or [])
        or ctx.get("active_booking_intent")
    )
    if not has_data:
        return ""

    lines = [
        "════════════════════════════════════════",
        "CONVERSATION CONTEXT",
        "════════════════════════════════════════",
    ]
    last_intent = ctx.get("last_intent")
    last_sq = ctx.get("last_search_query")
    last_dp = ctx.get("last_date_proposal")
    if last_intent:
        lines.append(f"Last intent: {last_intent}")
    if last_sq:
        lines.append(f'Last search query: "{last_sq}"')
    if isinstance(last_dp, dict) and last_dp.get("start"):
        lines.append(f"Last date proposal: {last_dp.get('start')}")
    active_booking = ctx.get("active_booking_intent")
    if active_booking and active_booking != last_intent:
        lines.append(f"Active booking intent (durable session): {active_booking}")

    turns = (ctx.get("turns") or [])[-3:]
    if turns:
        lines.append("")
        lines.append("Prior turns (oldest first):")
        for t in turns:
            lines.append(f"  User: {t.get('user', '')}")
            meta = f"  → intent={t.get('intent', '')}"
            if t.get("search_query"):
                meta += f', search_query="{t["search_query"]}"'
            lines.append(meta)

    lines += [
        "",
        "Context rules:",
        "- Resolve follow-up references ('it', 'that', 'how long') using prior turns and last_search_query.",
        "- For RAG intents, merge/refine search_query with the prior topic.",
        "- Do NOT invent booking slots (dates, times, services) on FAQ detours.",
        "- Slot-fill continuation: bare date/time fragments after a booking intent",
        "  continue that booking intent — not UNKNOWN, not CORRECTION.",
    ]
    return "\n".join(lines)
