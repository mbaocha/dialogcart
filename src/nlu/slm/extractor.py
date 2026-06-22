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
            "GENERAL_INQUIRY": "general question not covered by above (policies, hours, location, FAQs)",
        },
    },
    "dialog": {
        "requires_booking_verb": False,
        "search_query": False,
        "intents": {
            "CONFIRM_ACTION": "confirming a proposed action (yes, confirm, ok, sure)",
            "REJECT_ACTION":  "rejecting a proposed action (no, cancel that, don’t)",
        },
    },
    "fallback": {
        "requires_booking_verb": False,
        "search_query": False,
        "intents": {
            "UNKNOWN": "no explicit booking verb present, or truly indeterminate",
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
                        "description": "Booking reference ID if mentioned, else null.",
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


def _system_prompt(now: str, aliases: Dict[str, str]) -> str:
    keys = ", ".join(f'"{k}"' for k in aliases) if aliases else "none provided"
    return f"""You are a booking entity extractor. Your job has TWO independent steps:
STEP 1 — Classify intent. STEP 2 — Extract all entities. Always do BOTH, even for fragmentary input.

Current date/time: {now}

KNOWN SERVICE ALIASES (pick the closest key for service_id): {keys}

════════════════════════════════════════
STEP 1 — INTENT CLASSIFICATION
════════════════════════════════════════
{_format_intent_section()}

- UNKNOWN               — input is ambiguous, fragmentary, or matches none of the above

UNKNOWN examples:
  "haircut tomorrow"          → UNKNOWN  (no booking verb, not a question)
  "from april 12 to april 16" → UNKNOWN  (date range fragment, no verb or question)
  "friday"                    → UNKNOWN  (bare weekday, no context)

════════════════════════════════════════
STEP 2 — ENTITY EXTRACTION (ALWAYS do this, even for UNKNOWN intent)
════════════════════════════════════════

── DATE RULES ──────────────────────────────────────────────────────────────
Named-month dates (no year): month > current → current year; month == current → current year; month < current → next year.
Example (today=2026-01-13): "march 3"→2026-03-03, "jan 10"→2026-01-10, "dec 1"→2026-12-01.
Resolve named-month dates to ISO YYYY-MM-DD. For weekday/relative terms, preserve the user's exact words — do NOT add modifiers: bare "friday" stays "friday" (not "next friday"), "next monday" stays "next monday", "tomorrow" stays "tomorrow".
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
# Cache key = SHA-256(system_prompt + "\n---\n" + text), so any prompt edit
# automatically invalidates all prior entries.
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

    def extract(self, text: str, tenant_context: Dict[str, Any], now: str) -> Dict[str, Any]:
        aliases = tenant_context.get("aliases", {})
        system = _system_prompt(now, aliases)

        key = _cache_key(text, system)
        cached = _cache_get(key)
        if cached is not None:
            logger.debug("Cache hit for text=%r", text)
            return cached

        try:
            response = self._client.messages.create(
                model=_MODEL,
                max_tokens=512,
                system=system,
                tools=[_TOOL],
                tool_choice={"type": "any"},
                messages=[{"role": "user", "content": text}],
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
