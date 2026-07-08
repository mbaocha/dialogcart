"""
Stage 2 shared prompt components — DATE RULES, TIME RULES, BOOKING ID RULES.

Each Stage 2 group composes its prompt from the blocks it needs.
Extracted from nlu/slm/extractor.py so each group imports only what's relevant.
"""
from typing import Any, Dict, Optional

from ...registry.intent_groups import ALL_INTENTS, RAG_INTENTS, format_intent_registry


def date_rules(now: str) -> str:
    return f"""── DATE RULES ──────────────────────────────────────────────────────────────
CRITICAL: Only extract dates the user explicitly mentions. Never default to today/current date.
  "book haircut at 10am"  → dates=[], date_time_pairs=[]  (time only — date NOT mentioned)
  "at 3pm"                → dates=[], times=["15:00"]
date_time_pairs: ONLY when BOTH date AND time appear in the same utterance (e.g. "tomorrow at 3pm").
Named-month dates (no year): month > current → current year; month < current → next year.
Current date/time: {now}
Example: "march 3"→2026-03-03, "jan 10"→2026-01-10, "dec 1"→2026-12-01.
Resolve named-month dates to ISO YYYY-MM-DD. For weekday/relative terms, preserve user's exact words
in dates[] — bare "friday" stays "friday", "next monday" stays "next monday", "tomorrow" stays "tomorrow".
EXCEPTION — CORRECTION date replacement: when replacing a date ("saturday instead", "no make it friday"),
resolve the new date to ISO YYYY-MM-DD using current date/time.
For date ranges ("mar 5 through 8", "from may 1 to 10"), output exactly [start_date, end_date] — never enumerate intermediate dates.
Numeric format is DD/MM (day first): "04/03"→day=4,month=3→2026-03-04."""


def time_rules() -> str:
    return """── TIME RULES ──────────────────────────────────────────────────────────────
Exact times → mode=exact, start=HH:MM, end=HH:MM (same value), label=null; also in facts.times.
  ("3pm"→15:00, "9am"→09:00, "noon"→12:00, "midnight"→00:00)
"after X" → mode=exact, start=HH:MM, end="23:59".
"from X" / "by X" → mode=exact, start=end=HH:MM.
Named windows (morning/afternoon/evening/night) → mode=fuzzy, label=<name>, times=[].
No time → time_constraint=null, times=[]. ALWAYS extract times even for UNKNOWN intent."""


def booking_id_rules(tenant_context: Optional[Dict[str, Any]] = None) -> str:
    from ...config.booking_id import DEFAULT_BOOKING_ID_PATTERN, get_booking_id_settings

    _, _, examples = get_booking_id_settings(tenant_context or {})
    raw = (tenant_context or {}).get("booking_id") or {}
    pattern = raw.get("pattern") or DEFAULT_BOOKING_ID_PATTERN

    lines = [
        "── BOOKING ID RULES ────────────────────────────────────────────────────────",
        "Populate facts.booking_id only for clear booking reference tokens — never dates, times, or services.",
        f"Default ID shape (case-insensitive): {pattern}  (e.g. ABC123 — 2+ letters + 3+ digits).",
        "Do NOT set booking_id from vague phrases like \"my booking\" with no reference token.",
        "DO set booking_id when the user gives a standalone ID, an explicit anchor (#ABC123, \"booking id: …\"),",
        '  or "booking/reservation <ID>" when <ID> matches the format.',
        "When unsure, leave booking_id null — the pipeline regex is authoritative.",
    ]
    if examples:
        lines.append(f"Tenant booking ID examples (hints only): {', '.join(examples)}")
    return "\n".join(lines)


def service_rules(aliases: Dict[str, str]) -> str:
    keys = ", ".join(f'"{k}"' for k in aliases) if aliases else "none provided"
    return f"""── SERVICE RULES ───────────────────────────────────────────────────────────
KNOWN SERVICE ALIASES (for reference only): {keys}

Extract the user's service phrase EXACTLY as they said it into facts.service_term.
Do NOT resolve, correct, or match it against the aliases — code handles that.
- User says "book premiun haircut" → service_term = "premiun haircut"  (typo preserved)
- User says "massage"             → service_term = "massage"
- User says "book tomorrow at 3pm" (no service) → service_term = null
service_term must be null when no service is mentioned.
ALWAYS attempt service extraction even for UNKNOWN intent."""


def search_query_rules() -> str:
    rag = ", ".join(sorted(RAG_INTENTS))
    return f"""── SEARCH QUERY RULES ──────────────────────────────────────────────────────
Populate search_query ONLY for: {rag}.
Strip conversational filler ("tell me about", "what are", "do you have") and return the core noun phrase.
Examples:
  "tell me about deep tissue massage"  → "deep tissue massage"
  "how much does a haircut cost"       → "haircut price"
  "what services do you offer"         → "available services"
For all other intents, search_query must be null."""


def intent_validation_section(candidate_intent: str) -> str:
    """Compact intent list for Stage 2 validation / re-routing."""
    return f"""════════════════════════════════════════
INTENT VALIDATION
════════════════════════════════════════
Stage 1 classified this message as: {candidate_intent}

You are the final authority on intent. If after seeing the full slot context
you determine a different intent is correct, set validated_intent accordingly.
If still unclear, keep {candidate_intent}.

Supported intents:
{format_intent_registry()}"""


# ── Shared tool schema components ────────────────────────────────────────────

def _facts_schema(include: list) -> dict:
    """Build facts sub-schema including only the specified field names."""
    all_fields = {
        "dates": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Extracted dates. See DATE RULES.",
        },
        "times": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Extracted clock times HH:MM. See TIME RULES.",
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
        "service_term": {
            "type": ["string", "null"],
            "description": (
                "Raw service phrase the user mentioned, as spoken (typos preserved). "
                "Null if no service was mentioned. Do NOT resolve against aliases."
            ),
        },
        "booking_id": {
            "type": ["string", "null"],
            "description": "Booking reference ID when user states one matching the platform format. Null if absent.",
        },
    }
    props = {k: all_fields[k] for k in include if k in all_fields}
    return {
        "type": "object",
        "properties": props,
        "required": list(props.keys()),
    }


def _time_constraint_schema() -> dict:
    return {
        "type": ["object", "null"],
        "description": "Structured time constraint. null if no time is mentioned.",
        "properties": {
            "mode": {
                "type": "string",
                "enum": ["exact", "fuzzy"],
            },
            "start": {"type": "string", "description": "Start bound HH:MM."},
            "end": {"type": "string", "description": "End bound HH:MM."},
            "label": {
                "type": ["string", "null"],
                "description": "Window name for fuzzy (morning/afternoon/evening/night), null for exact.",
            },
        },
        "required": ["mode", "start", "end", "label"],
    }


def build_tool(
    name: str,
    description: str,
    facts_fields: list,
    include_time_constraint: bool = False,
    include_search_query: bool = False,
    include_validated_intent: bool = True,
    include_service_candidates: bool = False,
    include_operation: bool = False,
) -> dict:
    """Build a Stage 2 tool schema for a specific group."""
    props: dict = {}
    required: list = []

    if include_validated_intent:
        props["validated_intent"] = {
            "type": ["string", "null"],
            "enum": ALL_INTENTS + [None],
            "description": (
                "Override Stage 1's intent if you determine a different one is correct. "
                "null to accept Stage 1's classification."
            ),
        }
        required.append("validated_intent")

    props["confidence"] = {
        "type": "number",
        "description": "Confidence score 0.0–1.0.",
    }
    required.append("confidence")

    if facts_fields:
        props["facts"] = _facts_schema(facts_fields)
        required.append("facts")

    if include_time_constraint:
        props["time_constraint"] = _time_constraint_schema()
        required.append("time_constraint")

    if include_search_query:
        props["search_query"] = {
            "type": ["string", "null"],
            "description": "Normalised search string for RAG lookup. Null for non-RAG intents.",
        }
        required.append("search_query")

    if include_service_candidates:
        props["service_candidates"] = {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Alias keys for the user to choose from. "
                "Set to matching aliases when ambiguous; set to ALL alias keys when no service was mentioned; "
                "empty only when service_id is resolved or user mentioned a service not in the catalog."
            ),
        }
        required.append("service_candidates")

    if include_operation:
        props["operation"] = {
            "type": ["string", "null"],
            "enum": ["browse_next", "browse_previous", None],
            "description": (
                "Set when the user is navigating previously presented availability "
                "(browse_next or browse_previous). Null for new availability searches."
            ),
        }
        required.append("operation")

    return {
        "name": name,
        "description": description,
        "input_schema": {
            "type": "object",
            "properties": props,
            "required": required,
        },
    }
