"""
Haiku-based slot extractor — Stage 1 of the NLU pipeline.

Replaces luma stages 1-5 (extraction → intent → structure → grouping → semantic).
Uses Claude Haiku tool use to produce a fixed-schema JSON extraction in one call.
"""
import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

import anthropic

logger = logging.getLogger(__name__)

_MODEL = "claude-haiku-4-5-20251001"

INTENT_GROUPS = {
    "booking": {
        "requires_booking_verb": True,
        "search_query": False,
        "intents": {
            "CREATE_APPOINTMENT": "booking a timed service (haircut, massage, trim…)",
            "CREATE_RESERVATION": "booking accommodation/space for a date range (room, suite…)",
            "MODIFY_BOOKING":     "changing an existing booking",
            "CANCEL_BOOKING":     "cancelling an existing booking",
        },
    },
    "booking_query": {
        "requires_booking_verb": False,
        "search_query": False,
        "intents": {
            "BOOKING_INQUIRY": "asking about an existing booking",
            "AVAILABILITY":    "asking what times/slots are free",
            "PAYMENT":         "wants to pay",
            "PAYMENT_STATUS":  "asking about payment status",
        },
    },
    "informational": {
        "requires_booking_verb": False,
        "search_query": True,
        "intents": {
            "DISCOVERY":       "asking what services are offered",
            "DETAILS":         "asking for service details or info",
            "QUOTE":           "asking for price/cost",
            "RECOMMENDATION":  "asking for a recommendation",
            "GENERAL_INQUIRY": "business FAQ not covered above (policies, hours, location, payments, store info — not world knowledge)",
        },
    },
    "out_of_scope": {
        "requires_booking_verb": False,
        "search_query": False,
        "intents": {
            "OFF_TOPIC": "coherent, understood request outside this business's domain (world knowledge, jokes, unrelated topics — not business FAQs)",
        },
    },
    "dialog": {
        "requires_booking_verb": False,
        "search_query": False,
        "intents": {
            "CONFIRM_ACTION": "confirming a proposed action (yes, confirm, ok, sure)",
            "REJECT_ACTION":  "rejecting a proposed action (no, cancel that, don’t)",
            "CORRECTION":     "correcting or replacing a slot in the CURRENT flow — ONLY when conversation context shows an active booking intent (actually X, make it X, change it to X, wait I meant X)",
        },
    },
    "fallback": {
        "requires_booking_verb": False,
        "search_query": False,
        "intents": {
            "UNKNOWN": "utterance not understood — gibberish, fragments, or truly indeterminate (not a coherent off-topic request)",
        },
    },
}

# Derived — never edit these directly
_INTENTS: list = [i for g in INTENT_GROUPS.values() for i in g["intents"]]
_RAG_INTENTS: set = {i for g in INTENT_GROUPS.values() if g["search_query"] for i in g["intents"]}

_TOOL = {
    "name": "extract_booking_facts",
    "description": "Extract intent, facts, and time constraint from a booking-related user message.",
    "input_schema": {
        "type": "object",
        "properties": {
            "intent": {
                "type": "string",
                "enum": _INTENTS,
                "description": "Detected user intent.",
            },
            "confidence": {
                "type": "number",
                "description": "Confidence score 0.0–1.0.",
            },
            "facts": {
                "type": "object",
                "properties": {
                    "dates": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Extracted dates. See DATE RULES in system prompt.",
                    },
                    "times": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Extracted clock times HH:MM. See TIME RULES in system prompt.",
                    },
                    "date_time_pairs": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "date": {"type": "string"},
                                "time": {"type": "string"},
                            },
                            "required": ["date", "time"],
                        },
                        "description": "Date+time pairs when both are explicitly stated together.",
                    },
                    "service_id": {
                        "type": ["string", "null"],
                        "description": "Closest matching key from KNOWN SERVICE ALIASES, or null.",
                    },
                    "booking_id": {
                        "type": ["string", "null"],
                        "description": (
                            "Booking reference ID when the user states one that matches the "
                            "platform ID format (e.g. ABC123). Null if absent or ambiguous."
                        ),
                    },
                },
                "required": ["dates", "times", "date_time_pairs", "service_id", "booking_id"],
            },
            "time_constraint": {
                "type": ["object", "null"],
                "description": "Structured time constraint. null if no time is mentioned.",
                "properties": {
                    "mode": {
                        "type": "string",
                        "enum": ["exact", "fuzzy"],
                        "description": "exact = specific clock time; fuzzy = named time window (morning/evening/etc.)",
                    },
                    "start": {"type": "string", "description": "Start bound HH:MM."},
                    "end": {"type": "string", "description": "End bound HH:MM."},
                    "label": {
                        "type": ["string", "null"],
                        "description": "Window name for fuzzy (morning/afternoon/evening/night), null for exact.",
                    },
                },
                "required": ["mode", "start", "end", "label"],
            },
            "search_query": {
                "type": ["string", "null"],
                "description": (
                    "Normalised search string for RAG lookup. "
                    f"Populated only for {', '.join(sorted(_RAG_INTENTS))} — "
                    "strip conversational filler and return the core noun phrase. "
                    "Null for all other intents."
                ),
            },
        },
        "required": ["intent", "confidence", "facts", "time_constraint", "search_query"],
    },
}


def _format_intent_section() -> str:
    """Generate intent bullet list grouped by verb requirement."""
    verb_groups = {True: [], False: []}
    for group in INTENT_GROUPS.values():
        verb_groups[group["requires_booking_verb"]].extend(
            f"- {intent:<22} — {desc}"
            for intent, desc in group["intents"].items()
            if intent != "UNKNOWN"
        )
    return (
        "Intents that REQUIRE an explicit booking verb "
        "(book, reserve, schedule, cancel, modify, confirm, etc.):\n"
        + "\n".join(verb_groups[True])
        + "\n\nIntents that do NOT require a booking verb:\n"
        + "\n".join(verb_groups[False])
    )


def _format_search_query_intents() -> str:
    return ", ".join(sorted(_RAG_INTENTS))


def _format_conversation_context(ctx: Dict[str, Any]) -> str:
    """Format conversation_context into a prompt block. Returns empty string when ctx is empty or has no useful data."""
    if not ctx:
        return ""
    has_data = (
        ctx.get("last_intent")
        or ctx.get("last_search_query")
        or (ctx.get("turns") or [])
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
            asst = t.get("assistant", "")
            if asst:
                lines.append(f"  Assistant: {asst}")
            meta = f"  → intent={t.get('intent', '')}"
            if t.get("search_query"):
                meta += f', search_query="{t["search_query"]}"'
            lines.append(meta)

    lines += [
        "",
        "Context rules:",
        "- Resolve follow-up references ('it', 'that', 'how long') using prior turns and last_search_query.",
        "- For RAG intents, merge/refine search_query with the prior topic:",
        '  last="cancellation policy" + "and for group bookings?" → "cancellation policy group bookings"',
        '  last="deep tissue massage" + "how long is it?" → "deep tissue massage duration"',
        "- Do NOT invent booking slots (dates, times, services) on FAQ detours.",
        "- Slot-fill continuation (see STEP 1): bare date/time fragments after a booking intent",
        "  continue that booking intent — not UNKNOWN, not CORRECTION.",
    ]
    return "\n".join(lines)


def _format_booking_mode_section(booking_mode: str) -> str:
    if booking_mode == "reservation":
        return """BOOKING MODE: reservation (accommodation / multi-night stays)

Intent disambiguation for reservation mode:
- "book room", "reserve a room/suite", "book accommodation" → CREATE_RESERVATION (never UNKNOWN)
- Timed clock times (3pm, 10am) are irrelevant unless paired with an explicit date range
- Required slots are date_range (check-in → check-out), not single date + time

Examples (booking_mode=reservation):
  "book room"           → CREATE_RESERVATION, service_id=room (if alias exists), dates=[]
  "reserve deluxe"      → CREATE_RESERVATION, service_id=deluxe, dates=[]
  "book room march 5-10"→ CREATE_RESERVATION, dates=[start,end ISO]"""
    return """BOOKING MODE: service (timed appointments)

Intent disambiguation for service mode:
- "book haircut", "schedule massage" → CREATE_APPOINTMENT (never CREATE_RESERVATION)
- Appointments need service + date + time; do not treat as multi-night reservations

Examples (booking_mode=service):
  "book haircut at 10am" → CREATE_APPOINTMENT, dates=[], times=["10:00"] — NO date invented
  "book massage at 3pm"  → CREATE_APPOINTMENT, dates=[], times=["15:00"] — NO date invented
  "book haircut tomorrow at 3pm" → CREATE_APPOINTMENT, dates=["tomorrow"], times=["15:00"]"""


def _format_booking_id_section(tenant_context: Dict[str, Any]) -> str:
    from ..config.booking_id import DEFAULT_BOOKING_ID_PATTERN, get_booking_id_settings

    _, _, examples = get_booking_id_settings(tenant_context)
    raw = (tenant_context or {}).get("booking_id") or {}
    pattern = raw.get("pattern") or DEFAULT_BOOKING_ID_PATTERN

    lines = [
        "── BOOKING ID RULES ────────────────────────────────────────────────────────",
        "Populate facts.booking_id only for clear booking reference tokens — never dates, times, or services.",
        f"Default ID shape (validated in pipeline, case-insensitive): {pattern}  (e.g. ABC123 — 2+ letters + 3+ digits).",
        "Do NOT set booking_id from vague phrases like \"my booking\" with no reference token.",
        "DO set booking_id when the user gives a standalone ID, an explicit anchor (#ABC123, \"booking id: …\", \"ref: …\"),",
        '  or "booking/reservation <ID>" when <ID> matches the format.',
        "When unsure, leave booking_id null — the pipeline regex is authoritative.",
    ]
    if examples:
        ex = ", ".join(examples)
        lines.append(f"Tenant booking ID examples (hints only): {ex}")
    return "\n".join(lines)


def _system_prompt(
    now: str,
    aliases: Dict[str, str],
    booking_mode: str = "service",
    conversation_context: Optional[Dict[str, Any]] = None,
    tenant_context: Optional[Dict[str, Any]] = None,
) -> str:
    keys = ", ".join(f'"{k}"' for k in aliases) if aliases else "none provided"
    ctx_block = _format_conversation_context(conversation_context or {})
    ctx_section = f"\n{ctx_block}\n" if ctx_block else ""
    mode_section = _format_booking_mode_section(booking_mode)
    booking_id_section = _format_booking_id_section(tenant_context or {})
    return f"""You are a booking entity extractor. Your job has TWO independent steps:
STEP 1 — Classify intent. STEP 2 — Extract all entities. Always do BOTH, even for fragmentary input.

Current date/time: {now}
{ctx_section}
{mode_section}

KNOWN SERVICE ALIASES (pick the closest key for service_id): {keys}

════════════════════════════════════════
STEP 1 — INTENT CLASSIFICATION
════════════════════════════════════════
{_format_intent_section()}

- UNKNOWN               — input is ambiguous, fragmentary, or matches none of the above

CORRECTION guidance (ONLY when CONVERSATION CONTEXT shows an active booking intent):
  last_intent=CREATE_APPOINTMENT + "actually a massage" → CORRECTION  (service update)
  last_intent=CREATE_APPOINTMENT + "make it 3pm instead" → CORRECTION  (time update)
  last_intent=CREATE_APPOINTMENT + "change it to a massage tomorrow at 2pm" → CORRECTION
  last_intent=CREATE_APPOINTMENT + "wait, I meant friday" → CORRECTION  (date update)
  last_intent=CREATE_APPOINTMENT + date was friday + "no saturday instead" → CORRECTION, dates=["2026-01-17"]  (saturday after friday)
  last_intent=MODIFY_BOOKING + "actually 5pm" → CORRECTION  (time update)
  last_intent=CANCEL_BOOKING + "wait, it's ABC12345" → CORRECTION  (booking_id update)
  last_intent=MODIFY_BOOKING + "wait, it's ABC12345" → CORRECTION  (booking_id update)
  No prior booking context + booking verb present → use appropriate booking intent (CREATE_APPOINTMENT etc.), not CORRECTION
  No prior booking context + no booking verb → UNKNOWN
  "actually a massage at 3pm" (no prior context) → CREATE_APPOINTMENT, not CORRECTION
  "cancel that" → REJECT_ACTION  (not a slot correction)
  "I want to modify my booking" → MODIFY_BOOKING  (explicit new intent, not a slot correction)
  "change booking" (no prior context) → MODIFY_BOOKING
  "I need to reschedule" (no prior context) → MODIFY_BOOKING

SLOT-FILL continuation (ONLY when CONVERSATION CONTEXT shows Last intent OR Active booking intent
is CREATE_APPOINTMENT, CREATE_RESERVATION, or MODIFY_BOOKING):
  User supplies ONLY missing slot material — bare date, bare time, date range, or date+time — with
  NO new booking/switch verb and NO correction language ("instead", "wait I meant", "actually", "no make it").
  → Return the SAME booking intent from context (NOT UNKNOWN, NOT CORRECTION).
  last_intent=CREATE_APPOINTMENT + "tomorrow"           → CREATE_APPOINTMENT, dates=["tomorrow"]
  last_intent=CREATE_APPOINTMENT + "11am"               → CREATE_APPOINTMENT, times=["11:00"]
  last_intent=CREATE_APPOINTMENT + "tomorrow at 3pm"    → CREATE_APPOINTMENT, dates=["tomorrow"], times=["15:00"]
  last_intent=CREATE_RESERVATION + "march 10 to 15"     → CREATE_RESERVATION, dates=[start,end ISO]
  active_booking_intent=CREATE_APPOINTMENT + "tomorrow at 5pm" (after a QUOTE/FAQ detour) → CREATE_APPOINTMENT
  Do NOT apply when Last intent is QUOTE, GENERAL_INQUIRY, DISCOVERY, DETAILS, or other FAQ/RAG intents.
  Do NOT apply without CONVERSATION CONTEXT (cold start).
  Explicit booking verb in the utterance ("book", "schedule", "reserve") → classify normally, not slot-fill rule.

UNKNOWN examples (NO conversation context / cold start):
  "haircut tomorrow"          → UNKNOWN  (no booking verb, not a question)
  "from april 12 to april 16" → UNKNOWN  (date range fragment, no verb or question)
  "friday"                    → UNKNOWN  (bare weekday, no context)

════════════════════════════════════════
STEP 2 — ENTITY EXTRACTION (ALWAYS do this, even for UNKNOWN intent)
════════════════════════════════════════

── DATE RULES ──────────────────────────────────────────────────────────────
CRITICAL: Only extract dates the user explicitly mentions. Never default to today/current date.
  "book haircut at 10am"  → dates=[], date_time_pairs=[]  (time only — date is NOT mentioned)
  "book massage at 3pm"   → dates=[], date_time_pairs=[]  (same rule)
  "at 3pm"                → dates=[], times=["15:00"]
date_time_pairs: ONLY when BOTH date AND time appear in the same utterance (e.g. "tomorrow at 3pm").
Named-month dates (no year): month > current → current year; month == current → current year; month < current → next year.
Example (today=2026-01-13): "march 3"→2026-03-03, "jan 10"→2026-01-10, "dec 1"→2026-12-01.
Resolve named-month dates to ISO YYYY-MM-DD. For weekday/relative terms on a NEW booking turn, preserve the user's exact words in dates[] — do NOT add modifiers: bare "friday" stays "friday" (not "next friday"), "next monday" stays "next monday", "tomorrow" stays "tomorrow".
EXCEPTION — CORRECTION date replacement: when intent is CORRECTION and the user substitutes a new date/weekday ("saturday instead", "no make it friday", "wait I meant tomorrow"), resolve the NEW date to ISO YYYY-MM-DD using Current date/time (same weekday/named-month rules as above).
For date ranges ("mar 5 through 8", "from may 1 to 10"), output exactly [start_date, end_date] as two ISO dates — never enumerate intermediate dates.
Numeric format is DD/MM (day first): "04/03"→day=4,month=3→2026-03-04.

── TIME RULES ──────────────────────────────────────────────────────────────
Exact times → mode=exact, start=HH:MM, end=HH:MM (same value), label=null; also in facts.times.
  ("3pm"→15:00, "9am"→09:00, "noon"→12:00, "midnight"→00:00)
"after X" → mode=exact, start=HH:MM, end="23:59" (open-ended lower bound).
"from X" / "by X" → mode=exact, start=end=HH:MM.
Named windows (morning/afternoon/evening/night) → mode=fuzzy, label=<name>, times=[].
No time → time_constraint=null, times=[]. ALWAYS extract times even for UNKNOWN intent.

── SERVICE RULES ───────────────────────────────────────────────────────────
Match to nearest key in KNOWN SERVICE ALIASES (case-insensitive, typo-tolerant). Null if no service.
If the user's service term unambiguously maps to one alias key, return that key.
If the user's term is a generic word that matches multiple alias keys equally well (e.g. "massage" when both "swedish massage" and "deep tissue massage" exist), return null — do NOT guess.
ALWAYS extract service even for UNKNOWN intent.

{booking_id_section}

── SEARCH QUERY RULES ──────────────────────────────────────────────────────
Populate search_query ONLY for: {_format_search_query_intents()}.
Strip conversational filler ("tell me about", "what are", "do you have", "can you", "I want to know") and return the core noun phrase the user is asking about.
Examples:
  "tell me about deep tissue massage"   → "deep tissue massage"
  "what are your return policies"       → "return policies"
  "how much does a haircut cost"        → "haircut price"
  "what services do you offer"          → "available services"
  "do you have parking"                 → "parking"
For all other intents, search_query must be null."""


def _validate(result: Dict[str, Any]) -> Dict[str, Any]:
    """Enforce intent-group rules on Haiku output."""
    intent = result.get("intent", "UNKNOWN")
    search_query = result.get("search_query")

    if intent in _RAG_INTENTS:
        if not search_query:
            logger.warning("Haiku returned null search_query for RAG intent=%r", intent)
    else:
        if search_query is not None:
            logger.debug("Nulling spurious search_query for non-RAG intent=%r", intent)
            result = {**result, "search_query": None}

    return result


def _empty() -> Dict[str, Any]:
    return {
        "intent": "UNKNOWN",
        "confidence": 0.0,
        "facts": {
            "dates": [],
            "times": [],
            "date_time_pairs": [],
            "service_id": None,
            "booking_id": None,
        },
        "time_constraint": None,
        "search_query": None,
    }


# ---------------------------------------------------------------------------
# Request cache
# ---------------------------------------------------------------------------
# Enabled by default; set NLU_CACHE=0 to bypass (e.g. when testing prompt changes).
# Cache key = SHA-256(system_prompt + text). system_prompt already embeds the
# formatted conversation_context, so any change to context, prompt, or text
# automatically produces a different key.
# ---------------------------------------------------------------------------

_NLU_CACHE: bool = os.environ.get("NLU_CACHE", "1") != "0"
_CACHE_FILE: Path = Path(__file__).parent.parent / ".haiku_cache.json"
_cache: Optional[Dict[str, Any]] = None


def _get_cache() -> Dict[str, Any]:
    global _cache
    if _cache is None:
        _cache = {}
        if _NLU_CACHE and _CACHE_FILE.exists():
            try:
                _cache = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass
    return _cache


def _cache_get(key: str) -> Optional[Dict[str, Any]]:
    return _get_cache().get(key) if _NLU_CACHE else None


def _cache_put(key: str, value: Dict[str, Any]) -> None:
    if not _NLU_CACHE:
        return
    _get_cache()[key] = value
    try:
        _CACHE_FILE.write_text(
            json.dumps(_get_cache(), indent=2), encoding="utf-8"
        )
    except Exception:
        logger.debug("Cache write failed", exc_info=True)


def _cache_key(text: str, system: str) -> str:
    return hashlib.sha256(f"{system}\n---\n{text}".encode()).hexdigest()


class HaikuExtractor:
    """Single-call Haiku extractor using tool use for reliable structured output."""

    def __init__(self):
        self._client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    def extract(
        self,
        text: str,
        tenant_context: Dict[str, Any],
        now: str,
        conversation_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        aliases = tenant_context.get("aliases", {})
        booking_mode = tenant_context.get("booking_mode", "service")
        system = _system_prompt(
            now,
            aliases,
            booking_mode=booking_mode,
            conversation_context=conversation_context,
            tenant_context=tenant_context,
        )

        # system already contains the formatted context — no need to hash it separately.
        key = _cache_key(text, system)
        cached = _cache_get(key)
        if cached is not None:
            logger.debug("Cache hit for text=%r", text)
            return cached

        ctx_block = _format_conversation_context(conversation_context or {})
        user_content = f"CURRENT USER MESSAGE:\n{text}" if ctx_block else text
        try:
            response = self._client.messages.create(
                model=_MODEL,
                max_tokens=512,
                system=system,
                tools=[_TOOL],
                tool_choice={"type": "any"},
                messages=[{"role": "user", "content": user_content}],
            )
        except Exception:
            logger.exception("Haiku extraction failed for text=%r", text)
            return _empty()

        result = _empty()
        for block in response.content:
            if block.type == "tool_use" and block.name == "extract_booking_facts":
                result = block.input
                break
        else:
            logger.warning("No tool_use block returned by Haiku for text=%r", text)

        validated = _validate(result)
        _cache_put(key, validated)
        return validated
