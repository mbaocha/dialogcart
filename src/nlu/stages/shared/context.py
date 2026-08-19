"""
Shared conversation context formatter used by both Stage 1 and Stage 2 prompts.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

_MAX_OPTIONS = 8
_MAX_ASK_CHARS = 160
_MAX_OPTION_CHARS = 64

_BOLD_OPTION_RE = re.compile(r"\*\*\s*([^*]+?)\s*\*\*")
_BULLET_OPTION_RE = re.compile(
    r"^\s*(?:[-*]|\d+[.)])\s+(?:\*\*)?(.+?)(?:\*\*)?\s*$"
)
_ASK_LINE_RE = re.compile(
    r"(?i)^\s*(?:which|what|select|choose|pick|do you (?:want|prefer)|would you like)\b"
)


def _clean_option_label(raw: str) -> Optional[str]:
    text = raw.strip()
    text = re.sub(r"^[\s\-*>\d.)]+", "", text)
    text = re.sub(r"\s+", " ", text)
    # Drop trailing price / duration tails: "— £95" or "(30 min)"
    text = re.split(r"\s+[—–-]\s+", text, maxsplit=1)[0].strip()
    text = re.sub(r"\s*\([^)]*\)\s*$", "", text).strip()
    if not text or len(text) < 2:
        return None
    if text.endswith("?"):
        return None
    if len(text) > _MAX_OPTION_CHARS:
        text = text[: _MAX_OPTION_CHARS - 1].rstrip() + "…"
    return text


def _extract_ask(assistant: str) -> Optional[str]:
    lines = [ln.strip() for ln in assistant.replace("\r\n", "\n").split("\n") if ln.strip()]
    questions = [ln for ln in lines if "?" in ln]
    if questions:
        ask = questions[-1]
    else:
        ask = None
        for ln in reversed(lines):
            if _ASK_LINE_RE.match(ln):
                ask = ln
                break
    if not ask:
        return None
    ask = re.sub(r"\s+", " ", ask).strip()
    if len(ask) > _MAX_ASK_CHARS:
        ask = ask[: _MAX_ASK_CHARS - 1].rstrip() + "…"
    return ask


def _extract_options(assistant: str) -> List[str]:
    options: List[str] = []
    seen = set()

    def _add(label: Optional[str]) -> None:
        if not label:
            return
        key = label.casefold()
        if key in seen:
            return
        seen.add(key)
        options.append(label)

    for match in _BOLD_OPTION_RE.finditer(assistant):
        _add(_clean_option_label(match.group(1)))
        if len(options) >= _MAX_OPTIONS:
            return options

    for ln in assistant.replace("\r\n", "\n").split("\n"):
        m = _BULLET_OPTION_RE.match(ln)
        if not m:
            continue
        _add(_clean_option_label(m.group(1)))
        if len(options) >= _MAX_OPTIONS:
            break
    return options


def compact_assistant_move(assistant: Any) -> Optional[Tuple[Optional[str], List[str]]]:
    """Derive (ask, options) from an assistant utterance for compact prompt context."""
    if not isinstance(assistant, str):
        return None
    text = assistant.strip()
    if not text:
        return None
    ask = _extract_ask(text)
    options = _extract_options(text)
    if not ask and not options:
        # Fall back to a short clip so the model still sees the move existed.
        clip = re.sub(r"\s+", " ", text)
        if len(clip) > _MAX_ASK_CHARS:
            clip = clip[: _MAX_ASK_CHARS - 1].rstrip() + "…"
        return clip, []
    return ask, options


def format_conversation_context(ctx: Dict[str, Any]) -> str:
    """Format conversation_context into a prompt block.

    Returns empty string when ctx is empty or has no useful data.
    Includes a compact view of the immediately preceding assistant move
    (ask + offered options) without dumping full assistant transcripts.
    """
    if not ctx:
        return ""
    has_data = (
        ctx.get("last_intent")
        or ctx.get("last_search_query")
        or (ctx.get("turns") or [])
        or ctx.get("active_booking_intent")
        or (ctx.get("pending_assistant_proposals") or [])
        or ctx.get("presented_options")
        or ctx.get("pending_profile_request")
        or ctx.get("confirmation_state")
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
    missing = ctx.get("missing_slots")
    if isinstance(missing, list) and missing:
        lines.append(
            "Missing slots: " + ", ".join(str(item) for item in missing if item)
        )
    resolved_service = ctx.get("resolved_service_id")
    if resolved_service not in (None, ""):
        lines.append(f"Resolved service id: {resolved_service}")
    active_booking = ctx.get("active_booking_intent")
    if active_booking and active_booking != last_intent:
        lines.append(f"Active booking intent (durable session): {active_booking}")

    confirmation_state = ctx.get("confirmation_state")
    if confirmation_state:
        lines.append(f"Canonical confirmation state: {confirmation_state}")

    pending_profile = ctx.get("pending_profile_request")
    if isinstance(pending_profile, str) and pending_profile.strip():
        lines.append(f"Pending profile request: {pending_profile.strip()}")
    elif isinstance(pending_profile, dict):
        pending_kind = pending_profile.get("kind") or pending_profile.get("type")
        if isinstance(pending_kind, str) and pending_kind.strip():
            lines.append(f"Pending profile request: {pending_kind.strip()}")

    presented = ctx.get("presented_options")
    if ctx.get("temporal_context_version") == 1 and isinstance(presented, dict):
        reference = presented.get("reference")
        options = presented.get("options")
        if reference and isinstance(options, list) and options:
            lines.append("")
            lines.append(f"PRESENTED OPTIONS (reference={reference}):")
            for option in options:
                if isinstance(option, dict):
                    lines.append(f"  {option.get('index')}. {option.get('label')}")

    proposals = ctx.get("pending_assistant_proposals") or []
    if isinstance(proposals, list) and proposals:
        lines.append("")
        lines.append("Active assistant proposals:")
        for proposal in proposals:
            if isinstance(proposal, dict):
                lines.append(
                    "  - "
                    f"type={proposal.get('proposal_type')}; "
                    f"entity_type={proposal.get('entity_type')}; "
                    f"slot={proposal.get('slot_key')}; "
                    f"canonical_id={proposal.get('canonical_id')}; "
                    f"display_name={proposal.get('display_name')}; "
                    f"expected_responses={proposal.get('expected_responses')}"
                )

    turns = (ctx.get("turns") or [])[-3:]
    if turns:
        # Immediately preceding assistant move (compact) — last turn with assistant text.
        preceding_found = False
        for t in reversed(turns):
            compact = compact_assistant_move(t.get("assistant"))
            if not compact:
                continue
            ask, options = compact
            lines.append("")
            lines.append("Immediately preceding assistant (non-authoritative language context):")
            if ask:
                lines.append(f"  Asked: {ask}")
            elif options:
                lines.append(
                    "  Asked: (options offered; no explicit question line)"
                )
            if options:
                lines.append(f"  Offered: {'; '.join(options)}")
            preceding_found = True
            break

        if not preceding_found:
            messages = ctx.get("messages") or []
            if isinstance(messages, list):
                for message in reversed(messages):
                    if (
                        not isinstance(message, dict)
                        or message.get("role") != "assistant"
                    ):
                        continue
                    compact = compact_assistant_move(message.get("text"))
                    if not compact:
                        continue
                    ask, options = compact
                    lines.append("")
                    lines.append("Immediately preceding assistant (non-authoritative language context):")
                    if ask:
                        lines.append(f"  Asked: {ask}")
                    elif options:
                        lines.append(
                            "  Asked: (options offered; no explicit question line)"
                        )
                    if options:
                        lines.append(f"  Offered: {'; '.join(options)}")
                    break

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
        "- Structured fields are authoritative for workflow state. Assistant wording may",
        "  resolve references but cannot establish pending input or confirmation eligibility.",
        "- Resolve follow-up references ('it', 'that', 'how long') using prior turns and last_search_query.",
        "- response_act is additive: emit CONFIRM_ACTION or REJECT_ACTION for an",
        "  active assistant proposal even when primary intent describes another act.",
        "- For RAG intents, merge/refine search_query with the prior topic.",
        "- Do NOT invent booking slots (dates, times, services) on the FAQ / OFF_TOPIC",
        "  utterance itself.",
        "- After a FAQ / OFF_TOPIC / unrecognized digression, a later clear slot-shaped",
        "  reply (including clock forms like '1.30') resumes Active booking intent /",
        "  Last booking intent and extracts the slot — do not keep treating the turn as FAQ.",
        "- Conversational answer: when Immediately preceding assistant asked for or offered a",
        "  finite set of values, and the user replies with one of those values (or an unambiguous",
        "  reference such as 'the first one' / 'premium' / 'that one'), treat the reply as",
        "  answering that prompt — not as UNKNOWN and not as a new unrelated FAQ.",
        "- Booking continuation: in-flow replies after a booking intent continue that",
        "  booking intent — including uninterpretable input with no competing act.",
        "- Bare ordinal day revisions ('23rd', '24th', 'show slots for 15th') continue the",
        "  Last date proposal / prior availability month-year — resolve ISO for that day;",
        "  do not drop the date and do not reuse the prior day unchanged.",
    ]
    return "\n".join(lines)
